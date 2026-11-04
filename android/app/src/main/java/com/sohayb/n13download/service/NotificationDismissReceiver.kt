package com.sohayb.n13download.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.sohayb.n13download.N13Application

/**
 * Told when the user swipes a download notification away.
 *
 * Only paused downloads are dismissible — a running transfer's notification is
 * ongoing — so this exists to make the dismissal *stick*.  Without it, the next
 * database emission would happily rebuild the same notification, and a swipe
 * would look broken.
 *
 * A broadcast rather than a service intent on purpose: dismissing a notification
 * is not a reason to start a foreground service.
 */
class NotificationDismissReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val taskId = intent.getLongExtra(DownloadNotifications.EXTRA_TASK_ID, -1L)
        if (taskId <= 0L) return
        val application = context.applicationContext as? N13Application ?: return
        application.container.notificationCenter.onDismissed(taskId)
    }
}
