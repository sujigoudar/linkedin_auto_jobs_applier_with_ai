package com.signalcopier.notificationbridge.capture

/**
 * What [NotificationBridgeListenerService] actually pulled out of one
 * `Notification` object -- see that service's own docstring for the
 * extraction rules, and `mobile/notification-bridge/README.md`'s
 * extraction table for the same thing documented for a human reader.
 *
 * `completeness` is the one field this whole bridge's server-side
 * honesty guarantee (signal-copier's `app/notification_bridge.py`
 * `ContentCompleteness`) depends on being accurate: it must reflect
 * EXACTLY what was actually available from the `Notification` object,
 * never optimistically assumed "complete" just because something was
 * extracted.
 */
data class ExtractedContent(
    val title: String?,
    val shortText: String?,
    /** The fullest text this service could extract -- from
     * `Notification.EXTRA_BIG_TEXT`, `MessagingStyle` messages, or
     * `EXTRA_TEXT_LINES`, in that preference order -- NEVER just
     * `shortText` when a richer field was available. This is the exact
     * bug `ItsAzni/NotificationForwarder` (a reference implementation
     * the Track 10 research flagged) has: it reads the expanded text
     * for duplicate detection but only ever enqueues the shorter field,
     * silently losing content. This field is what actually gets queued
     * and uploaded -- see [bestAvailableText]. */
    val expandedText: String?,
    val isGroupConversation: Boolean,
    val conversationParticipants: List<String>,
    val completeness: String, // "complete" | "truncated" | "title_only"
) {
    /** The single text value the queue/upload layer forwards -- always
     * the fullest thing available, per this class's own docstring. */
    val bestAvailableText: String?
        get() = expandedText ?: shortText ?: title

    /** Nothing at all was extractable (no title, no text, no expansion)
     * -- e.g. a pure progress/media-control notification with no words.
     * Such a notification is never queued at all (see
     * NotificationBridgeListenerService.onNotificationPosted) -- there is
     * nothing honest to report even under `title_only`. */
    val isEmpty: Boolean
        get() = title.isNullOrBlank() && shortText.isNullOrBlank() && expandedText.isNullOrBlank()
}
