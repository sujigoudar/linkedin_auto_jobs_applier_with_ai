package com.signalcopier.notificationbridge.capture

import android.app.Notification
import android.service.notification.StatusBarNotification
import androidx.core.app.NotificationCompat
import androidx.core.app.Person

/**
 * The extraction rules for turning one `StatusBarNotification` into an
 * [ExtractedContent] -- kept as a small, pure, directly-unit-testable
 * function separate from [NotificationBridgeListenerService]'s own
 * Android-framework-bound `onNotificationPosted` callback, same
 * separation signal-copier's own Python adapters use between event
 * parsing and I/O.
 *
 * Extraction preference order for the "fullest available text" (see
 * [ExtractedContent.expandedText]):
 *
 *  1. `MessagingStyle` messages (`Notification.EXTRA_MESSAGES`, read via
 *     `androidx.core.app.NotificationCompat.MessagingStyle` so this works
 *     across API levels without reading the raw Bundle array by hand) --
 *     the richest, most complete representation for a conversation-style
 *     notification (used by messaging apps and increasingly by trading
 *     bots that post via a chat-style integration).
 *  2. `Notification.EXTRA_BIG_TEXT` (`BigTextStyle`'s expanded text) --
 *     the standard "long form" field Android notifications use when the
 *     short text alone would be truncated in the collapsed notification
 *     shade.
 *  3. `Notification.EXTRA_TEXT_LINES` (`InboxStyle`) -- joined with
 *     newlines.
 *
 * If none of those are present, [ExtractedContent.expandedText] is
 * `null` and only `Notification.EXTRA_TITLE`/`EXTRA_TEXT` are used --
 * see [ExtractedContent]'s own docstring for why this, NOT an
 * optimistic guess, is what decides `completeness`.
 */
object NotificationContentExtractor {
    /** Android's own convention for "this text was elided/truncated in
     * the collapsed view" -- a short text ending in an ellipsis is a
     * real, observable signal that more content existed than what this
     * field alone carries, even when no BigText/MessagingStyle/InboxStyle
     * expansion was ALSO provided (some apps truncate EXTRA_TEXT itself
     * without ever supplying an expanded style). */
    private val TRUNCATION_MARKERS = listOf("…", "...")

    fun extract(sbn: StatusBarNotification): ExtractedContent {
        val notification = sbn.notification
        val extras = notification.extras

        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()?.trim()?.ifBlank { null }
        val shortText = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString()?.trim()?.ifBlank { null }

        val bigText = extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString()?.trim()?.ifBlank { null }
        val textLines = extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)
            ?.mapNotNull { it?.toString()?.trim()?.ifBlank { null } }
            ?.takeIf { it.isNotEmpty() }

        val messagingStyle = runCatching {
            NotificationCompat.MessagingStyle.extractMessagingStyleFromNotification(notification)
        }.getOrNull()

        val messagingText = messagingStyle?.messages
            ?.takeIf { it.isNotEmpty() }
            ?.joinToString("\n") { message ->
                val sender = message.person?.name?.toString() ?: "?"
                "$sender: ${message.text}"
            }

        val participants: List<String> = messagingStyle?.let { style ->
            val names = mutableListOf<String?>()
            style.user.name?.toString()?.let(names::add)
            style.messages.forEach { it.person?.name?.toString()?.let(names::add) }
            names.filterNotNull().distinct()
        } ?: emptyList()

        val isGroup = messagingStyle?.isGroupConversation ?: false

        val expandedText = messagingText ?: bigText ?: textLines?.joinToString("\n")

        val completeness = when {
            expandedText != null -> "complete"
            shortText != null && !looksTruncated(shortText) -> "complete"
            shortText != null -> "truncated"
            title != null -> "title_only"
            else -> "title_only"
        }

        return ExtractedContent(
            title = title,
            shortText = shortText,
            expandedText = expandedText,
            isGroupConversation = isGroup,
            conversationParticipants = participants,
            completeness = completeness,
        )
    }

    private fun looksTruncated(text: String): Boolean = TRUNCATION_MARKERS.any { text.endsWith(it) }
}
