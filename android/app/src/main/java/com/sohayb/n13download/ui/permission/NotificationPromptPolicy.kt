package com.sohayb.n13download.ui.permission

/**
 * Whether the first-launch notification explanation should be shown.
 *
 * Kept as a pure function — no Compose, no `Context` — because this is the part
 * of the flow that decides whether the user is asked once, never, or again, and
 * getting it wrong is exactly how an app becomes the one that nags on every
 * launch.  Everything Android-specific (reading the grant, launching the system
 * prompt, opening settings) lives in [rememberNotificationPermission]; this only
 * answers the policy question, so it can be tested on the JVM for every state.
 *
 * The rules, in order:
 *
 *  - **[askedBefore]** wins over everything. Once N13 has shown the explanation
 *    and the user answered — either way — it is never shown again automatically.
 *    Settings keeps a permanent route back, which is the honest place for a
 *    second attempt.
 *  - **Notifications switched off in the app** means the OS grant is irrelevant,
 *    so asking for it would be noise.
 *  - **Already granted** means there is nothing to explain.
 *  - **An update dialog on screen** takes precedence, so two dialogs can never
 *    stack on the first launch after a release.
 *
 * Everything else — never asked on Android 13+, and the pre-13 case where the
 * user has notifications switched off in the system settings — is worth one
 * explanation.
 */
object NotificationPromptPolicy {

    fun shouldPrompt(
        access: NotificationAccess,
        askedBefore: Boolean,
        notificationsEnabled: Boolean,
        updateDialogVisible: Boolean,
    ): Boolean {
        if (askedBefore) return false
        if (!notificationsEnabled) return false
        if (access == NotificationAccess.GRANTED) return false
        if (updateDialogVisible) return false
        return true
    }

    /**
     * Whether the "Allow" button can launch the system prompt itself.
     *
     * False when the OS will not show it any more (blocked, or permanently
     * denied); the caller then has to send the user to the app's notification
     * settings instead of tapping a button that does nothing.
     */
    fun canRequest(access: NotificationAccess): Boolean =
        access == NotificationAccess.NOT_ASKED || access == NotificationAccess.DENIED
}
