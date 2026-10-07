package com.sohayb.n13download.data.storage

import android.content.ContentResolver
import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import com.sohayb.n13download.domain.download.DownloadDestination
import com.sohayb.n13download.domain.model.DestinationKind
import java.io.BufferedOutputStream
import java.io.IOException
import java.io.OutputStream

/**
 * Publishes into a folder the user granted through the Storage Access Framework.
 *
 * This is how N13 lets you pick any destination on Android — an SD card, a cloud
 * provider's document tree, or a specific project folder.  The grant is a
 * persisted URI permission held by the app, so it survives restarts.
 *
 * Implemented directly on [DocumentsContract] rather than through
 * `androidx.documentfile`: the platform API is what that library wraps, and using
 * it directly avoids a dependency for the handful of operations needed here.
 *
 * Publishing mirrors the file-based destinations — write to a `.n13part` sibling,
 * then rename in [finalize] — so a partial file is never visible under the real
 * name.
 */
class TreeDestination(
    private val context: Context,
    private val treeUri: Uri,
    private val folder: String,
) : DownloadDestination {

    private val resolver: ContentResolver get() = context.contentResolver

    override val kind: DestinationKind = DestinationKind.TREE

    private val rootDocumentUri: Uri by lazy {
        val rootId = DocumentsContract.getTreeDocumentId(treeUri)
        DocumentsContract.buildDocumentUriUsingTree(treeUri, rootId)
    }

    override val displayPath: String
        get() {
            val name = queryDisplayName(rootDocumentUri) ?: "Folder"
            return if (folder.isBlank()) name else "$name/$folder"
        }

    override val reference: String get() = treeUri.toString()

    private var cachedDirectory: Uri? = null

    /** Finds or creates the destination folder inside the granted tree. */
    private fun ensureDirectory(): Uri? {
        cachedDirectory?.let { if (exists(it)) return it }
        val safe = SafePath.safeFolder(folder)
        val target: Uri? = if (safe.isEmpty()) {
            rootDocumentUri
        } else {
            val existing = findChild(rootDocumentUri, safe)
            if (existing != null && isDirectory(existing)) {
                existing
            } else {
                createDirectory(rootDocumentUri, safe)
            }
        }
        cachedDirectory = target
        return target
    }

    private fun createDirectory(parent: Uri, name: String): Uri? = runCatching {
        DocumentsContract.createDocument(
            resolver,
            parent,
            DocumentsContract.Document.MIME_TYPE_DIR,
            name,
        )
    }.getOrNull()

    private fun isDirectory(uri: Uri): Boolean =
        queryColumn(uri, DocumentsContract.Document.COLUMN_MIME_TYPE) ==
            DocumentsContract.Document.MIME_TYPE_DIR

    override suspend fun isAvailable(): Boolean = ensureDirectory() != null

    override suspend fun listNames(): Set<String> {
        val directory = ensureDirectory() ?: return emptySet()
        val names = mutableSetOf<String>()
        val childrenUri = childrenUri(directory) ?: return emptySet()
        val projection = arrayOf(DocumentsContract.Document.COLUMN_DISPLAY_NAME)

        runCatching {
            resolver.query(childrenUri, projection, null, null, null)?.use { cursor ->
                val column = cursor.getColumnIndexOrThrow(
                    DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                )
                while (cursor.moveToNext()) {
                    cursor.getString(column)?.takeIf { !it.endsWith(TEMP_SUFFIX) }?.let(names::add)
                }
            }
        }
        return names
    }

    override suspend fun exists(name: String): Boolean {
        val directory = ensureDirectory() ?: return false
        SafePath.validate(name) ?: return false
        return findChild(directory, name) != null
    }

    override suspend fun delete(name: String): Boolean {
        val directory = ensureDirectory() ?: return false
        SafePath.validate(name) ?: return false
        val child = findChild(directory, name) ?: return true
        return runCatching { DocumentsContract.deleteDocument(resolver, child) }.getOrDefault(false)
    }

    override suspend fun openOutput(name: String): OutputStream {
        val directory = ensureDirectory() ?: throw IOException("Destination folder is not available")
        SafePath.requireSafe(name)
        // Remove a leftover staging file from a previous attempt.
        findChild(directory, "$name$TEMP_SUFFIX")?.let { stale ->
            runCatching { DocumentsContract.deleteDocument(resolver, stale) }
        }
        val staging = runCatching {
            DocumentsContract.createDocument(resolver, directory, MIME_BINARY, "$name$TEMP_SUFFIX")
        }.getOrNull() ?: throw IOException("Could not create the file in that folder")

        return resolver.openOutputStream(staging)
            ?.let { BufferedOutputStream(it, BUFFER_SIZE) }
            ?: throw IOException("Could not open the file in that folder")
    }

    override suspend fun finalize(name: String) {
        val directory = ensureDirectory() ?: throw IOException("Destination folder is not available")
        val staging = findChild(directory, "$name$TEMP_SUFFIX")
            ?: throw IOException("Staged file is missing")

        // Only our own leftover can be in the way: the queue already de-duplicated
        // the name before the transfer started.
        findChild(directory, name)?.let { existing ->
            runCatching { DocumentsContract.deleteDocument(resolver, existing) }
        }

        val renamed = runCatching {
            DocumentsContract.renameDocument(resolver, staging, name)
        }.getOrNull() ?: throw IOException("Could not publish the finished file")
        cachedDirectory = directory
        // Touch the result so a provider that returns null on success still works.
        if (!exists(name)) {
            throw IOException("The finished file did not appear in the destination")
        }
        renamed.toString()
    }

    override suspend fun abort(name: String) {
        val directory = ensureDirectory() ?: return
        findChild(directory, "$name$TEMP_SUFFIX")?.let { staging ->
            runCatching { DocumentsContract.deleteDocument(resolver, staging) }
        }
    }

    override fun contentUri(name: String): Uri {
        val directory = ensureDirectory()
        return directory?.let { findChild(it, name) } ?: treeUri
    }

    override fun filePath(name: String): String? = null

    // ------------------------------------------------------------------ //

    private fun childrenUri(directory: Uri): Uri? = runCatching {
        val documentId = DocumentsContract.getDocumentId(directory)
        DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, documentId)
    }.getOrNull()

    private fun findChild(directory: Uri, name: String): Uri? {
        val children = childrenUri(directory) ?: return null
        val projection = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
        )
        return runCatching {
            resolver.query(children, projection, null, null, null)?.use { cursor ->
                val idColumn = cursor.getColumnIndexOrThrow(DocumentsContract.Document.COLUMN_DOCUMENT_ID)
                val nameColumn =
                    cursor.getColumnIndexOrThrow(DocumentsContract.Document.COLUMN_DISPLAY_NAME)
                while (cursor.moveToNext()) {
                    if (cursor.getString(nameColumn) == name) {
                        return@use DocumentsContract.buildDocumentUriUsingTree(
                            treeUri,
                            cursor.getString(idColumn),
                        )
                    }
                }
                null
            }
        }.getOrNull()
    }

    private fun exists(uri: Uri): Boolean =
        queryColumn(uri, DocumentsContract.Document.COLUMN_DOCUMENT_ID) != null

    private fun queryDisplayName(uri: Uri): String? =
        queryColumn(uri, DocumentsContract.Document.COLUMN_DISPLAY_NAME)

    /** Single-column query on a document URI; null when it is gone or unreadable. */
    private fun queryColumn(uri: Uri, column: String): String? = runCatching {
        resolver.query(uri, arrayOf(column), null, null, null)?.use { cursor ->
            if (cursor.moveToFirst() && !cursor.isNull(0)) cursor.getString(0) else null
        }
    }.getOrNull()

    private companion object {
        const val BUFFER_SIZE = 1 shl 16
        const val TEMP_SUFFIX = ".n13part"
        const val MIME_BINARY = "application/octet-stream"
    }
}
