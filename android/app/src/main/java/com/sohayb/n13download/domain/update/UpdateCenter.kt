package com.sohayb.n13download.domain.update

import com.sohayb.n13download.domain.download.SettingsProvider
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Owns the update check: when it runs, and what the UI should offer.
 *
 * Application-scoped (not per-screen) so the check runs once per process no
 * matter how often the UI recomposes or how many Activities are created, and so
 * "Later" survives an Activity recreation.
 *
 * The check is a single fire-and-forget coroutine kicked off at startup. It never
 * blocks, and every failure path ends in "no update" rather than an error.
 */
class UpdateCenter(
    private val repository: UpdateRepository,
    private val settingsProvider: SettingsProvider,
    private val installedVersionCode: Int,
    private val installedVersionName: String,
    private val scope: CoroutineScope,
    /** Injectable for tests. */
    private val now: () -> Long = System::currentTimeMillis,
) {

    private val _update = MutableStateFlow<AppUpdate?>(null)

    /** The update to offer, or null when there is nothing to show. */
    val update: StateFlow<AppUpdate?> = _update.asStateFlow()

    /** Guarantees one attempt per process, whatever calls [start]. */
    private val started = AtomicBoolean(false)

    /** Set when the user chose "Later", so the dialog is not shown again this session. */
    @Volatile
    private var dismissedThisSession = false

    /** Runs the check at most once per process. Safe to call from anywhere. */
    fun start() {
        if (!started.compareAndSet(false, true)) return
        scope.launch {
            try {
                check()
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (_: Exception) {
                // Defence in depth: the repository already reports failure as
                // null, but an update check must never be able to take the app
                // down, so nothing escapes this coroutine.
            }
        }
    }

    /** The user chose "Later". */
    fun dismiss() {
        dismissedThisSession = true
        _update.value = null
    }

    private suspend fun check() {
        val settings = settingsProvider.current()

        // ~24h between checks. A negative elapsed time means the device clock
        // moved backwards, which is treated as "check now" rather than as a
        // reason to stay silent.
        val elapsed = now() - settings.lastUpdateCheckAt
        if (elapsed in 0 until CHECK_INTERVAL_MILLIS) return

        val release = repository.latestAndroidRelease() ?: return

        // Only record the timestamp once we actually reached GitHub, so a
        // transient failure retries on the next launch instead of going quiet
        // for a day.
        settingsProvider.update(
            settingsProvider.current().copy(lastUpdateCheckAt = now()),
        )

        if (dismissedThisSession) return
        if (!UpdateChecker.isUpdateAvailable(installedVersionCode, installedVersionName, release)) {
            return
        }

        val versionName = UpdateChecker.versionNameOf(release) ?: return
        _update.value = AppUpdate(versionName = versionName, releaseUrl = release.htmlUrl)
    }

    private companion object {
        const val CHECK_INTERVAL_MILLIS = 24L * 60L * 60L * 1000L
    }
}
