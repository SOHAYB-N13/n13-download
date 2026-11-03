package com.sohayb.n13download.ui.screens.batch

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream

/**
 * Reads a saved URL list the user picked with the system file picker.
 *
 * The read is bounded: a link list is a few kilobytes, and a user who accidentally
 * picks a 2 GB video must not OOM the app before the extension check even runs.
 */
object BatchFileReader {

    /** 8 MB is far beyond any real link list, and still safe to hold as text. */
    private const val MAX_BYTES = 8 * 1024 * 1024

    data class Loaded(val name: String, val text: String)

    suspend fun read(context: Context, uri: Uri): Loaded? = withContext(Dispatchers.IO) {
        runCatching {
            val name = displayName(context, uri)
            val text = context.contentResolver.openInputStream(uri)?.use { input ->
                val out = ByteArrayOutputStream()
                val buffer = ByteArray(16 * 1024)
                var total = 0
                while (true) {
                    val read = input.read(buffer)
                    if (read <= 0) break
                    val keep = minOf(read, MAX_BYTES - total)
                    if (keep <= 0) break
                    out.write(buffer, 0, keep)
                    total += keep
                    if (total >= MAX_BYTES) break
                }
                out.toString(Charsets.UTF_8.name())
            }
            if (text == null) null else Loaded(name, text)
        }.getOrNull()
    }

    private fun displayName(context: Context, uri: Uri): String {
        val fromProvider = runCatching {
            context.contentResolver.query(uri, null, null, null, null)?.use { cursor ->
                val index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (index >= 0 && cursor.moveToFirst()) cursor.getString(index) else null
            }
        }.getOrNull()
        return fromProvider?.takeIf { it.isNotBlank() }
            ?: uri.lastPathSegment?.substringAfterLast('/')
            ?: "links.txt"
    }
}
