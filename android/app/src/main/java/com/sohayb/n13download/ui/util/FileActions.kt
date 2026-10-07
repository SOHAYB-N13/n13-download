package com.sohayb.n13download.ui.util

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.widget.Toast
import androidx.core.content.FileProvider
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
        launch(context, intent, "No app can open this file type")
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
        launch(context, Intent.createChooser(intent, task.filename), "Nothing to share with")
    }

    /** Copies the source URL to the clipboard. */
    fun copyUrl(context: Context, task: DownloadTask) {
        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE)
            as? android.content.ClipboardManager ?: return
        clipboard.setPrimaryClip(android.content.ClipData.newPlainText("N13 link", task.url))
        Toast.makeText(context, "Link copied", Toast.LENGTH_SHORT).show()
    }

    /** Copies the on-disk path, or the destination label when there is no path. */
    suspend fun copyPath(context: Context, manager: DownloadManager, task: DownloadTask) {
        val path = manager.filePathFor(task) ?: manager.destinationLabelFor(task)
        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE)
            as? android.content.ClipboardManager ?: return
        clipboard.setPrimaryClip(android.content.ClipData.newPlainText("N13 path", path))
        Toast.makeText(context, "Path copied", Toast.LENGTH_SHORT).show()
    }

    /** Opens the destination folder in a file manager. */
    suspend fun openFolder(context: Context, manager: DownloadManager, task: DownloadTask) {
        val uri = manager.contentUriFor(task)
        val folderIntent = Intent(Intent.ACTION_VIEW).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            if (uri != null) {
                setDataAndType(uri, "resource/folder")
            }
        }
        try {
            context.startActivity(folderIntent)
        } catch (_: ActivityNotFoundException) {
            // Most file managers do not expose a folder view intent; fall back to
            // opening the file itself rather than failing silently.
            open(context, manager, task)
        }
    }

    private fun missingFile(context: Context, task: DownloadTask) {
        Toast.makeText(context, "${task.filename} is no longer on disk", Toast.LENGTH_LONG).show()
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
}
