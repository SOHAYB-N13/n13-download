package com.sohayb.n13download.data.engine

import android.util.Log
import com.sohayb.n13download.domain.model.DownloadSettings
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager
import okhttp3.ConnectionPool
import okhttp3.OkHttpClient

/**
 * Shared OkHttp clients.
 *
 * Two of them, mirroring N13's split between the probe transport and the
 * transfer transport:
 *  - [probe]: short timeouts so a dead-but-accepting server fails fast instead
 *    of multiplying a read timeout into a multi-minute stall.
 *  - [transfer]: generous timeouts for the actual byte stream.
 *
 * `retryOnConnectionFailure` is off because retries are handled explicitly with
 * N13's own classification and backoff — letting OkHttp retry underneath would
 * silently multiply the attempt budget.
 */
object OkHttpClients {

    private const val TAG = "N13Http"

    private const val PROBE_CONNECT_TIMEOUT = 4L
    private const val PROBE_READ_TIMEOUT = 8L
    private const val TRANSFER_CONNECT_TIMEOUT = 30L

    /**
     * How long a transfer may go without receiving anything before the socket is
     * declared dead.
     *
     * Deliberately not generous: a kernel-blocked read is not reliably woken by
     * closing the socket, so this value is also the upper bound on how long a pause
     * can take to be observed on a stalled connection.  Thirty seconds of total
     * silence is already far beyond any healthy server's inter-chunk gap.
     */
    private const val TRANSFER_READ_TIMEOUT = 30L

    private val permissiveSslContext: SSLContext by lazy {
        val trustAll = arrayOf<TrustManager>(object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
            override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        })
        SSLContext.getInstance("TLS").apply { init(null, trustAll, java.security.SecureRandom()) }
    }

    private var probeClient: OkHttpClient? = null
    private var transferClient: OkHttpClient? = null
    private var insecureTransferClient: OkHttpClient? = null
    private var insecureProbeClient: OkHttpClient? = null

    @Synchronized
    fun probe(settings: DownloadSettings): OkHttpClient {
        val insecure = !settings.verifySsl
        val cached = if (insecure) insecureProbeClient else probeClient
        if (cached != null) return cached

        val client = baseBuilder(settings)
            .connectTimeout(PROBE_CONNECT_TIMEOUT, TimeUnit.SECONDS)
            .readTimeout(PROBE_READ_TIMEOUT, TimeUnit.SECONDS)
            .build()

        if (insecure) {
            Log.w(TAG, "SSL verification disabled for probe transport (user setting)")
            insecureProbeClient = client
        } else {
            probeClient = client
        }
        return client
    }

    @Synchronized
    fun transfer(settings: DownloadSettings): OkHttpClient {
        val insecure = !settings.verifySsl
        val cached = if (insecure) insecureTransferClient else transferClient
        if (cached != null) return cached

        val builder = baseBuilder(settings)
            .connectTimeout(TRANSFER_CONNECT_TIMEOUT, TimeUnit.SECONDS)
            .readTimeout(TRANSFER_READ_TIMEOUT, TimeUnit.SECONDS)
            .writeTimeout(TRANSFER_CONNECT_TIMEOUT, TimeUnit.SECONDS)
            // Per-host pool sized for multi-connection downloads.
            .connectionPool(ConnectionPool(MAX_IDLE_CONNECTIONS, 5, TimeUnit.MINUTES))

        if (insecure) {
            Log.w(TAG, "SSL verification disabled for transfer transport (user setting)")
            val trustAll = arrayOf<TrustManager>(object : X509TrustManager {
                override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
                override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
                override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
            })
            builder.sslSocketFactory(permissiveSslContext.socketFactory, trustAll[0] as X509TrustManager)
            builder.hostnameVerifier { _, _ -> true }
            insecureTransferClient = builder.build()
        } else {
            transferClient = builder.build()
        }
        return if (insecure) insecureTransferClient!! else transferClient!!
    }

    private fun baseBuilder(settings: DownloadSettings): OkHttpClient.Builder =
        OkHttpClient.Builder()
            .followRedirects(true)
            .followSslRedirects(true)
            // We retry ourselves, with N13's classification and backoff.
            .retryOnConnectionFailure(false)
            .cache(null)

    @Synchronized
    fun shutdown() {
        listOfNotNull(probeClient, transferClient, insecureProbeClient, insecureTransferClient)
            .forEach { client ->
                runCatching {
                    client.dispatcher.executorService.shutdown()
                    client.connectionPool.evictAll()
                }
            }
        probeClient = null
        transferClient = null
        insecureProbeClient = null
        insecureTransferClient = null
    }

    private const val MAX_IDLE_CONNECTIONS = 16
}
