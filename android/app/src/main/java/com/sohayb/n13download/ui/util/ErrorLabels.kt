package com.sohayb.n13download.ui.util

import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource
import com.sohayb.n13download.R

/**
 * Translates an error message for display.
 *
 * Error text is **stored** on the task (and written by the engine in the language
 * that was active when the transfer ran), so it cannot simply be looked up as a
 * resource at write time.  Instead the known messages are matched here and
 * swapped for the current language's wording; anything unrecognised — including
 * the engine's own detailed diagnostics — is shown verbatim rather than hidden.
 *
 * This keeps stored data language-neutral in effect: a download that failed while
 * the app was in English reads in Persian the moment the language changes, and
 * nothing has to be migrated.
 */
@Composable
fun errorLabel(raw: String): String {
    val trimmed = raw.trim()
    if (trimmed.isEmpty()) return raw

    EXACT[trimmed]?.let { return stringResource(it) }

    // Messages that embed an HTTP status code.
    AUTH.find(trimmed)?.let { return stringResource(R.string.err_auth, it.groupValues[1]) }
    SERVER_ERROR.find(trimmed)?.let {
        return stringResource(R.string.err_server_error, it.groupValues[1])
    }
    REFUSED.find(trimmed)?.let {
        return stringResource(R.string.err_refused, it.groupValues[1])
    }
    if (trimmed == NOT_FOUND) return stringResource(R.string.err_not_found)

    // Unknown message (often the engine's own detailed diagnostic): show as-is
    // rather than hiding information the user may need.
    return raw
}

/** Exact matches, keyed by the message the engine writes. */
private val EXACT: Map<String, Int> = mapOf(
    "Cancelled" to R.string.err_cancelled,
    "Not enough space on the device - free some space and try again" to R.string.err_not_enough_space,
    "Cannot write to the destination folder" to R.string.err_destination_unavailable,
    "File name is too long" to R.string.err_file_name_too_long,
    "Enter a valid http:// or https:// link" to R.string.err_invalid_url,
    "That file name is not valid" to R.string.err_invalid_filename,
    "The server does not support resume - this download has to restart from the beginning" to R.string.err_resume_unsupported,
    "Server returned an HTML page instead of the file " +
        "(the link may be expired, region-blocked, or require a browser session)." to R.string.err_html_interstitial,
    "Checksum mismatch - the downloaded file is corrupted" to R.string.err_checksum_mismatch,
    "The download stopped before it finished" to R.string.err_incomplete,
    "Connection timed out - the server did not respond" to R.string.err_timeout,
    "Cannot reach the server - check the connection" to R.string.err_unreachable,
    "Network connection lost - will retry automatically" to R.string.err_network_retrying,
    "SSL certificate verification failed" to R.string.err_ssl,
    "The server closed the connection unexpectedly" to R.string.err_stream_reset,
    "The download was interrupted" to R.string.err_interrupted,
    "Network connection was lost" to R.string.err_connection_lost,
    "Server is busy - will retry automatically" to R.string.err_busy,
    "The server rejected the resume position" to R.string.err_range_rejected,
    "Plain HTTP is blocked on this device for that host" to R.string.err_cleartext,
    "The server closed the connection early" to R.string.err_closed_early,
    "Could not write the file to the destination" to R.string.err_write_failed,
    "Download failed" to R.string.err_failed,
)

/** Messages that carry an HTTP status code. */
private val AUTH =
    Regex("""^Authentication required - the server rejected the request \((\d{3})\)$""")

private val SERVER_ERROR =
    Regex("""^Server error \(HTTP (\d{3})\) - will retry automatically$""")

private val REFUSED =
    Regex("""^The server refused the request \(HTTP (\d{3})\)$""")

private const val NOT_FOUND = "Server returned 404 - the file was not found"
