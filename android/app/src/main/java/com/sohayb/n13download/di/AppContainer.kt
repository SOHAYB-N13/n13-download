package com.sohayb.n13download.di

import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat
import com.sohayb.n13download.data.engine.OkHttpDownloadEngine
import com.sohayb.n13download.data.local.N13Database
import com.sohayb.n13download.data.prefs.DataStoreSettingsProvider
import com.sohayb.n13download.data.repository.RoomDownloadRepository
import com.sohayb.n13download.data.storage.DestinationFactoryImpl
import com.sohayb.n13download.domain.download.DownloadEngine
import com.sohayb.n13download.domain.download.DownloadManager
import com.sohayb.n13download.domain.download.DownloadQueue
import com.sohayb.n13download.domain.download.SettingsProvider
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.repository.DownloadRepository
import com.sohayb.n13download.service.DownloadNotifications
import com.sohayb.n13download.service.DownloadService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * Manual dependency container.
 *
 * The whole graph is visible in one place.  Swapping Room for another store, or
 * the OkHttp engine for something else, happens here and nowhere else.
 *
 * The queue is owned by the application, not by an Activity: that is what lets a
 * transfer survive the UI being closed while the foreground service keeps the
 * process alive.
 */
class AppContainer(context: Context) {

    private val appContext: Context = context.applicationContext

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    val settingsProvider: SettingsProvider = DataStoreSettingsProvider(appContext)

    private val database = N13Database.get(appContext)

    val repository: DownloadRepository = RoomDownloadRepository(database.downloadTaskDao())

    val engine: DownloadEngine = OkHttpDownloadEngine()

    val destinations = DestinationFactoryImpl(appContext)

    val notifications = DownloadNotifications(appContext)

    val queue = DownloadQueue(
        repository = repository,
        engine = engine,
        settingsProvider = settingsProvider,
        destinations = destinations,
        workingRoot = destinations.workingRoot(),
        scope = scope,
    )

    val downloadManager = DownloadManager(
        repository = repository,
        engine = engine,
        queue = queue,
        settingsProvider = settingsProvider,
        destinations = destinations,
    )

    /**
     * Latest settings, cached so synchronous callers (the service's notification
     * path, for instance) never have to block on DataStore.
     */
    @Volatile
    private var cachedSettings: DownloadSettings = DownloadSettings()

    fun settingsSnapshot(): DownloadSettings = cachedSettings

    init {
        scope.launch {
            settingsProvider.settings.collect { cachedSettings = it }
        }

        // Recover tasks that were mid-transfer when the process died.
        scope.launch {
            val recovered = downloadManager.recoverAfterRestart()
            if (recovered > 0) {
                android.util.Log.i("N13App", "recovered $recovered interrupted download(s)")
            }
        }

        // Bring the foreground service up as soon as there is real work.
        scope.launch {
            queue.activeCount.collect { active ->
                if (active > 0) startDownloadService()
            }
        }
    }

    /** Brings up the foreground service; harmless when it is already running. */
    fun startDownloadService() {
        runCatching {
            ContextCompat.startForegroundService(
                appContext,
                Intent(appContext, DownloadService::class.java)
                    .setAction(DownloadService::class.java.name),
            )
        }
    }

    fun shutdown() {
        engine.shutdown()
    }
}
