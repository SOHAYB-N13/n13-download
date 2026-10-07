package com.sohayb.n13download.ui.navigation

import android.net.Uri
import androidx.annotation.StringRes
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.sohayb.n13download.R
import com.sohayb.n13download.di.AppContainer
import com.sohayb.n13download.ui.components.N13BottomBar
import com.sohayb.n13download.ui.components.N13Icons
import com.sohayb.n13download.ui.screens.adddownload.AddDownloadScreen
import com.sohayb.n13download.ui.screens.details.DetailsScreen
import com.sohayb.n13download.ui.screens.downloads.DownloadsScreen
import com.sohayb.n13download.ui.screens.history.HistoryScreen
import com.sohayb.n13download.ui.screens.settings.SettingsScreen
import com.sohayb.n13download.ui.theme.LocalN13Colors

/**
 * Every screen the app can show.
 *
 * The first entry is the start destination, so N13 opens on Downloads — the same
 * place the Windows application starts.
 */
enum class N13Destination(
    val route: String,
    @param:StringRes val labelRes: Int,
    val icon: ImageVector,
) {
    Downloads("downloads", R.string.nav_downloads, N13Icons.Download),
    History("history", R.string.nav_history, N13Icons.History),
    Settings("settings", R.string.nav_settings, N13Icons.Settings),
    ;

    companion object {
        val START: N13Destination = Downloads

        /** Routes that show the bottom bar; the full-screen flows do not. */
        val withBottomBar: Set<String> = entries.map { it.route }.toSet()
    }
}

/** Full-screen flows, presented without the bottom bar. */
object N13Routes {
    const val ADD_DOWNLOAD = "add_download?url={url}"
    const val DETAILS = "details/{taskId}"

    fun addDownload(url: String? = null): String =
        "add_download?url=" + Uri.encode(url.orEmpty())

    fun details(taskId: Long): String = "details/$taskId"

    const val ARG_URL = "url"
    const val ARG_TASK_ID = "taskId"
}

private const val ENTER_MILLIS = 200
private const val EXIT_MILLIS = 150

/**
 * App shell: one Scaffold with the bottom bar and a [NavHost] that starts on
 * Downloads.  Transitions are short — enough to feel smooth, not enough to
 * notice.
 */
@Composable
fun N13NavHost(
    container: AppContainer,
    sharedUrl: String?,
    onSharedUrlConsumed: () -> Unit,
    pendingTaskId: Long?,
    onPendingTaskConsumed: () -> Unit,
    /** Tab to open on first composition; defaults to the downloads list. */
    initialRoute: String = N13Destination.START.route,
    modifier: Modifier = Modifier,
) {
    val navController = rememberNavController()
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route
    val showBottomBar = currentRoute in N13Destination.withBottomBar

    // A URL shared into N13 (Chrome -> Share -> N13) opens Add Download.
    LaunchedEffect(sharedUrl) {
        if (!sharedUrl.isNullOrBlank()) {
            navController.navigate(N13Routes.addDownload(sharedUrl))
            onSharedUrlConsumed()
        }
    }

    // A notification tap opens that download's properties.
    LaunchedEffect(pendingTaskId) {
        if (pendingTaskId != null && pendingTaskId > 0L) {
            navController.navigate(N13Routes.details(pendingTaskId))
            onPendingTaskConsumed()
        }
    }

    Scaffold(
        modifier = modifier.fillMaxSize(),
        containerColor = LocalN13Colors.current.background,
        bottomBar = {
            if (showBottomBar) {
                N13BottomBar(
                    currentRoute = currentRoute,
                    onSelect = { destination ->
                        if (destination.route == currentRoute) return@N13BottomBar
                        navController.navigate(destination.route) {
                            popUpTo(navController.graph.findStartDestination().id) { saveState = true }
                            launchSingleTop = true
                            restoreState = true
                        }
                    },
                )
            }
        },
    ) { innerPadding ->
        NavHost(
            navController = navController,
            startDestination = initialRoute,
            modifier = Modifier.padding(
                top = innerPadding.calculateTopPadding(),
                bottom = if (showBottomBar) innerPadding.calculateBottomPadding() else 0.dp,
            ),
            enterTransition = { fadeIn(tween(ENTER_MILLIS)) },
            exitTransition = { fadeOut(tween(EXIT_MILLIS)) },
            popEnterTransition = { fadeIn(tween(ENTER_MILLIS)) },
            popExitTransition = { fadeOut(tween(EXIT_MILLIS)) },
        ) {
            composable(N13Destination.Downloads.route) {
                DownloadsScreen(
                    manager = container.downloadManager,
                    onAddDownload = { navController.navigate(N13Routes.addDownload()) },
                    onOpenTask = { id -> navController.navigate(N13Routes.details(id)) },
                    onOpenHistory = {
                        navController.navigate(N13Destination.History.route) {
                            launchSingleTop = true
                        }
                    },
                )
            }

            composable(N13Destination.History.route) {
                HistoryScreen(
                    manager = container.downloadManager,
                    onOpenTask = { id -> navController.navigate(N13Routes.details(id)) },
                    onOpenDownloads = {
                        navController.navigate(N13Destination.Downloads.route) {
                            launchSingleTop = true
                        }
                    },
                )
            }

            composable(N13Destination.Settings.route) {
                SettingsScreen(manager = container.downloadManager)
            }

            composable(
                route = N13Routes.ADD_DOWNLOAD,
                arguments = listOf(
                    navArgument(N13Routes.ARG_URL) {
                        type = NavType.StringType
                        defaultValue = ""
                    },
                ),
                enterTransition = { slideInHorizontally(tween(ENTER_MILLIS)) { it / 4 } + fadeIn(tween(ENTER_MILLIS)) },
                popExitTransition = { slideOutHorizontally(tween(EXIT_MILLIS)) { it / 4 } + fadeOut(tween(EXIT_MILLIS)) },
            ) { entry ->
                val prefill = entry.arguments?.getString(N13Routes.ARG_URL)?.takeIf { it.isNotBlank() }
                AddDownloadScreen(
                    manager = container.downloadManager,
                    prefillUrl = prefill,
                    onDone = {
                        navController.popBackStack()
                        navController.navigate(N13Destination.Downloads.route) {
                            launchSingleTop = true
                        }
                    },
                    onCancel = { navController.popBackStack() },
                )
            }

            composable(
                route = N13Routes.DETAILS,
                arguments = listOf(
                    navArgument(N13Routes.ARG_TASK_ID) { type = NavType.LongType },
                ),
                enterTransition = { slideInHorizontally(tween(ENTER_MILLIS)) { it / 4 } + fadeIn(tween(ENTER_MILLIS)) },
                popExitTransition = { slideOutHorizontally(tween(EXIT_MILLIS)) { it / 4 } + fadeOut(tween(EXIT_MILLIS)) },
            ) { entry ->
                DetailsScreen(
                    manager = container.downloadManager,
                    taskId = entry.arguments?.getLong(N13Routes.ARG_TASK_ID) ?: 0L,
                    onBack = { navController.popBackStack() },
                )
            }
        }
    }
}
