package com.sohayb.n13download.ui.screens.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.sohayb.n13download.domain.download.DownloadManager
import com.sohayb.n13download.domain.model.AppLanguage
import com.sohayb.n13download.domain.model.ConnectionMode
import com.sohayb.n13download.domain.model.DestinationKind
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.model.DuplicatePolicy
import com.sohayb.n13download.domain.model.ThemeMode
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/**
 * State holder for Settings.
 *
 * Every setter writes straight through to DataStore and the queue picks the
 * change up on the next tick, so there is no "apply" step and no setting that
 * only takes effect after a restart.
 */
class SettingsViewModel(
    private val manager: DownloadManager,
) : ViewModel() {

    val settings: StateFlow<DownloadSettings> = manager.observeSettings()
        .stateIn(
            scope = viewModelScope,
            started = SharingStarted.WhileSubscribed(STOP_TIMEOUT_MILLIS),
            initialValue = DownloadSettings(),
        )

    fun update(transform: (DownloadSettings) -> DownloadSettings) {
        viewModelScope.launch {
            manager.updateSettings(transform(manager.settings()))
        }
    }

    fun setDestinationKind(kind: DestinationKind) = update { it.copy(destinationKind = kind) }

    fun setDestinationFolder(folder: String) = update { it.copy(destinationFolder = folder) }

    fun setTreeUri(uri: String) = update {
        it.copy(destinationKind = DestinationKind.TREE, destinationUri = uri)
    }

    fun setMaxConcurrent(value: Int) = update {
        it.copy(maxConcurrent = value.coerceIn(DownloadSettings.MIN_CONCURRENT, DownloadSettings.MAX_CONCURRENT_LIMIT))
    }

    fun setNumThreads(value: Int) = update { it.copy(numThreads = value.coerceIn(1, DownloadSettings.MAX_THREADS)) }

    fun setConnectionMode(mode: ConnectionMode) = update { it.copy(connectionMode = mode) }

    fun setSmartMax(value: Int) = update { it.copy(smartMaxConnections = value.coerceIn(1, 32)) }

    fun setSmartAdaptive(value: Boolean) = update { it.copy(smartAdaptive = value) }

    fun setSpeedLimit(bytesPerSecond: Long) = update { it.copy(maxSpeedBps = bytesPerSecond.coerceAtLeast(0L)) }

    fun setDuplicatePolicy(policy: DuplicatePolicy) = update { it.copy(duplicatePolicy = policy) }

    fun setAutoCategorize(value: Boolean) = update { it.copy(autoCategorize = value) }

    fun setStartImmediately(value: Boolean) = update { it.copy(startImmediately = value) }

    fun setResumeOnStartup(value: Boolean) = update { it.copy(resumeOnStartup = value) }

    fun setMaxRetries(value: Int) = update { it.copy(maxRetries = value.coerceIn(0, 20)) }

    fun setVerifySsl(value: Boolean) = update { it.copy(verifySsl = value) }

    fun setVerifySize(value: Boolean) = update { it.copy(verifySize = value) }

    fun setBlockPrivateUrls(value: Boolean) = update { it.copy(blockPrivateUrls = value) }

    fun setNotificationsEnabled(value: Boolean) = update { it.copy(notificationsEnabled = value) }

    fun setNotifyCompleted(value: Boolean) = update { it.copy(notifyCompleted = value) }

    fun setNotifyFailed(value: Boolean) = update { it.copy(notifyFailed = value) }

    fun setNotifyStarted(value: Boolean) = update { it.copy(notifyStarted = value) }

    fun setThemeMode(mode: ThemeMode) = update { it.copy(themeMode = mode) }

    fun setAccentColor(argb: Long) = update { it.copy(accentColor = argb) }

    /**
     * Persists the chosen interface language.
     *
     * Applying it — and rebuilding the UI — is the caller's job, because that
     * needs the Activity. Splitting it this way keeps the ViewModel free of
     * Android UI types and keeps the persistence path the same as every other
     * setting.
     */
    fun setLanguage(language: AppLanguage) = update { it.copy(language = language) }

    fun resetUserAgent() = update {
        it.copy(userAgent = com.sohayb.n13download.core.BrowserHeaders.DEFAULT_USER_AGENT)
    }

    fun setUserAgent(value: String) = update { it.copy(userAgent = value) }

    companion object {
        private const val STOP_TIMEOUT_MILLIS = 5_000L

        fun factory(manager: DownloadManager): ViewModelProvider.Factory = viewModelFactory {
            initializer { SettingsViewModel(manager) }
        }
    }
}
