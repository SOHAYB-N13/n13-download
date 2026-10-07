package com.sohayb.n13download.data.storage

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.annotation.RequiresApi
import com.sohayb.n13download.domain.download.DownloadDestination
import com.sohayb.n13download.domain.model.DestinationKind
import java.io.BufferedOutputStream
import java.io.IOException
import java.io.OutputStream

/**
 * Publishes into the public **Downloads** collection through MediaStore.
 *
 * This is the correct way to write a user-visible download on Android 10+:
 * no storage permission is needed for the app's own inserts, and other apps can
 * still see the file.
 *
 * The entry is created with `IS_PENDING = 1` so it is invisible until the bytes
 * are complete, then cleared in [finalize].  That gives the same atomicity as the
 * file-based destinations without needing a rename.
 */
@RequiresApi(Build.VERSION_CODES.Q)
class MediaStoreDestination(
    private val context: Context,
    private val folder: String,
) : DownloadDestination {

    override val kind: DestinationKind = DestinationKind.MEDIA_STORE

    private val safeFolder: String = SafePath.safeFolder(folder)

    /** Relative path must be `Downloads/<sub>/` with a trailing slash. */
    private val relativePath: String
        get() = buildString {
            append(Environment.DIRECTORY_DOWNLOADS)
            append('/')
            if (safeFolder.isNotEmpty()) {
                append(safeFolder)
                append('/')
            }
        }

    override val displayPath: String
        get() = if (safeFolder.isEmpty()) "Downloads" else "Downloads/$safeFolder"

    override val reference: String get() = safeFolder

    /** URIs of entries this instance created and has not finalised yet. */
    private val pending = mutableMapOf<String, Uri>()

    private fun collection(): Uri =
        MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)

    override suspend fun isAvailable(): Boolean = true

    override suspend fun listNames(): Set<String> {
        val names = mutableSetOf<String>()
        val projection = arrayOf(MediaStore.MediaColumns.DISPLAY_NAME)
        // Only completed entries count: our own in-flight files must not look
        // like duplicates to the user.
        val selection = "${MediaStore.MediaColumns.RELATIVE_PATH} LIKE ? AND " +
            "${MediaStore.MediaColumns.IS_PENDING} = 0"
        val args = arrayOf("%$relativePath%")

        runCatching {
            context.contentResolver.query(collection(), projection, selection, args, null)
                ?.use { cursor ->
                    val column = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.DISPLAY_NAME)
                    while (cursor.moveToNext()) {
                        cursor.getString(column)?.let { names.add(it) }
                    }
                }
        }
        return names
    }

    override suspend fun exists(name: String): Boolean {
        SafePath.validate(name) ?: return false
        val projection = arrayOf(MediaStore.MediaColumns._ID)
        val selection = "${MediaStore.MediaColumns.DISPLAY_NAME} = ? AND " +
            "${MediaStore.MediaColumns.RELATIVE_PATH} LIKE ? AND " +
            "${MediaStore.MediaColumns.IS_PENDING} = 0"
        val args = arrayOf(name, "%$relativePath%")
        return runCatching {
            context.contentResolver.query(collection(), projection, selection, args, null)
                ?.use { it.count > 0 } ?: false
        }.getOrDefault(false)
    }

    override suspend fun delete(name: String): Boolean {
        SafePath.validate(name) ?: return false
        val selection = "${MediaStore.MediaColumns.DISPLAY_NAME} = ? AND " +
            "${MediaStore.MediaColumns.RELATIVE_PATH} LIKE ?"
        val args = arrayOf(name, "%$relativePath%")
        return runCatching {
            context.contentResolver.delete(collection(), selection, args) > 0
        }.getOrDefault(false)
    }

    override suspend fun openOutput(name: String): OutputStream {
        SafePath.requireSafe(name)
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, name)
            put(MediaStore.MediaColumns.RELATIVE_PATH, relativePath)
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val uri = context.contentResolver.insert(collection(), values)
            ?: throw IOException("Could not create the file in Downloads")
        pending[name] = uri
        return context.contentResolver.openOutputStream(uri)
            ?.let { BufferedOutputStream(it, BUFFER_SIZE) }
            ?: throw IOException("Could not open the file in Downloads")
    }

    override suspend fun finalize(name: String) {
        val uri = pending.remove(name) ?: resolveUri(name) ?: return
        val values = ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }
        context.contentResolver.update(uri, values, null, null)
    }

    override suspend fun abort(name: String) {
        val uri = pending.remove(name)
        if (uri != null) {
            runCatching { context.contentResolver.delete(uri, null, null) }
        }
    }

    override fun contentUri(name: String): Uri =
        pending[name] ?: resolveUri(name) ?: collection()

    override fun filePath(name: String): String? = null

    private fun resolveUri(name: String): Uri? {
        SafePath.validate(name) ?: return null
        val projection = arrayOf(MediaStore.MediaColumns._ID)
        val selection = "${MediaStore.MediaColumns.DISPLAY_NAME} = ? AND " +
            "${MediaStore.MediaColumns.RELATIVE_PATH} LIKE ?"
        val args = arrayOf(name, "%$relativePath%")
        return runCatching {
            context.contentResolver.query(collection(), projection, selection, args, null)
                ?.use { cursor ->
                    if (!cursor.moveToFirst()) return@use null
                    val id = cursor.getLong(cursor.getColumnIndexOrThrow(MediaStore.MediaColumns._ID))
                    Uri.withAppendedPath(collection(), id.toString())
                }
        }.getOrNull()
    }

    private companion object {
        const val BUFFER_SIZE = 1 shl 16
    }
}
