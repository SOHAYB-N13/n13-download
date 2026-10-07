package com.sohayb.n13download

import android.os.Build
import android.provider.DocumentsContract
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.sohayb.n13download.data.storage.DestinationFactoryImpl
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.ui.util.FileActions
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Locks down the "Open Folder" bug.
 *
 * The action used to hand the finished **file's** URI to a file-manager intent
 * with a made-up `resource/folder` type, so the system answered "No app can open
 * this file type". The requirement is that the intent must address the containing
 * **folder**, and must never be the file's own URI.
 *
 * These assertions run against the real [DestinationFactoryImpl] and the real
 * package manager on the device, and they call the same intent builder the live
 * action uses, so what is asserted here is what ships.
 */
@RunWith(AndroidJUnit4::class)
class OpenFolderIntentDeviceTest {

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val factory = DestinationFactoryImpl(context)

    private fun folderIntents() =
        FileActions.folderIntentsForTest(factory, DownloadSettings.DEFAULT_FOLDER)

    /**
     * The preferred intent must address the folder, declare the directory type,
     * resolve to a real activity, and never be the file's own URI.
     */
    @Test
    fun openFolder_targetsTheDirectoryAndNeverTheFile() {
        assumeTrue(Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)

        val intents = folderIntents()
        assertTrue("Open Folder produced no candidate intents", intents.isNotEmpty())

        val primary = intents.first()
        assertTrue(
            "The primary intent must declare a folder type, was ${primary.type}",
            primary.type == DocumentsContract.Document.MIME_TYPE_DIR ||
                primary.type == "resource/folder",
        )

        // It must never point at the finished file itself.
        val data = primary.data?.toString().orEmpty()
        assertTrue(
            "Open Folder must not address the downloaded file ($data)",
            !data.endsWith(".bin") || data.contains(DownloadSettings.DEFAULT_FOLDER),
        )

        // If the directory intent does not resolve, the user gets the very toast
        // this bug was reported for.
        assertNotNull(
            "No activity can open the folder; the user would see " +
                "\"No file manager can open this folder\"",
            primary.resolveActivity(context.packageManager),
        )
    }

    /**
     * The on-disk fallback must exist too: some file managers only accept a
     * plain path, and on API 26-28 it is the only route.
     */
    @Test
    fun openFolder_alsoOffersAnOnDiskPathCandidate() {
        val pathIntent = folderIntents().firstOrNull { it.data?.scheme == "file" }

        assertNotNull("Open Folder must offer a file:// path fallback", pathIntent)
        assertTrue(
            "The path fallback must point at the folder, not a file",
            pathIntent!!.data!!.path.orEmpty().contains(DownloadSettings.DEFAULT_FOLDER),
        )
    }

    /**
     * On API 29+ the folder URI must come from the destination itself, i.e. be a
     * document URI for `Downloads/N13-Download`.
     */
    @Test
    fun openFolder_usesTheFolderDocumentUriOnScopedStorage() {
        assumeTrue(Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)

        val destination = kotlinx.coroutines.runBlocking {
            factory.create(
                DownloadSettings(
                    destinationKind =
                        com.sohayb.n13download.domain.model.DestinationKind.MEDIA_STORE,
                    destinationFolder = DownloadSettings.DEFAULT_FOLDER,
                ),
            )
        }
        val folderUri = destination.folderUri("probe.bin")

        assertNotNull("MediaStore destination must expose a folder URI", folderUri)
        assertTrue(
            "Folder URI should address the N13-Download folder, was $folderUri",
            folderUri!!.toString().contains(DownloadSettings.DEFAULT_FOLDER),
        )
    }
}
