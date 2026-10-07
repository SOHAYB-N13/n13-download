package com.sohayb.n13download.data.prefs

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.doublePreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.sohayb.n13download.core.BrowserHeaders
import com.sohayb.n13download.domain.download.SettingsProvider
import com.sohayb.n13download.domain.model.ConnectionMode
import com.sohayb.n13download.domain.model.DestinationKind
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.model.DuplicatePolicy
import com.sohayb.n13download.domain.model.ThemeMode
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.settingsDataStore: DataStore<Preferences> by preferencesDataStore(
    name = "n13-settings",
)

/**
 * Settings backed by DataStore.
 *
 * Defaults come from [DownloadSettings], which mirrors the Windows N13
 * `config/settings.py`, so a fresh install behaves exactly like the desktop app.
 * Every key here is read by real code — there is no toggle that does nothing.
 */
class DataStoreSettingsProvider(context: Context) : SettingsProvider {

    private val store = context.applicationContext.settingsDataStore

    override val settings: Flow<DownloadSettings> = store.data.map { it.toSettings() }

    override suspend fun current(): DownloadSettings = store.data.first().toSettings()

    override suspend fun update(settings: DownloadSettings) {
        store.edit { prefs ->
            prefs[Keys.DESTINATION_KIND] = settings.destinationKind.storageValue
            prefs[Keys.DESTINATION_URI] = settings.destinationUri
            prefs[Keys.DESTINATION_FOLDER] = settings.destinationFolder
            prefs[Keys.CONNECTION_MODE] = settings.connectionMode.storageValue
            prefs[Keys.NUM_THREADS] = settings.numThreads
            prefs[Keys.SMART_MAX_CONNECTIONS] = settings.smartMaxConnections
            prefs[Keys.SMART_ADAPTIVE] = settings.smartAdaptive
            prefs[Keys.MAX_CONCURRENT] = settings.maxConcurrent
            prefs[Keys.MAX_SPEED_BPS] = settings.maxSpeedBps
            prefs[Keys.DUPLICATE_POLICY] = settings.duplicatePolicy.storageValue
            prefs[Keys.AUTO_CATEGORIZE] = settings.autoCategorize
            prefs[Keys.START_IMMEDIATELY] = settings.startImmediately
            prefs[Keys.RESUME_ON_STARTUP] = settings.resumeOnStartup
            prefs[Keys.MAX_RETRIES] = settings.maxRetries
            prefs[Keys.RETRY_DELAY] = settings.retryDelay
            prefs[Keys.RETRY_BACKOFF] = settings.retryBackoff
            prefs[Keys.RETRY_JITTER] = settings.retryJitter
            prefs[Keys.RETRY_MAX_DELAY] = settings.retryMaxDelay
            prefs[Keys.VERIFY_SSL] = settings.verifySsl
            prefs[Keys.VERIFY_SIZE] = settings.verifySize
            prefs[Keys.BLOCK_PRIVATE_URLS] = settings.blockPrivateUrls
            prefs[Keys.NOTIFICATIONS_ENABLED] = settings.notificationsEnabled
            prefs[Keys.NOTIFY_COMPLETED] = settings.notifyCompleted
            prefs[Keys.NOTIFY_FAILED] = settings.notifyFailed
            prefs[Keys.NOTIFY_STARTED] = settings.notifyStarted
            prefs[Keys.USER_AGENT] = settings.userAgent
            prefs[Keys.THEME_MODE] = settings.themeMode.storageValue
            prefs[Keys.ACCENT_COLOR] = settings.accentColor
        }
    }

