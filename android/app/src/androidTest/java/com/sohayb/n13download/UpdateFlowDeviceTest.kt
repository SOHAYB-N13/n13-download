package com.sohayb.n13download

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.sohayb.n13download.core.AppLocale
import com.sohayb.n13download.domain.download.SettingsProvider
import com.sohayb.n13download.domain.model.AppLanguage
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.update.AppUpdate
import com.sohayb.n13download.domain.update.RemoteRelease
import com.sohayb.n13download.domain.update.UpdateCenter
import com.sohayb.n13download.domain.update.UpdateRepository
import com.sohayb.n13download.ui.theme.N13Theme
import com.sohayb.n13download.ui.update.N13UpdateDialog
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The update flow end to end, minus the network.
 *
 * A fake repository stands in for GitHub so the behaviour under test is the
 * app's own: what it decides to offer, which page it points at, and what happens
 * when the API is unreachable.
 */
@RunWith(AndroidJUnit4::class)
class UpdateFlowDeviceTest {

    @get:Rule
    val compose = createComposeRule()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @After
    fun restoreLocale() {
        AppLocale.seed(AppLanguage.DEFAULT)
    }

    // ---- What gets offered -------------------------------------------------

    /** 1.1.0 installed, 1.1.1 published -> offer it, pointing at the official page. */
    @Test
    fun newerRelease_isOfferedAndPointsAtTheOfficialPage() {
        val center = center(
            installedVersionCode = 2,
            installedVersionName = "1.1.0",
            repository = FakeUpdateRepository(androidRelease("android-v1.1.1", 3)),
        )

        center.start()

        val update = center.update.value
        assertEquals("1.1.1", update?.versionName)
        assertEquals(
            "https://github.com/SOHAYB-N13/n13-download/releases/tag/android-v1.1.1",
            update?.releaseUrl,
        )
    }

    /** Same version: nothing is shown. */
    @Test
    fun equalVersion_isNotOffered() {
        val center = center(
            installedVersionCode = 3,
            installedVersionName = "1.1.1",
            repository = FakeUpdateRepository(androidRelease("android-v1.1.1", 3)),
        )

        center.start()

        assertNull(center.update.value)
    }

    /**
     * GitHub unreachable, rate-limited, or offline: the app stays quiet and
     * keeps working.
     */
    @Test
    fun networkFailure_producesNoUpdateAndNoCrash() {
        val center = center(
            installedVersionCode = 2,
            installedVersionName = "1.1.0",
            repository = FakeUpdateRepository(latest = null),
        )

        center.start()

        assertNull(center.update.value)
    }

    /** A thrown error is treated the same as "no answer". */
    @Test
    fun repositoryThrowing_producesNoUpdateAndNoCrash() {
        val center = center(
            installedVersionCode = 2,
            installedVersionName = "1.1.0",
            repository = ThrowingUpdateRepository(),
        )

        center.start()

        assertNull(center.update.value)
    }

    /** A Windows release in the feed must never be offered to an Android user. */
    @Test
    fun windowsRelease_isNotOffered() {
        val windows = RemoteRelease(
            tag = "v1.9.9",
            title = "N13 Download Manager v1.9.9",
            body = "versionCode 999",
            htmlUrl = "https://github.com/SOHAYB-N13/n13-download/releases/tag/v1.9.9",
            isDraft = false,
            isPrerelease = false,
            assetNames = listOf("N13-Download-Manager-Setup.exe"),
        )
        val center = center(
            installedVersionCode = 2,
            installedVersionName = "1.1.0",
            repository = FakeUpdateRepository(windows),
        )

        center.start()

        assertNull(center.update.value)
    }

    // ---- Frequency ---------------------------------------------------------

    /** Within the 24h window the API is not called again. */
    @Test
    fun withinTwentyFourHours_doesNotAskGitHubAgain() {
        val repository = FakeUpdateRepository(androidRelease("android-v1.1.1", 3))
        val day = 24L * 60L * 60L * 1000L
        val center = center(
            installedVersionCode = 2,
            installedVersionName = "1.1.0",
            repository = repository,
            lastCheckAt = 1_000_000L,
            now = { 1_000_000L + day / 2 },
        )

        center.start()

        assertEquals(0, repository.calls)
        assertNull(center.update.value)
    }

