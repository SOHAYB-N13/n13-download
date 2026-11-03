package com.sohayb.n13download.ui.permission

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The "ask once" rule for the first-launch notification explanation.
 *
 * This is the part of the permission flow a user actually feels.  Everything else
 * is plumbing that reads a grant or launches a dialog; this decides whether the
 * app speaks up at all.  A bug here is not cosmetic — it is either an app that
 * nags on every launch, or one that never explains itself and silently drops
 * every progress notification.  Each case below is a state the app can genuinely
 * be in on launch, so the table doubles as the specification.
 */
class NotificationPromptPolicyTest {

    private fun shouldPrompt(
        access: NotificationAccess = NotificationAccess.NOT_ASKED,
        askedBefore: Boolean = false,
        notificationsEnabled: Boolean = true,
        updateDialogVisible: Boolean = false,
    ) = NotificationPromptPolicy.shouldPrompt(
        access = access,
        askedBefore = askedBefore,
        notificationsEnabled = notificationsEnabled,
        updateDialogVisible = updateDialogVisible,
    )

    @Test
    fun `first launch without the permission explains and offers the prompt`() {
        assertTrue(shouldPrompt(access = NotificationAccess.NOT_ASKED))
    }

    @Test
    fun `already granted means nothing to explain`() {
        assertFalse(shouldPrompt(access = NotificationAccess.GRANTED))
    }

    @Test
    fun `granted stays silent even on a first launch`() {
        // Android 12 and below report GRANTED with no runtime permission at all;
        // asking there would be a dialog about a permission that does not exist.
        assertFalse(
            shouldPrompt(
                access = NotificationAccess.GRANTED,
                askedBefore = false,
                notificationsEnabled = true,
            ),
        )
    }

    @Test
    fun `declining once is final for automatic prompting`() {
        assertFalse(shouldPrompt(askedBefore = true))
    }

    @Test
    fun `reopening the app after an answer never asks again`() {
        // Every subsequent launch: the answer is recorded, the access may still be
        // NOT_ASKED or DENIED — neither may bring the dialog back.
        assertFalse(shouldPrompt(access = NotificationAccess.NOT_ASKED, askedBefore = true))
        assertFalse(shouldPrompt(access = NotificationAccess.DENIED, askedBefore = true))
        assertFalse(shouldPrompt(access = NotificationAccess.BLOCKED, askedBefore = true))
    }

    @Test
    fun `asked before wins over every other prompt-worthy state`() {
        assertFalse(
            shouldPrompt(
                access = NotificationAccess.NOT_ASKED,
                askedBefore = true,
                notificationsEnabled = true,
                updateDialogVisible = false,
            ),
        )
    }

    @Test
    fun `app-level notifications switched off are not worth a system prompt`() {
        // The OS grant is irrelevant when N13 itself is not allowed to notify.
        assertFalse(shouldPrompt(notificationsEnabled = false))
    }

    @Test
    fun `the update dialog is never stacked on top of`() {
        assertFalse(shouldPrompt(updateDialogVisible = true))
    }

    @Test
    fun `pre-thirteen blocked app still gets one explanation`() {
        // No runtime permission exists below Android 13, so the only way to be
        // "not granted" is the system-level app switch.  The explanation is the
        // one chance to point at it, and it is offered exactly once.
        assertTrue(
            shouldPrompt(
                access = NotificationAccess.BLOCKED,
                askedBefore = false,
                notificationsEnabled = true,
            ),
        )
    }

    @Test
    fun `declined once but the system would still prompt is not re-asked automatically`() {
        assertFalse(shouldPrompt(access = NotificationAccess.DENIED, askedBefore = true))
    }

    @Test
    fun `allow can launch the system prompt only when the OS will show it`() {
        assertTrue(NotificationPromptPolicy.canRequest(NotificationAccess.NOT_ASKED))
        assertTrue(NotificationPromptPolicy.canRequest(NotificationAccess.DENIED))
    }

    @Test
    fun `allow routes to settings when the system prompt is exhausted`() {
        assertFalse(NotificationPromptPolicy.canRequest(NotificationAccess.BLOCKED))
        assertFalse(NotificationPromptPolicy.canRequest(NotificationAccess.GRANTED))
    }
}
