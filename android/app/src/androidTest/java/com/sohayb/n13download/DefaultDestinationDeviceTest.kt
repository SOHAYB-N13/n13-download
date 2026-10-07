package com.sohayb.n13download

import android.os.Build
import android.os.Environment
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.sohayb.n13download.data.storage.DestinationFactoryImpl
import com.sohayb.n13download.domain.model.DestinationKind
import com.sohayb.n13download.domain.model.DownloadSettings
import java.io.File
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Proves the *default* download destination behaves the way a user expects.
 *
 * The user-visible requirement is blunt: a download that needs no configuration
 * must land in the shared Downloads folder, under `N13-Download`, where any file
 * manager can find it — and the folder must come into existence by itself.
 *
 * This exercises the real [DestinationFactoryImpl] against the real MediaStore,
 * not a sandbox: it writes through the same path a real download takes.
 */
@RunWith(AndroidJUnit4::class)
class DefaultDestinationDeviceTest {

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val factory = DestinationFactoryImpl(context)

    /** Anything this test published, so it can be removed afterwards. */
    private val published = mutableListOf<String>()

    @After
    fun tearDown() {
        // Never leave test artefacts in the user's Downloads folder.
        runBlocking {
            val destination = factory.create(defaultSettings())
            published.forEach { runCatching { destination.delete(it) } }
        }
    }

    /**
     * A brand-new install must target `Downloads/N13-Download/` with no setup.
     *
     * This is the assertion that would have failed before the default was
     * changed: a fresh install used to inherit an empty/legacy destination.
     */
    @Test
    fun defaultSettings_targetTheSharedDownloadsFolder() {
        val settings = DownloadSettings()

        assertEquals(DestinationKind.MEDIA_STORE, settings.destinationKind)
        assertEquals("N13-Download", settings.destinationFolder)
        assertEquals(DownloadSettings.DEFAULT_FOLDER, settings.destinationFolder)
    }

    /** The destination must report the friendly path the UI shows. */
    @Test
    fun defaultDestination_reportsAFriendlyPublicPath() = runBlocking {
        val destination = factory.create(defaultSettings())

        assertEquals(DestinationKind.MEDIA_STORE, destination.kind)
        assertEquals("Downloads/N13-Download/", destination.friendlyPath)
        assertTrue("the destination must be usable", destination.isAvailable())
    }

    /**
     * Writing through the default destination must create
     * `Downloads/N13-Download/` on the shared volume and publish the file.
     *
     * The folder is created implicitly by MediaStore from `RELATIVE_PATH`, which
     * is what makes this work without a storage permission on modern Android.
     */
    @Test
    fun writingThroughTheDefaultDestination_landsInTheSharedFolder() = runBlocking {
        assumeTrue(Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)

        val name = "n13-default-destination-${System.currentTimeMillis()}.bin"
        val payload = ByteArray(64 * 1024) { index -> (index * 13).toByte() }
        val destination = factory.create(defaultSettings())

        destination.openOutput(name).use { it.write(payload) }
        destination.finalize(name)
        published.add(name)

        // The file must be enumerable through the destination…
        assertTrue(
            "the destination should list the file it just published",
            destination.listNames().contains(name),
        )
        assertTrue("exists() should agree", destination.exists(name))

        // …and it must be physically present in the shared Downloads folder.
        val onDisk = File(
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
            "${DownloadSettings.DEFAULT_FOLDER}/$name",
        )
        assertTrue("the file must exist at ${onDisk.absolutePath}", onDisk.exists())
        assertTrue(
            "the containing folder must itself have been created",
            onDisk.parentFile?.isDirectory == true,
        )
        assertEquals(
            "the published length must match what was written",
            payload.size.toLong(),
            onDisk.length(),
        )

        // Read the bytes back the way any other app would: through the
        // MediaStore URI.  Opening the raw path directly is exactly what scoped
        // storage forbids, so `File.readBytes()` here would be a test bug rather
        // than an app one.
        val viaUri = context.contentResolver
            .openInputStream(destination.contentUri(name))
            ?.use { it.readBytes() }
        assertTrue(
            "reading through the content URI must return the original bytes",
            viaUri != null && viaUri.contentEquals(payload),
        )
    }

    /**
     * An in-flight file must be invisible until it is finalised.
     *
     * `IS_PENDING` is what stops a half-written download from looking like a
     * finished file to the user or to the duplicate check.
     */
    @Test
    fun anUnfinalisedFileIsNotVisible() = runBlocking {
        assumeTrue(Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)

        val name = "n13-pending-${System.currentTimeMillis()}.bin"
        val destination = factory.create(defaultSettings())

        destination.openOutput(name).use { it.write(ByteArray(1024)) }
        // Deliberately no finalize().
        assertTrue(
            "a pending file must not be listed as existing",
            !destination.exists(name),
        )
        destination.abort(name)
    }

    private fun defaultSettings() = DownloadSettings(
        destinationKind = DestinationKind.MEDIA_STORE,
        destinationFolder = DownloadSettings.DEFAULT_FOLDER,
    )
}
