package com.sohayb.n13download

import android.app.Application
import android.util.Log
import com.sohayb.n13download.core.AppLocale
import com.sohayb.n13download.di.AppContainer

/**
 * Application entry point.
 *
 * Builds the dependency graph and lets the container recover interrupted
 * downloads and start the foreground service when work exists.
 */
class N13Application : Application() {

    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        installCrashLogger()
        container = AppContainer(this)
        seedLanguage()
    }

    /**
     * Reads the persisted language before any Activity is created.
     *
     * `attachBaseContext` cannot suspend, so the choice has to be in memory by
     * the time the first Activity starts.  This is a single small DataStore read
     * at process start — the only blocking call in the startup path, and the
     * price of a locale that is correct on the very first frame instead of
     * flashing the wrong language and then recreating.
     */
    private fun seedLanguage() {
        val language = runCatching {
            kotlinx.coroutines.runBlocking { container.settingsProvider.current().language }
        }.getOrDefault(com.sohayb.n13download.domain.model.AppLanguage.DEFAULT)
        AppLocale.seed(language)
    }

    override fun onTerminate() {
        container.shutdown()
        super.onTerminate()
    }

    /**
     * Logs an uncaught exception before the process goes down.
     *
     * Some OEM builds (MIUI among them) suppress the platform's own crash log, and
     * without this a release build would die silently with nothing to go on.
     */
    private fun installCrashLogger() {
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            Log.e(TAG, "uncaught exception on thread '${thread.name}'", throwable)
            previous?.uncaughtException(thread, throwable)
        }
    }

    private companion object {
        const val TAG = "N13Crash"
    }
}
