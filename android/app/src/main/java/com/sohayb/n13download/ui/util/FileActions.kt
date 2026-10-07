package com.sohayb.n13download.ui.util

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.DocumentsContract
import android.widget.Toast
import androidx.core.content.FileProvider
import com.sohayb.n13download.R
import com.sohayb.n13download.domain.download.DownloadManager
import com.sohayb.n13download.domain.model.DownloadTask
import java.io.File

/**
 * Open / share / reveal actions for a finished download.
 *
 * Everything goes through a content URI: the MediaStore and SAF destinations
 * already provide one, and app-storage files are handed out through the app's
 * FileProvider.  A MIME type the device cannot handle is reported as a toast
 * instead of crashing, which is the N13 "handle unsupported types gracefully"
 * rule.
 */
object FileActions {

    /** Opens the file with whatever app handles its MIME type. */
    suspend fun open(context: Context, manager: DownloadManager, task: DownloadTask) {
        val uri = manager.contentUriFor(task) ?: return missingFile(context, task)
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, task.mimeType)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        launch(context, intent, context.getString(R.string.toast_no_app_for_file))
    }

    /** Shares the file through the system share sheet. */
    suspend fun share(context: Context, manager: DownloadManager, task: DownloadTask) {
        val uri = manager.contentUriFor(task) ?: return missingFile(context, task)
        val intent = Intent(Intent.ACTION_SEND).apply {
            type = task.mimeType
            putExtra(Intent.EXTRA_STREAM, uri)
            putExtra(Intent.EXTRA_SUBJECT, task.filename)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        launch(
            context,
            Intent.createChooser(intent, task.filename),
            context.getString(R.string.toast_nothing_to_share),
        )
    }

    /** Copies the source URL to the clipboard. */
    fun copyUrl(context: Context, task: DownloadTask) {
        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE)
            as? android.content.ClipboardManager ?: return
        clipboard.setPrimaryClip(android.content.ClipData.newPlainText("N13 link", task.url))
        Toast.makeText(context, context.getString(R.string.toast_link_copied), Toast.LENGTH_SHORT).show()
    }

    /** Copies the on-disk path, or the destination label when there is no path. */
    suspend fun copyPath(context: Context, manager: DownloadManager, task: DownloadTask) {
        val path = manager.filePathFor(task) ?: manager.destinationLabelFor(task)
        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE)
            as? android.content.ClipboardManager ?: return
        clipboard.setPrimaryClip(android.content.ClipData.newPlainText("N13 path", path))
        Toast.makeText(context, context.getString(R.string.toast_path_copied), Toast.LENGTH_SHORT).show()
    }

    /**
     * Opens the folder that *contains* the download, not the download itself.
     *
     * The old implementation set the finished **file's** content URI on an
     * `ACTION_VIEW` intent with a made-up `resource/folder` type.  Only a handful
     * of file managers understand that pair, so on a normal device every
     * candidate either failed to match or refused the URI and the system
     * surfaced "No app can open this file type".  It also fell back to [open],
     * i.e. it deliberately tried to open the *file* by MIME type — the exact
     * behaviour this action must not have.
     *
     * The fix targets the folder instead:
     *  - API 26+: the standards-track `DocumentsContract.ACTION_OPEN_DOCUMENT`
     *    pointed at the folder's document URI, with the tree URI as a second
     *    candidate.  Both are ordinary user-visible file documents, so any
     *    DocumentsUI-based picker resolves them.
     *  - Every API: an `ACTION_VIEW` on the on-disk folder path, which file
     *    managers that advertise `resource/folder` do accept (and which is the
     *    only route on API 26-28, where SAF does not take part here).
     *
     * Nothing falls back to opening the file: if no file manager can be found the
     * user gets a message and can still use Open File or Copy path.
     */
    suspend fun openFolder(context: Context, manager: DownloadManager, task: DownloadTask) {
        for (intent in folderIntents(manager, task)) {
            if (tryStart(context, intent)) return
        }
        Toast.makeText(
            context,
            context.getString(R.string.toast_no_file_manager),
            Toast.LENGTH_LONG,
        ).show()
    }

    /** Candidate intents that reveal the destination folder, best first. */
    private suspend fun folderIntents(
        manager: DownloadManager,
        task: DownloadTask,
    ): List<Intent> = folderIntents(
        folderUri = manager.folderUriFor(task),
        folderPath = folderPath(manager, task),
    )

    /**
     * The single implementation of "which intents can reveal this folder".
     *
     * Both the live action and its test drive this, so the asserted behaviour and
     * the shipped behaviour cannot drift apart.
     */
    private fun folderIntents(folderUri: Uri?, folderPath: String?): List<Intent> {
        val intents = mutableListOf<Intent>()

        if (folderUri != null && isAtLeastQ) {
            // Preferred: the folder as a document. DocumentsUI's FilesActivity
            // declares this action/type pair, and it resolves without any storage
            // permission. Verified on-device: adding CATEGORY_OPENABLE makes the
            // intent unresolvable, because the directory type is not "openable"
            // in the provider's eyes — so it is deliberately omitted.
            val directoryType = DocumentsContract.Document.MIME_TYPE_DIR
            intents += Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(folderUri, directoryType)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }

            // Some pickers only accept the tree form, matched on the data URI.
            intents += Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).apply {
                setDataAndType(folderUriAsTree(folderUri), directoryType)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
        }

        // Works on every supported API for on-disk folders. The file path's parent
        // is the folder; when there is no path (MediaStore on API 29+ hides it)
        // the public Downloads path is rebuilt from the destination reference.
        //
        // Verified on-device against a stock device with no third-party file
        // manager: `file://` + "resource/folder" resolves to *nothing*, so the
        // directory MIME type is what makes this candidate real. Both are offered
        // so OEM file managers that only know the legacy type still match.
        folderPath?.let { path ->
            val folder = File(path)
            intents += Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(Uri.fromFile(folder), DocumentsContract.Document.MIME_TYPE_DIR)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            intents += Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(Uri.fromFile(folder), LEGACY_FOLDER_TYPE)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
        }

        return intents
    }

    /**
     * Rewrites a `.../document/<id>` URI into the matching `.../tree/<id>` form.
     *
     * `ACTION_OPEN_DOCUMENT_TREE` is matched on the tree URI in the data field,
     * not on a document URI plus an extra, so the id has to be carried across.
     * Returns the input unchanged for URIs that are already trees or that this
     * does not understand — a wrong candidate is simply skipped by [openFolder].
     */
    private fun folderUriAsTree(documentUri: Uri): Uri = runCatching {
        val documentId = DocumentsContract.getDocumentId(documentUri)
        DocumentsContract.buildTreeDocumentUri(documentUri.authority.orEmpty(), documentId)
    }.getOrDefault(documentUri)

    /** The folder's on-disk path, or null when it truly has none. */
    private suspend fun folderPath(manager: DownloadManager, task: DownloadTask): String? {
        manager.filePathFor(task)?.let { return File(it).parent }

        // The destination itself lives in storage, so read it from there rather
        // than from settings; it is the authoritative record for this task.
        val reference = manager.folderReferenceFor(task)
        return if (reference.isNotBlank()) {
            val publicRoot = Environment.getExternalStoragePublicDirectory(
                Environment.DIRECTORY_DOWNLOADS,
            )
            File(publicRoot, reference).absolutePath
        } else {
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
                .absolutePath
        }
    }

    /**
     * The candidate folder intents for a real task, in priority order.
     *
     * Exposed so an instrumentation test can assert against a genuinely
     * completed download — and against the device's real package manager —
     * without launching a file manager. It calls the same private builder the
     * live action uses, so asserted behaviour and shipped behaviour cannot
     * drift. Not part of the app's public surface.
     */
    @androidx.annotation.VisibleForTesting
    internal suspend fun folderIntentsForTask(
        task: DownloadTask,
        manager: DownloadManager,
    ): List<Intent> = folderIntents(manager, task)

    /** Starts the first activity that accepts [intent], skipping anything else. */
    private fun tryStart(context: Context, intent: Intent): Boolean = try {
        context.startActivity(intent)
        true
    } catch (_: ActivityNotFoundException) {
        false
    } catch (_: SecurityException) {
        false
    }

    /**
     * The candidate folder intents for a destination folder, in priority order.
     *
     * Exposed so an instrumentation test can assert that "Open Folder" addresses
     * a **folder** — and never the downloaded file — without having to launch a
     * file manager on the device. It calls the same private builder the live
     * action uses. Not part of the app's public surface.
     */
    @androidx.annotation.VisibleForTesting
    internal fun folderIntentsForTest(
        factory: com.sohayb.n13download.domain.download.DestinationFactory,
        folder: String,
    ): List<Intent> {
        val destination = kotlinx.coroutines.runBlocking {
            factory.create(
                com.sohayb.n13download.domain.model.DownloadSettings(
                    destinationKind =
                        com.sohayb.n13download.domain.model.DestinationKind.MEDIA_STORE,
                    destinationFolder = folder,
                ),
            )
        }
        return folderIntents(
            folderUri = destination.folderUri(PROBE_NAME),
            folderPath = destination.filePath(PROBE_NAME)?.let { File(it).parent }
                ?: folderPathFromReference(folder),
        )
    }

    /** Mirrors the live path fallback for a MediaStore destination. */
    private fun folderPathFromReference(reference: String): String {
        val publicRoot = Environment.getExternalStoragePublicDirectory(
            Environment.DIRECTORY_DOWNLOADS,
        )
        return if (reference.isNotBlank()) {
            File(publicRoot, reference).absolutePath
        } else {
            publicRoot.absolutePath
        }
    }

    private fun missingFile(context: Context, task: DownloadTask) {
        Toast.makeText(
            context,
            context.getString(R.string.toast_file_gone, task.filename),
            Toast.LENGTH_LONG,
        ).show()
    }

    private fun launch(context: Context, intent: Intent, failureMessage: String) {
        try {
            context.startActivity(intent)
        } catch (_: ActivityNotFoundException) {
            Toast.makeText(context, failureMessage, Toast.LENGTH_LONG).show()
        } catch (_: SecurityException) {
            Toast.makeText(context, failureMessage, Toast.LENGTH_LONG).show()
        }
    }

    /** Kept for callers that hold a raw path rather than a task. */
    @Suppress("unused")
    fun fileUri(context: Context, file: File): Uri =
        FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)

    val isAtLeastQ: Boolean get() = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q

    /**
     * A harmless placeholder name used when only the *folder* is of interest.
     * Folder resolution ignores the leaf name, so any safe value will do.
     */
    private const val PROBE_NAME = "n13-probe.bin"

    /** Legacy folder type some OEM file managers advertise instead of the MIME. */
    private const val LEGACY_FOLDER_TYPE = "resource/folder"
}
