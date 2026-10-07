package com.sohayb.n13download.ui.util

import android.content.Context
import com.sohayb.n13download.R
import com.sohayb.n13download.domain.model.DestinationKind
import com.sohayb.n13download.domain.model.DownloadSettings

/**
 * One place that turns a destination into the words the user reads.
 *
 * The Properties screen, the Add Download summary and the Settings picker all
 * have to describe the same folder; keeping the phrasing here stops the three
 * from drifting apart.
 *
 * The prose parts ("App storage", "Chosen folder") come from string resources so
 * they translate; the folder path itself never does — `Downloads/N13-Download/`
 * is a location, not a sentence, and it stays left-to-right in every language.
 */
object DestinationLabels {

    /** Short name for the picker, e.g. "Downloads/N13-Download". */
    fun shortLabel(context: Context, kind: DestinationKind, folder: String): String = when (kind) {
        DestinationKind.APP -> context.getString(R.string.dest_app_storage)
        DestinationKind.MEDIA_STORE -> publicFolder(folder)
        DestinationKind.TREE -> context.getString(R.string.dest_chosen_folder)
    }

    /** Long, browseable location, e.g. "Downloads/N13-Download/". */
    fun pathLabel(context: Context, kind: DestinationKind, folder: String): String = when (kind) {
        DestinationKind.APP ->
            context.getString(R.string.dest_app_storage) + "/" + folder.trim('/')

        DestinationKind.MEDIA_STORE -> publicFolder(folder) + "/"

        DestinationKind.TREE -> folder.trim('/')
            .ifBlank { context.getString(R.string.dest_chosen_folder) }
    }

    /**
     * The same label, but aware of whether a SAF grant actually exists.
     *
     * A TREE destination with no URI cannot be used — the factory falls back to
     * app storage — so saying "Chosen folder" would be a lie about where the file
     * went.  This is the variant the Add Download summary should use.
     */
    fun resolvedLabel(
        context: Context,
        kind: DestinationKind,
        folder: String,
        treeUri: String,
    ): String = when (kind) {
        DestinationKind.TREE -> if (treeUri.isBlank()) {
            context.getString(R.string.dest_app_storage_no_folder)
        } else {
            pathLabel(context, kind, folder)
        }

        else -> pathLabel(context, kind, folder)
    }

    /**
     * The public folder path.
     *
     * `Downloads` is the platform's own directory name and the folder is created
     * on disk under that name, so it is deliberately not translated.
     */
    fun publicFolder(folder: String): String {
        val clean = folder.trim().trim('/')
        return if (clean.isEmpty()) "Downloads" else "Downloads/$clean"
    }

    fun defaultFolderPath(): String = "Downloads/${DownloadSettings.DEFAULT_FOLDER}/"
}
