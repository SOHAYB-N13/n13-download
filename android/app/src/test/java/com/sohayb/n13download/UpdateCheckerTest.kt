package com.sohayb.n13download

import com.sohayb.n13download.domain.update.RemoteRelease
import com.sohayb.n13download.domain.update.UpdateChecker
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Version comparison and Android-release selection.
 *
 * Pure JVM tests — no device, no network. This is the part of the update feature
 * that decides whether the user is told about an update, so it is the part worth
 * pinning down precisely.
 */
class UpdateCheckerTest {

    // ---- Version comparison ------------------------------------------------

    /** The release this feature was built for: 1.1.0 -> 1.1.1. */
    @Test
    fun newerVersionCode_isAnUpdate() {
        val latest = androidRelease("android-v1.1.1", versionCode = 3)

        assertTrue(
            UpdateChecker.isUpdateAvailable(
                installedVersionCode = 2,
                installedVersionName = "1.1.0",
                latest = latest,
            ),
        )
    }

    /** Same build: never offer an update. */
    @Test
    fun equalVersionCode_isNotAnUpdate() {
        val latest = androidRelease("android-v1.1.0", versionCode = 2)

        assertFalse(
            UpdateChecker.isUpdateAvailable(
                installedVersionCode = 2,
                installedVersionName = "1.1.0",
                latest = latest,
            ),
        )
    }

    /** Running something newer than the published release: no downgrade prompt. */
    @Test
    fun lowerVersionCode_isNotAnUpdate() {
        val latest = androidRelease("android-v1.1.0", versionCode = 2)

        assertFalse(
            UpdateChecker.isUpdateAvailable(
                installedVersionCode = 5,
                installedVersionName = "1.2.0",
                latest = latest,
            ),
        )
    }

    /**
     * `versionCode` is authoritative, so a release with a bigger *name* but a
     * smaller code must not be offered.
     */
    @Test
    fun versionCodeWinsOverVersionName() {
        val latest = androidRelease("android-v9.9.9", versionCode = 2)

        assertFalse(
            UpdateChecker.isUpdateAvailable(
                installedVersionCode = 3,
                installedVersionName = "1.1.1",
                latest = latest,
            ),
        )
    }

    /** If a release omits the versionCode, fall back to the version name. */
    @Test
    fun missingVersionCode_fallsBackToVersionName() {
        val newer = androidRelease("android-v1.1.1", versionCode = null)
        val same = androidRelease("android-v1.1.0", versionCode = null)
        val older = androidRelease("android-v1.0.0", versionCode = null)

        assertTrue(UpdateChecker.isUpdateAvailable(2, "1.1.0", newer))
        assertFalse(UpdateChecker.isUpdateAvailable(2, "1.1.0", same))
        assertFalse(UpdateChecker.isUpdateAvailable(2, "1.1.0", older))
    }

    /** Multi-digit segments must compare numerically, not as text. */
    @Test
    fun versionSegments_compareNumerically() {
        assertEquals(1, UpdateChecker.compareVersions(listOf(1, 1, 10), listOf(1, 1, 9)))
        assertEquals(0, UpdateChecker.compareVersions(listOf(1, 1), listOf(1, 1, 0)))
        assertTrue(UpdateChecker.compareVersions(listOf(1, 2), listOf(1, 1, 9)) > 0)
    }

    // ---- Identifying the Android release -----------------------------------

    /** The Windows releases share the feed and must never be offered. */
    @Test
    fun windowsReleases_areNotTreatedAsAndroid() {
        val windows = RemoteRelease(
            tag = "v1.4.1",
            title = "N13 Download Manager v1.4.1",
            body = "Windows release",
            htmlUrl = "https://github.com/SOHAYB-N13/n13-download/releases/tag/v1.4.1",
            isDraft = false,
            isPrerelease = false,
            assetNames = listOf("N13-Download-Manager-Setup.exe"),
        )

        assertFalse(UpdateChecker.isAndroidRelease(windows))
        assertNull(UpdateChecker.versionNameOf(windows))
    }

    /** Drafts and pre-releases are not offered to normal users. */
    @Test
    fun draftsAndPrereleases_areIgnored() {
        assertFalse(UpdateChecker.isAndroidRelease(androidRelease("android-v1.1.1", 3, draft = true)))
        assertFalse(
            UpdateChecker.isAndroidRelease(androidRelease("android-v1.1.1", 3, prerelease = true)),
        )
    }

    /** With both products in the feed, the newest Android release wins. */
    @Test
    fun latestAndroidRelease_picksTheNewestAndroidBuild() {
        val releases = listOf(
            RemoteRelease(
                tag = "v1.4.1",
                title = "N13 Download Manager v1.4.1",
                body = "",
                htmlUrl = "https://github.com/SOHAYB-N13/n13-download/releases/tag/v1.4.1",
                isDraft = false,
                isPrerelease = false,
                assetNames = listOf("N13-Download-Manager-Setup.exe"),
            ),
            androidRelease("android-v1.0.0", versionCode = 1),
            androidRelease("android-v1.1.0", versionCode = 2),
            androidRelease("android-v1.1.1", versionCode = 3),
        )

        val latest = UpdateChecker.latestAndroidRelease(releases)

        assertEquals("android-v1.1.1", latest?.tag)
        assertEquals("1.1.1", latest?.let(UpdateChecker::versionNameOf))
    }

    @Test
    fun latestAndroidRelease_isNullWhenThereAreNoAndroidReleases() {
        assertNull(UpdateChecker.latestAndroidRelease(emptyList()))
    }

    // ---- Parsing helpers ---------------------------------------------------

    @Test
    fun versionCode_isReadFromTheReleaseNotes() {
        val release = androidRelease("android-v1.1.1", versionCode = 3)

        assertEquals(3, UpdateChecker.versionCodeOf(release))
    }

    @Test
    fun parseVersionName_rejectsNonVersions() {
        assertEquals(listOf(1, 1, 1), UpdateChecker.parseVersionName("1.1.1"))
        assertNull(UpdateChecker.parseVersionName("latest"))
        assertNull(UpdateChecker.parseVersionName(""))
        assertNull(UpdateChecker.parseVersionName("1.x.1"))
    }

    // ------------------------------------------------------------------------

    /** Mirrors the real release-notes shape, which carries the versionCode. */
    private fun androidRelease(
        tag: String,
        versionCode: Int?,
        draft: Boolean = false,
        prerelease: Boolean = false,
    ): RemoteRelease {
        val versionName = tag.removePrefix(UpdateChecker.ANDROID_TAG_PREFIX)
        val codeLine = versionCode?.let { "- Version: **$versionName** (versionCode $it)" }.orEmpty()
        return RemoteRelease(
            tag = tag,
            title = "N13 Download Manager for Android v$versionName",
            body = "## Compatibility\n\n$codeLine\n",
            htmlUrl = "https://github.com/SOHAYB-N13/n13-download/releases/tag/$tag",
            isDraft = draft,
            isPrerelease = prerelease,
            assetNames = listOf("N13-Download-Manager-Android-v$versionName-release.apk"),
        )
    }
}
