package com.sohayb.n13download.di

import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat
import com.sohayb.n13download.core.appVersionInfo
import com.sohayb.n13download.data.engine.OkHttpDownloadEngine
import com.sohayb.n13download.data.local.N13Database
import com.sohayb.n13download.data.prefs.DataStoreSettingsProvider
import com.sohayb.n13download.data.repository.RoomDownloadRepository
import com.sohayb.n13download.data.storage.DestinationFactoryImpl
import com.sohayb.n13download.data.update.GitHubUpdateRepository
import com.sohayb.n13download.domain.download.DownloadEngine
import com.sohayb.n13download.domain.download.DownloadManager
import com.sohayb.n13download.domain.download.DownloadQueue
import com.sohayb.n13download.domain.download.SettingsProvider
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.repository.DownloadRepository
import com.sohayb.n13download.domain.update.UpdateCenter
import com.sohayb.n13download.service.DownloadNotificationCenter
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

    /**
     * Latest settings, cached so synchronous callers (the service's notification
     * path, for instance) never have to block on DataStore.
     */
    @Volatile
    private var cachedSettings: DownloadSettings = DownloadSettings()

    fun settingsSnapshot(): DownloadSettings = cachedSettings

    /**
     * The single owner of the notification shade.
     *
     * Application-scoped, not service-scoped, for two reasons: it must remember
     * what it has posted across a service stop and start (the service stops as
     * soon as nothing is running, but paused notifications stay), and the
     * dismissal receiver has to reach it without a service being alive.
     */
    val notificationCenter = DownloadNotificationCenter(
        context = appContext,
        settings = { settingsSnapshot() },
    )

    val queue = DownloadQueue(
        repository = repository,
        engine = engine,
        settingsProvider = settingsProvider,
        destinations = destinations,
        workingRoot = destinations.workingRoot(),
        scope = scope,
        // Removing a download must take its notification with it, even when the
        // centre has never seen the task — which is exactly the case after the
        // process was restarted while a finished notification was still on screen.
        onTaskRemoved = { notificationCenter.forget(it) },
        onHistoryCleared = { notificationCenter.forgetFinished() },
    )

    val downloadManager = DownloadManager(
        repository = repository,
        engine = engine,
        queue = queue,
        settingsProvider = settingsProvider,
        destinations = destinations,
    )

    /**
     * Checks GitHub for a newer Android release.
     *
     * Application-scoped so the check happens once per process regardless of how
     * often the UI recomposes, and so a dismissed dialog stays dismissed across
     * Activity recreations. It never blocks startup: the work happens on [scope].
     */
    val updateCenter = UpdateCenter(
        repository = GitHubUpdateRepository(settingsProvider),
        settingsProvider = settingsProvider,
        installedVersionCode = appContext.appVersionInfo().versionCode,
        installedVersionName = appContext.appVersionInfo().versionName,
        scope = scope,
    )

    init {
        // Empty the shade before anything else can post to it.
        //
        // Notifications survive a process death; the app's knowledge of them does
        // not, and neither the system (`getActiveNotifications()` returns nothing
        // on MIUI) nor a persisted record can say which of them are still true.
        // Starting from an empty shade and rebuilding it from the rows in the
        // same pass is exact, and it also clears leftovers from versions that
        // kept no records at all.  Runs synchronously and first, so it can never
        // race the foreground service — `Application.onCreate` always precedes
        // `Service.onCreate`.
        runCatching { notificationCenter.resetShade() }.onFailure {
            android.util.Log.w("N13App", "notification reset failed", it)
        }

        scope.launch {
            settingsProvider.settings.collect { cachedSettings = it }
        }

        // Look for a newer Android release. Fire-and-forget by design.
        updateCenter.start()

        // Recover tasks that were mid-transfer when the process died.
        scope.launch {
            val recovered = downloadManager.recoverAfterRestart()
            if (recovered > 0) {
                android.util.Log.i("N13App", "recovered $recovered interrupted download(s)")
            }

            // Rebuild the shade *after* recovery, from the persisted rows: a
            // process killed mid-transfer leaves progress notifications claiming
            // downloads that are no longer running, and they must not survive the
            // restart.  Doing it here rather than waiting for the service is what
            // makes that true even when nothing is downloading.
            runCatching {
                notificationCenter.render(repository.allNow())
            }.onFailure {
                android.util.Log.w("N13App", "notification reconcile failed", it)
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
