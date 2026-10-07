package com.sohayb.n13download.core

import android.system.ErrnoException
import android.system.OsConstants
import java.io.FileNotFoundException
import java.io.IOException
import java.io.InterruptedIOException
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLException
import okhttp3.internal.http2.StreamResetException
import okio.IOException as OkioIOException

/**
 * Converts engine exceptions into the short, actionable sentences N13 shows the
 * user, and never leaks a stack trace or a URL with credentials.
 *
 * Ported from `core/errors.py`, with the OkHttp/`java.net` exception families
 * substituted for the `requests` ones.
 */
object N13Errors {

    const val CANCELLED = "Cancelled"
    const val NOT_ENOUGH_SPACE = "Not enough space on the device - free some space and try again"
    const val DESTINATION_UNAVAILABLE = "Cannot write to the destination folder"
    const val FILE_NAME_TOO_LONG = "File name is too long"
    const val INVALID_URL = "Enter a valid http:// or https:// link"
    const val RESUME_UNSUPPORTED =
        "The server does not support resume - this download has to restart from the beginning"
    const val HTML_INTERSTITIAL =
        "Server returned an HTML page instead of the file " +
            "(the link may be expired, region-blocked, or require a browser session)."
    const val CHECKSUM_MISMATCH = "Checksum mismatch - the downloaded file is corrupted"
    const val INCOMPLETE = "The download stopped before it finished"

    /** Maps an exception (plus optional HTTP status) to a user-facing message. */
    fun friendly(throwable: Throwable, status: Int? = null): String {
        when (throwable) {
            is SocketTimeoutException -> return "Connection timed out - the server did not respond"
            is UnknownHostException -> return "Cannot reach the server - check the connection"
            is ConnectException, is NoRouteToHostException ->
                return "Network connection lost - will retry automatically"
            is SSLException -> return "SSL certificate verification failed"
            is StreamResetException -> return "The server closed the connection unexpectedly"
            is InterruptedIOException -> return "The download was interrupted"
        }

        if (throwable is SocketException) {
            return "Network connection was lost"
        }

        status?.let { code ->
            when {
                code == 401 || code == 403 ->
                    return "Authentication required - the server rejected the request ($code)"
                code == 404 -> return "Server returned 404 - the file was not found"
                code == 408 || code == 429 -> return "Server is busy - will retry automatically"
                code == 416 -> return "The server rejected the resume position"
                code >= 500 -> return "Server error (HTTP $code) - will retry automatically"
                code >= 400 -> return "The server refused the request (HTTP $code)"
            }
        }

        // Disk and permission problems surface as IOException with an errno.
        val errno = errnoOf(throwable)
        if (errno != null) {
            when (errno) {
                OsConstants.ENOSPC -> return NOT_ENOUGH_SPACE
                OsConstants.EACCES, OsConstants.EPERM -> return DESTINATION_UNAVAILABLE
                OsConstants.ENAMETOOLONG -> return FILE_NAME_TOO_LONG
                OsConstants.ECONNRESET, OsConstants.ECONNABORTED, OsConstants.EPIPE ->
                    return "Network connection was lost"
                OsConstants.ETIMEDOUT, OsConstants.EHOSTUNREACH, OsConstants.ENETUNREACH ->
                    return "Network connection lost - will retry automatically"
            }
        }

        if (throwable is FileNotFoundException) return DESTINATION_UNAVAILABLE
        if (throwable is OkioIOException || throwable is IOException) {
            val message = throwable.message.orEmpty()
            when {
                message.contains("ENOSPC", ignoreCase = true) ||
                    message.contains("No space left", ignoreCase = true) -> return NOT_ENOUGH_SPACE

                message.contains("ENAMETOOLONG", ignoreCase = true) -> return FILE_NAME_TOO_LONG

                message.contains("EACCES", ignoreCase = true) ||
                    message.contains("Permission denied", ignoreCase = true) -> return DESTINATION_UNAVAILABLE

                message.contains("ECONNRESET", ignoreCase = true) ||
                    message.contains("Connection reset", ignoreCase = true) ->
                    return "Network connection was lost"

                message.contains("CLEARTEXT", ignoreCase = true) ->
                    return "Plain HTTP is blocked on this device for that host"

                message.contains("unexpected end of stream", ignoreCase = true) ||
                    message.contains("Socket closed", ignoreCase = true) ->
                    return "The server closed the connection early"

                // The engine's own diagnostics ("Incomplete download: x/y bytes")
                // say far more than a generic storage message, so they are kept.
                message.isNotBlank() -> return message
            }
            return "Could not write the file to the destination"
        }

        return throwable.message?.takeIf { it.isNotBlank() } ?: "Download failed"
    }

    private fun errnoOf(throwable: Throwable): Int? {
        var current: Throwable? = throwable
        var depth = 0
        while (current != null && depth < 6) {
            if (current is ErrnoException) return current.errno
            current = current.cause
            depth++
        }
        return null
    }
}
