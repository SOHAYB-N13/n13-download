package com.sohayb.n13download.core

import java.net.InetAddress
import java.net.URI
import java.net.URISyntaxException
import java.net.UnknownHostException

/**
 * URL security checks (SSRF prevention).
 *
 * Ported from `core/security.py`. Blocks private / loopback / link-local /
 * reserved addresses, dangerous hostnames, and anything that is not HTTP(S).
 *
 * [resolveHost] performs a **blocking DNS lookup** and must therefore only ever
 * be called from a background dispatcher.
 */
object UrlSecurity {

    private val BLOCKED_HOSTNAMES = setOf(
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "broadcasthost",
        "metadata.google.internal",
        "169.254.169.254",
    )

    private val ALLOWED_SCHEMES = setOf("http", "https")

    data class Result(val ok: Boolean, val reason: String = "") {
        companion object {
            val OK = Result(true)
        }
    }

    /**
     * @param blockPrivate when true (the N13 default) the host is resolved and
     *   every returned address must be public.
     */
    fun validate(url: String, blockPrivate: Boolean = true): Result {
        val uri = try {
            URI(url.trim())
        } catch (_: URISyntaxException) {
            return Result(false, "Invalid URL")
        }

        val scheme = uri.scheme?.lowercase()
        if (scheme == null || scheme !in ALLOWED_SCHEMES) {
            return Result(false, "URL must use http or https")
        }
        if (uri.host.isNullOrBlank()) return Result(false, "URL missing host")

        // user:pass@host is a phishing obfuscation vector; N13 supports auth via settings.
        if (uri.userInfo != null) {
            return Result(false, "Embedded URL credentials are not allowed")
        }

        if (!blockPrivate) return Result.OK
        return resolveHost(uri.host!!)
    }

    /** Rejects private/sensitive addresses and unresolvable hostnames. */
    fun resolveHost(hostname: String): Result {
        val host = hostname.trim().lowercase().removeSuffix(".").removeSurrounding("[", "]")
        if (host in BLOCKED_HOSTNAMES) return Result(false, "Blocked hostname")

        parseIpLiteral(host)?.let { address ->
            return if (isBlocked(address)) Result(false, "Blocked IP address") else Result.OK
        }

        val addresses = try {
            InetAddress.getAllByName(host)
        } catch (_: UnknownHostException) {
            return Result(false, "Cannot resolve hostname")
        }
        if (addresses.isEmpty()) return Result(false, "Hostname did not resolve to any address")

        for (address in addresses) {
            if (isBlocked(address)) {
                return Result(false, "Hostname resolves to blocked address: ${address.hostAddress}")
            }
        }
        return Result.OK
    }

    private fun parseIpLiteral(host: String): InetAddress? {
        // Only accept literals, never trigger a DNS lookup here.
        val looksV4 = host.isNotEmpty() && host.all { it.isDigit() || it == '.' }
        val looksV6 = host.contains(':')
        if (!looksV4 && !looksV6) return null
        return try {
            InetAddress.getByName(host)
        } catch (_: UnknownHostException) {
            null
        }
    }

    private fun isBlocked(address: InetAddress): Boolean =
        address.isAnyLocalAddress ||
            address.isLoopbackAddress ||
            address.isLinkLocalAddress ||
            address.isSiteLocalAddress ||
            address.isMulticastAddress ||
            isUniqueLocalV6(address) ||
            isCarrierGradeNat(address)

    private fun isUniqueLocalV6(address: InetAddress): Boolean {
        val bytes = address.address
        if (bytes.size != 16) return false
        return (bytes[0].toInt() and 0xFE) == 0xFC
    }

    private fun isCarrierGradeNat(address: InetAddress): Boolean {
        val bytes = address.address
        if (bytes.size != 4) return false
        val first = bytes[0].toInt() and 0xFF
        val second = bytes[1].toInt() and 0xFF
        return first == 100 && second in 64..127
    }
}
