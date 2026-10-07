package com.sohayb.n13download

import java.io.BufferedOutputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread

/**
 * A minimal HTTP/1.1 server that runs inside the test process.
 *
 * The Redmi 8 is used on a network where the usual public test hosts are
 * unreachable, and a download engine must be verifiable regardless of that.  This
 * server implements exactly the parts of HTTP the engine depends on — HEAD,
 * `Accept-Ranges`, `Range: bytes=a-b` with 206 and `Content-Range`, and
 * `Content-Disposition` — so the probe, the segmented writer, resume and the
 * merge step can all be exercised for real over loopback.
 *
 * It is a real server speaking real HTTP over a real socket; nothing is stubbed
 * out on the client side.
 */
class LocalHttpServer(
    private val body: ByteArray,
    private val supportsRange: Boolean = true,
    private val contentType: String = "application/octet-stream",
    private val filename: String? = null,
    /** 0 = as fast as the socket allows; otherwise a deliberate throttle. */
    private val bytesPerSecond: Int = 0,
    /** Fixed port, so a server can be stopped and brought back on the same URL. */
    port: Int = 0,
) {

    private val server = ServerSocket(port)
    private val running = AtomicBoolean(false)
    private var acceptThread: Thread? = null

    /**
     * Every socket handed out by `accept()`.
     *
     * Tracked so [stop] can close them: a handler thread can still be parked in
     * `writeBody` after the client walked away, and leaking those sockets across a
     * test class eventually starves the process of descriptors.
     */
    private val openSockets = java.util.concurrent.CopyOnWriteArrayList<Socket>()

    /** Number of requests served, so a test can prove a resume reused its parts. */
    @Volatile
    var requestCount: Int = 0
        private set

    @Volatile
    var rangedRequestCount: Int = 0
        private set

    val port: Int get() = server.localPort
    val baseUrl: String get() = "http://127.0.0.1:$port"

    fun url(path: String = "/file.bin"): String = baseUrl + path

    fun start() {
        running.set(true)
        acceptThread = thread(isDaemon = true, name = "n13-test-http") {
            while (running.get()) {
                val socket = try {
                    server.accept()
                } catch (_: IOException) {
                    break
                }
                openSockets.add(socket)
                thread(isDaemon = true) { handle(socket) }
            }
        }
    }

    fun stop() {
        running.set(false)
        runCatching { server.close() }
        // Close every accepted socket too, or a handler parked in a write would
        // keep the descriptor (and its thread) alive past the test.
        openSockets.forEach { runCatching { it.close() } }
        openSockets.clear()
        acceptThread?.interrupt()
    }

    // ------------------------------------------------------------------ //

    private fun handle(socket: Socket) {
        try {
            socket.use { client ->
                val input = client.getInputStream()
                val output = BufferedOutputStream(client.getOutputStream(), 32 * 1024)

                val requestLine = readLine(input) ?: return
                val headers = readHeaders(input)

                val parts = requestLine.split(' ')
                if (parts.size < 2) return
                val method = parts[0].uppercase()
                val rangeHeader = headers["range"]

                requestCount++

                if (rangeHeader != null && supportsRange) rangedRequestCount++

                val range = if (supportsRange && rangeHeader != null) parseRange(rangeHeader) else null

                android.util.Log.d(
                    "N13TestServer",
                    "port=$port $method range=$rangeHeader parsed=$range",
                )

                when {
                    method == "HEAD" -> writeHead(output, 200, 0L, body.size - 1L)
                    range != null -> {
                        val (start, end) = range
                        writeHead(output, 206, start, end, totalOverride = body.size)
                        writeBody(output, start, end)
                    }

                    else -> {
                        writeHead(output, 200, 0L, body.size - 1L)
                        writeBody(output, 0L, body.size - 1L)
                    }
                }
                output.flush()
            }
        } catch (failure: Throwable) {
            android.util.Log.w("N13TestServer", "port=$port handler failed: $failure")
        }
    }

    private fun writeHead(
        output: OutputStream,
        status: Int,
        rangeStart: Long,
        rangeEnd: Long,
        totalOverride: Int = -1,
    ) {
        val total = if (totalOverride >= 0) totalOverride else body.size
        val length = rangeEnd - rangeStart + 1

        val head = buildString {
            append("HTTP/1.1 $status ${if (status == 206) "Partial Content" else "OK"}\r\n")
            append("Content-Type: $contentType\r\n")
            append("Content-Length: $length\r\n")
            if (filename != null) {
                append("Content-Disposition: attachment; filename=\"$filename\"\r\n")
            }
            if (supportsRange) {
                append("Accept-Ranges: bytes\r\n")
                if (status == 206) {
                    append("Content-Range: bytes $rangeStart-$rangeEnd/$total\r\n")
                }
            }
            append("Connection: close\r\n")
            append("\r\n")
        }
        output.write(head.toByteArray(Charsets.ISO_8859_1))
        // Headers go out immediately: a throttled body must not delay them.
        output.flush()
    }

    private fun writeBody(output: OutputStream, start: Long, end: Long) {
        val chunk = ByteArray(16 * 1024)
        var position = start
        while (position <= end) {
            val size = minOf(chunk.size.toLong(), end - position + 1).toInt()
            System.arraycopy(body, position.toInt(), chunk, 0, size)
            output.write(chunk, 0, size)
            position += size
            if (bytesPerSecond > 0) {
                val sleepMillis = (size * 1000L) / bytesPerSecond
                if (sleepMillis > 0) Thread.sleep(sleepMillis)
            }
        }
    }

    private fun parseRange(value: String): Pair<Long, Long>? {
        // Only the "bytes=start-end" form the engine actually emits.
        val spec = value.removePrefix("bytes=").trim()
        val dash = spec.indexOf('-')
        if (dash <= 0) return null
        val start = spec.substring(0, dash).toLongOrNull() ?: return null
        val end = spec.substring(dash + 1).toLongOrNull() ?: (body.size - 1L)
        if (start > end || start >= body.size) return null
        return start to minOf(end, body.size - 1L)
    }

    private fun readLine(input: InputStream): String? {
        val builder = StringBuilder()
        while (true) {
            val byte = input.read()
            if (byte == -1) return builder.toString().ifEmpty { null }
            if (byte == '\n'.code) return builder.toString().trimEnd('\r')
            builder.append(byte.toChar())
        }
    }

    private fun readHeaders(input: InputStream): Map<String, String> {
        val headers = mutableMapOf<String, String>()
        while (true) {
            val line = readLine(input) ?: break
            if (line.isEmpty()) break
            val colon = line.indexOf(':')
            if (colon > 0) {
                headers[line.substring(0, colon).trim().lowercase()] =
                    line.substring(colon + 1).trim()
            }
        }
        return headers
    }
}