    /** Past the 24h window it checks again. */
    @Test
    fun afterTwentyFourHours_checksAgain() {
        val repository = FakeUpdateRepository(androidRelease("android-v1.1.1", 3))
        val day = 24L * 60L * 60L * 1000L
        val center = center(
            installedVersionCode = 2,
            installedVersionName = "1.1.0",
            repository = repository,
            lastCheckAt = 1_000_000L,
            now = { 1_000_000L + day + 1 },
        )

        center.start()

        assertEquals(1, repository.calls)
        assertEquals("1.1.1", center.update.value?.versionName)
    }

    /** "Later" clears the offer and it does not come back this session. */
    @Test
    fun later_dismissesTheOffer() {
        val center = center(
            installedVersionCode = 2,
            installedVersionName = "1.1.0",
            repository = FakeUpdateRepository(androidRelease("android-v1.1.1", 3)),
        )
        center.start()
        assertEquals("1.1.1", center.update.value?.versionName)

        center.dismiss()

        assertNull(center.update.value)
    }

    // ---- The dialog --------------------------------------------------------

    /** The dialog renders with the release it was given. */
    @Test
    fun dialog_rendersInEnglish() {
        compose.setContent {
            N13Theme {
                N13UpdateDialog(
                    update = AppUpdate(
                        versionName = "1.1.1",
                        releaseUrl = "https://github.com/SOHAYB-N13/n13-download/releases/tag/android-v1.1.1",
                    ),
                    onDismiss = {},
                )
            }
        }
        compose.waitForIdle()

        compose.onNodeWithText(context.getString(R.string.update_available_title)).assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.update_download)).assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.update_later)).assertIsDisplayed()

        // The message names the new version.
        compose.onNodeWithText("1.1.1", substring = true).assertIsDisplayed()
    }

    /**
     * The Persian wording is what the app will actually show, so it is asserted
     * against the resources the Persian build resolves.
     */
    @Test
    fun dialogStrings_areTranslatedForPersian() {
        AppLocale.seed(AppLanguage.PERSIAN)
        val persian = AppLocale.wrap(context)

        assertEquals("نسخه جدید موجود است", persian.getString(R.string.update_available_title))
        assertEquals("دانلود نسخه جدید", persian.getString(R.string.update_download))
        assertEquals("بعداً", persian.getString(R.string.update_later))

        val message = persian.getString(R.string.update_available_message, "1.1.1")
        assertTrue(
            "Persian message should name the new version, was: $message",
            message.contains("1.1.1"),
        )
        assertTrue(
            "Persian message should be the published wording, was: $message",
            message.contains("N13 Download Manager"),
        )
    }

    // ------------------------------------------------------------------------

    /**
     * Runs the check eagerly on the calling thread, so the assertions do not
     * depend on timing.
     */
    private fun center(
        installedVersionCode: Int,
        installedVersionName: String,
        repository: UpdateRepository,
        lastCheckAt: Long = 0L,
        now: () -> Long = { 2_000_000_000_000L },
    ): UpdateCenter = UpdateCenter(
        repository = repository,
        settingsProvider = FakeSettingsProvider(
            DownloadSettings(lastUpdateCheckAt = lastCheckAt),
        ),
        installedVersionCode = installedVersionCode,
        installedVersionName = installedVersionName,
        scope = CoroutineScope(Dispatchers.Unconfined),
        now = now,
    )

    private fun androidRelease(tag: String, versionCode: Int): RemoteRelease {
        val versionName = tag.removePrefix("android-v")
        return RemoteRelease(
            tag = tag,
            title = "N13 Download Manager for Android v$versionName",
            body = "- Version: **$versionName** (versionCode $versionCode)\n",
            htmlUrl = "https://github.com/SOHAYB-N13/n13-download/releases/tag/$tag",
            isDraft = false,
            isPrerelease = false,
            assetNames = listOf("N13-Download-Manager-Android-v$versionName-release.apk"),
        )
    }

    private class FakeUpdateRepository(private val latest: RemoteRelease?) : UpdateRepository {
        var calls = 0
            private set

        override suspend fun latestAndroidRelease(): RemoteRelease? {
            calls++
            return latest
        }
    }

    private class ThrowingUpdateRepository : UpdateRepository {
        override suspend fun latestAndroidRelease(): RemoteRelease? =
            throw java.io.IOException("network down")
    }

    /** Settings held in memory so the test never writes to real preferences. */
    private class FakeSettingsProvider(initial: DownloadSettings) : SettingsProvider {
        private val state = MutableStateFlow(initial)
        override val settings: StateFlow<DownloadSettings> = state
        override suspend fun current(): DownloadSettings = state.value
        override suspend fun update(settings: DownloadSettings) {
            state.value = settings
        }
    }
}
