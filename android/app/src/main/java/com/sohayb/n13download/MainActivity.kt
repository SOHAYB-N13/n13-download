package com.sohayb.n13download

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sohayb.n13download.core.AppLocale
import com.sohayb.n13download.ui.navigation.N13Destination
import kotlinx.coroutines.flow.MutableStateFlow

class MainActivity : ComponentActivity() {

    /** Text shared into N13 from another app; consumed by the nav host. */
    private val sharedUrl = MutableStateFlow<String?>(null)

    /** Set when a notification tap should open a specific download. */
    private val pendingTaskId = MutableStateFlow<Long?>(null)

    /** Tab to open first; a deep link or launcher shortcut can override it. */
    private val startTab = MutableStateFlow(N13Destination.START.route)

    /**
     * Applies the chosen language to this Activity's resources.
     *
     * Runs before `onCreate`, so every string — including the window title — is
     * already resolved in the right language, and the configuration carries the
     * right-to-left direction for Persian.
     */
    override fun attachBaseContext(newBase: android.content.Context) {
        super.attachBaseContext(AppLocale.wrap(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // The app is dark-first, so the system bars always need light icons.
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
        )

        handleIntent(intent)

        setContent {
            val shared by sharedUrl.collectAsStateWithLifecycle()
            val taskId by pendingTaskId.collectAsStateWithLifecycle()
            val initialRoute by startTab.collectAsStateWithLifecycle()

            N13App(
                sharedUrl = shared,
                onSharedUrlConsumed = { sharedUrl.value = null },
                pendingTaskId = taskId,
                onPendingTaskConsumed = { pendingTaskId.value = null },
                initialRoute = initialRoute,
            )
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    /**
     * Extracts what another app handed us.
     *
     * Only the first valid http(s) link is taken: a share can carry arbitrary
     * text, and N13 must not act on anything it has not validated.
     */
    private fun handleIntent(intent: Intent?) {
        if (intent == null) return

        intent.getLongExtra(EXTRA_OPEN_TASK_ID, -1L)
            .takeIf { it > 0L }
            ?.let { pendingTaskId.value = it }

        intent.getStringExtra(EXTRA_START_TAB)
            ?.takeIf { it == N13Destination.History.route || it == N13Destination.Settings.route }
            ?.let { startTab.value = it }

        when (intent.action) {
            Intent.ACTION_SEND -> {
                extractLink(intent.getStringExtra(Intent.EXTRA_TEXT))?.let { sharedUrl.value = it }
            }

            Intent.ACTION_SEND_MULTIPLE -> {
                intent.getStringArrayListExtra(Intent.EXTRA_TEXT)
                    ?.firstNotNullOfOrNull { extractLink(it) }
                    ?.let { sharedUrl.value = it }
            }

            else -> Unit
        }
    }

    private fun extractLink(text: String?): String? {
        if (text.isNullOrBlank()) return null
        val candidate = text.trim().split(Regex("\\s+")).firstOrNull { it.startsWith("http") }
            ?: text.trim()
        return candidate.takeIf {
            it.startsWith("http://", ignoreCase = true) || it.startsWith("https://", ignoreCase = true)
        }
    }

    companion object {
        const val EXTRA_OPEN_TASK_ID = "open_task_id"

        /**
         * Opens the app on a specific tab.
         *
         * Used by launcher shortcuts and by the notification for a finished
         * download, so tapping "History" lands on History rather than the
         * downloads list.  Only the two non-default tabs are accepted; anything
         * else falls back to the normal entry point.
         */
        const val EXTRA_START_TAB = "start_tab"
    }
}
