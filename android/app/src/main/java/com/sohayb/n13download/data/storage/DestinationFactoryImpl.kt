package com.sohayb.n13download.data.storage

import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import com.sohayb.n13download.domain.download.DestinationFactory
import com.sohayb.n13download.domain.download.DownloadDestination
import com.sohayb.n13download.domain.model.DestinationKind
import com.sohayb.n13download.domain.model.DownloadSettings
import java.io.File

/**
 * Chooses the right storage backend for the user's settings.
 *
 * The three Android storage eras are handled explicitly rather than assumed:
 *  - API 26-28: legacy external storage, so a real path in Downloads works
 *    behind `WRITE_EXTERNAL_STORAGE`.
 *  - API 29+:   scoped storage, so the public Downloads collection is reached
 *    through MediaStore (no permission needed for the app's own inserts).
 *  - Any API:   app-specific storage always works, and a SAF tree lets the user
 *    pick anywhere else.
 */
class DestinationFactoryImpl(private val context: Context) : DestinationFactory {

    /** Root for app-specific downloads: `Android/data/<pkg>/files/Download`. */
    fun appDownloadRoot(): File =
        context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
            ?: File(context.filesDir, "downloads")

    /** Root for `.part` files. Always app-private, so it is always writable. */
    fun workingRoot(): File = File(appDownloadRoot(), ".n13/work")

    override suspend fun create(settings: DownloadSettings): DownloadDestination =
        when (settings.destinationKind) {
            DestinationKind.APP -> FileDirectoryDestination(
                context = context,
                rootDirectory = appDownloadRoot(),
                folder = settings.destinationFolder,
                kind = DestinationKind.APP,
                labelPrefix = "App storage",
            )

            DestinationKind.MEDIA_STORE -> if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                MediaStoreDestination(context, settings.destinationFolder)
            } else {
                FileDirectoryDestination(
                    context = context,
                    rootDirectory = Environment.getExternalStoragePublicDirectory(
                        Environment.DIRECTORY_DOWNLOADS,
                    ),
                    folder = settings.destinationFolder,
                    kind = DestinationKind.MEDIA_STORE,
                    labelPrefix = "Downloads",
                )
            }

            DestinationKind.TREE -> {
                val uri = settings.destinationUri.takeIf { it.isNotBlank() }?.let(Uri::parse)
                if (uri == null) {
                    // The grant is gone; fall back to app storage rather than failing.
                    FileDirectoryDestination(
                        context = context,
                        rootDirectory = appDownloadRoot(),
                        folder = settings.destinationFolder,
                        kind = DestinationKind.APP,
                        labelPrefix = "App storage",
                    )
                } else {
                    TreeDestination(context, uri, settings.destinationFolder)
                }
            }
        }
}