    private fun Preferences.toSettings(): DownloadSettings {
        val defaults = DownloadSettings()
        return DownloadSettings(
            destinationKind = DestinationKind.fromStorage(this[Keys.DESTINATION_KIND]),
            destinationUri = this[Keys.DESTINATION_URI] ?: defaults.destinationUri,
            destinationFolder = this[Keys.DESTINATION_FOLDER] ?: defaults.destinationFolder,
            connectionMode = ConnectionMode.fromStorage(this[Keys.CONNECTION_MODE]),
            numThreads = (this[Keys.NUM_THREADS] ?: defaults.numThreads)
                .coerceIn(1, DownloadSettings.MAX_THREADS),
            smartMaxConnections = (this[Keys.SMART_MAX_CONNECTIONS] ?: defaults.smartMaxConnections)
                .coerceIn(1, 32),
            smartAdaptive = this[Keys.SMART_ADAPTIVE] ?: defaults.smartAdaptive,
            maxConcurrent = (this[Keys.MAX_CONCURRENT] ?: defaults.maxConcurrent)
                .coerceIn(DownloadSettings.MIN_CONCURRENT, DownloadSettings.MAX_CONCURRENT_LIMIT),
            maxSpeedBps = (this[Keys.MAX_SPEED_BPS] ?: defaults.maxSpeedBps).coerceAtLeast(0L),
            duplicatePolicy = DuplicatePolicy.fromStorage(this[Keys.DUPLICATE_POLICY]),
            autoCategorize = this[Keys.AUTO_CATEGORIZE] ?: defaults.autoCategorize,
            startImmediately = this[Keys.START_IMMEDIATELY] ?: defaults.startImmediately,
            resumeOnStartup = this[Keys.RESUME_ON_STARTUP] ?: defaults.resumeOnStartup,
            maxRetries = (this[Keys.MAX_RETRIES] ?: defaults.maxRetries).coerceIn(0, 20),
            retryDelay = (this[Keys.RETRY_DELAY] ?: defaults.retryDelay).coerceIn(1.0, 60.0),
            retryBackoff = (this[Keys.RETRY_BACKOFF] ?: defaults.retryBackoff).coerceIn(1.0, 5.0),
            retryJitter = (this[Keys.RETRY_JITTER] ?: defaults.retryJitter).coerceIn(0.0, 0.5),
            retryMaxDelay = (this[Keys.RETRY_MAX_DELAY] ?: defaults.retryMaxDelay).coerceIn(5.0, 600.0),
            verifySsl = this[Keys.VERIFY_SSL] ?: defaults.verifySsl,
            verifySize = this[Keys.VERIFY_SIZE] ?: defaults.verifySize,
            blockPrivateUrls = this[Keys.BLOCK_PRIVATE_URLS] ?: defaults.blockPrivateUrls,
            notificationsEnabled = this[Keys.NOTIFICATIONS_ENABLED] ?: defaults.notificationsEnabled,
            notifyCompleted = this[Keys.NOTIFY_COMPLETED] ?: defaults.notifyCompleted,
            notifyFailed = this[Keys.NOTIFY_FAILED] ?: defaults.notifyFailed,
            notifyStarted = this[Keys.NOTIFY_STARTED] ?: defaults.notifyStarted,
            userAgent = this[Keys.USER_AGENT] ?: BrowserHeaders.DEFAULT_USER_AGENT,
            themeMode = ThemeMode.fromStorage(this[Keys.THEME_MODE]),
            accentColor = this[Keys.ACCENT_COLOR] ?: defaults.accentColor,
        )
    }

    private object Keys {
        val DESTINATION_KIND = stringPreferencesKey("destination_kind")
        val DESTINATION_URI = stringPreferencesKey("destination_uri")
        val DESTINATION_FOLDER = stringPreferencesKey("destination_folder")
        val CONNECTION_MODE = stringPreferencesKey("connection_mode")
        val NUM_THREADS = intPreferencesKey("num_threads")
        val SMART_MAX_CONNECTIONS = intPreferencesKey("smart_max_connections")
        val SMART_ADAPTIVE = booleanPreferencesKey("smart_adaptive")
        val MAX_CONCURRENT = intPreferencesKey("max_concurrent")
        val MAX_SPEED_BPS = longPreferencesKey("max_speed_bps")
        val DUPLICATE_POLICY = stringPreferencesKey("duplicate_policy")
        val AUTO_CATEGORIZE = booleanPreferencesKey("auto_categorize")
        val START_IMMEDIATELY = booleanPreferencesKey("start_immediately")
        val RESUME_ON_STARTUP = booleanPreferencesKey("resume_on_startup")
        val MAX_RETRIES = intPreferencesKey("max_retries")
        val RETRY_DELAY = doublePreferencesKey("retry_delay")
        val RETRY_BACKOFF = doublePreferencesKey("retry_backoff")
        val RETRY_JITTER = doublePreferencesKey("retry_jitter")
        val RETRY_MAX_DELAY = doublePreferencesKey("retry_max_delay")
        val VERIFY_SSL = booleanPreferencesKey("verify_ssl")
        val VERIFY_SIZE = booleanPreferencesKey("verify_size")
        val BLOCK_PRIVATE_URLS = booleanPreferencesKey("block_private_urls")
        val NOTIFICATIONS_ENABLED = booleanPreferencesKey("notifications_enabled")
        val NOTIFY_COMPLETED = booleanPreferencesKey("notify_completed")
        val NOTIFY_FAILED = booleanPreferencesKey("notify_failed")
        val NOTIFY_STARTED = booleanPreferencesKey("notify_started")
        val USER_AGENT = stringPreferencesKey("user_agent")
        val THEME_MODE = stringPreferencesKey("theme_mode")
        val ACCENT_COLOR = longPreferencesKey("accent_color")
    }
}
