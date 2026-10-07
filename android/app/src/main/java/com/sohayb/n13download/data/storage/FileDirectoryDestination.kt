package com.sohayb.n13download.data.storage

import android.content.Context
import android.net.Uri
import androidx.core.content.FileProvider
import com.sohayb.n13download.domain.download.DownloadDestination
import com.sohayb.n13download.domain.model.DestinationKind
import java.io.BufferedOutputStream
import java.io.File
import java.io.IOException
import java.io.OutputStream

/**
 * A plain directory destination.
 *
 * Used for app-specific storage (no permission needed on any supported API) and
 * for the public Downloads folder on API 26-28, where legacy external storage is
 * still reachable behind `WRITE_EXTERNAL_STORAGE`.
 *
 * Publishing is atomic: bytes go to a `.n13part` sibling and only replace the
 * target in [finalize].  A crash or a retry therefore never leaves a truncated
 * file under the user's real name, and an unrelated file with the same name is
 * never silently destroyed.
 */
class FileDirectoryDestination(
    private val context: Context,
    private val rootDirectory: File,
    private val folder: String,
    override val kind: DestinationKind,
    private val labelPrefix: String,
) : DownloadDestination {

    private val directory: File by lazy {
        val safe = SafePath.safeFolder(folder)
        if (safe.isEmpty()) rootDirectory else File(rootDirectory, safe)
    }

    override val displayPath: String
        get() = if (folder.isBlank()) labelPrefix else "$labelPrefix/$folder"

    override val reference: String get() = folder

    override suspend fun isAvailable(): Boolean = try {
        if (directory.isDirectory) true else directory.mkdirs() || directory.isDirectory
    } catch (_: SecurityException) {
        false
    }

    override suspend fun listNames(): Set<String> =
        directory.list()?.filterNot { it.endsWith(TEMP_SUFFIX) }?.toSet().orEmpty()

    override suspend fun exists(name: String): Boolean =
        SafePath.resolve(directory, name)?.exists() == true

    override suspend fun delete(name: String): Boolean {
        val file = SafePath.resolve(directory, name) ?: return false
        return !file.exists() || file.delete()
    }

    override suspend fun openOutput(name: String): OutputStream {
        if (!isAvailable()) throw IOException("Destination folder is not available")
        SafePath.requireSafe(name)
        val staging = stagingFile(name)
        staging.parentFile?.mkdirs()
        return BufferedOutputStream(staging.outputStream(), BUFFER_SIZE)
    }

    override suspend fun finalize(name: String) {
        val staging = stagingFile(name)
        val target = SafePath.resolve(directory, name)
            ?: throw IOException("Unsafe file name")
        if (!staging.exists()) throw IOException("Staged file is missing")
        // The queue already guaranteed `name` is unique; only our own leftovers
        // can be in the way, so replacing is safe here.
        if (target.exists() && !target.delete()) {
            throw IOException("Could not replace the existing file")
        }
        if (!staging.renameTo(target)) {
            // Cross-device fallback (should not happen inside one directory).
            staging.copyTo(target, overwrite = true)
            staging.delete()
        }
    }

    override suspend fun abort(name: String) {
        runCatching { stagingFile(name).delete() }
    }

    override fun contentUri(name: String): Uri {
        val file = SafePath.resolve(directory, name)
            ?: throw IllegalArgumentException("Unsafe file name")
        return FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
    }

    override fun filePath(name: String): String? = SafePath.resolve(directory, name)?.absolutePath

    private fun stagingFile(name: String): File =
        File(directory, "$name$TEMP_SUFFIX")

    private companion object {
        const val BUFFER_SIZE = 1 shl 16
        const val TEMP_SUFFIX = ".n13part"
    }
}
