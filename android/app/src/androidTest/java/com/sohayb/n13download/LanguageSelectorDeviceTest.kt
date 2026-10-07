package com.sohayb.n13download

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.sohayb.n13download.core.AppLocale
import com.sohayb.n13download.data.engine.OkHttpDownloadEngine
import com.sohayb.n13download.data.local.N13Database
import com.sohayb.n13download.data.repository.RoomDownloadRepository
import com.sohayb.n13download.data.storage.DestinationFactoryImpl
import com.sohayb.n13download.domain.download.DownloadManager
import com.sohayb.n13download.domain.download.DownloadQueue
import com.sohayb.n13download.domain.download.SettingsProvider
import com.sohayb.n13download.domain.model.AppLanguage
import com.sohayb.n13download.domain.model.ConnectionMode
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.model.DuplicatePolicy
import com.sohayb.n13download.ui.screens.settings.SettingsScreen
import com.sohayb.n13download.ui.theme.N13Theme
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Verifies the language selector on the real Settings screen.
 *
 * The device this runs on blocks input injection, so scrolling to the Language
 * section by hand is not possible; rendering the screen and driving it through
 * the Compose test API is both possible and more deterministic.
 */
@RunWith(AndroidJUnit4::class)
class LanguageSelectorDeviceTest {

    @get:Rule
    val compose = createComposeRule()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private lateinit var database: N13Database
    private lateinit var engine: OkHttpDownloadEngine
    private lateinit var scope: CoroutineScope
    private lateinit var provider: FakeSettingsProvider
    private lateinit var manager: DownloadManager

    @Before
    fun setUp() {
        File(context.getDatabasePath(DB_NAME).parentFile ?: context.filesDir, DB_NAME).delete()
        database = Room.databaseBuilder(context, N13Database::class.java, DB_NAME).build()
        database.clearAllTables()

        engine = OkHttpDownloadEngine()
        val destinations = DestinationFactoryImpl(context)
        provider = FakeSettingsProvider(
            DownloadSettings(
                connectionMode = ConnectionMode.MANUAL,
                duplicatePolicy = DuplicatePolicy.ASK,
                language = AppLanguage.PERSIAN,
            ),
        )
        scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val queue = DownloadQueue(
            repository = RoomDownloadRepository(database.downloadTaskDao()),
            engine = engine,
            settingsProvider = provider,
            destinations = destinations,
            workingRoot = File(context.cacheDir, "lang-test-work"),
            scope = scope,
        )
        manager = DownloadManager(
            repository = RoomDownloadRepository(database.downloadTaskDao()),
            engine = engine,
            queue = queue,
            settingsProvider = provider,
            destinations = destinations,
        )
    }

    @After
    fun tearDown() {
        engine.shutdown()
        scope.cancel()
        database.close()
        File(context.getDatabasePath(DB_NAME).parentFile ?: context.filesDir, DB_NAME).delete()
        AppLocale.seed(AppLanguage.DEFAULT)
    }

    /** Both languages must be offered, with the current one marked selected. */
    @Test
    fun settings_offersBothLanguages() {
        compose.setContent {
            N13Theme {
                SettingsScreen(manager = manager)
            }
        }
        compose.waitForIdle()

        // The labels are the languages' own names, so they read the same in any
        // interface language — "English" and "فارسی". They sit below the fold on
        // a phone, so scroll them into view rather than asserting on-screen.
        compose.onNodeWithText("English").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("فارسی").performScrollTo().assertIsDisplayed()
    }

    /** Choosing a language must persist it. */
    @Test
    fun selectingALanguage_persistsIt() {
        compose.setContent {
            N13Theme {
                SettingsScreen(manager = manager)
            }
        }
        compose.waitForIdle()

        compose.onNodeWithText("English").performScrollTo().performClick()
        compose.waitForIdle()

        val stored = runBlocking { provider.current().language }
        assertEquals(AppLanguage.ENGLISH, stored)
    }

    private companion object {
        const val DB_NAME = "n13-language-test.db"
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
