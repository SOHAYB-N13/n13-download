package com.sohayb.n13download

import android.app.Application
import android.util.Log
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
