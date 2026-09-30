package com.signalcopier.notificationbridge.db

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * One captured notification, durably queued until it's successfully
 * uploaded (Track 10 brief, Part B point 2: "a captured notification
 * must survive a network outage/app restart before successful upload").
 *
 * [notificationKey] is Android's own `StatusBarNotification.key` -- the
 * stable idempotency key the server dedups on (Part B point 3) via the
 * same field name / meaning as the server's own
 * `app/notification_bridge.py` module docstring describes. This row's
 * own [id] (an app-local autoincrement) is NEVER sent to the server --
 * it exists purely to let this device track queue/retry state locally.
 */
@Entity(tableName = "queued_notifications")
data class QueuedNotificationEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val appPackage: String,
    val notificationKey: String,
    val postedAtEpochMillis: Long,
    val title: String?,
    val text: String?,
    val expandedText: String?,
    val isGroupConversation: Boolean,
    /** JSON-encoded list of participant names, or null -- see
     * [com.signalcopier.notificationbridge.capture.NotificationBridgeListenerService]
     * for how a `MessagingStyle` conversation's participants are
     * extracted. */
    val conversationParticipantsJson: String?,
    /** One of "complete" / "truncated" / "title_only" -- see
     * NotificationBridgeListenerService's own docstring for exactly how
     * this is decided from which `Notification` fields were actually
     * populated. Never guessed here in the queue/upload layer -- this
     * column only ever stores whatever the capture layer already
     * decided. */
    val contentCompleteness: String,
    val capturedAtEpochMillis: Long,
    /** How many upload attempts have already failed for this row -- see
     * [com.signalcopier.notificationbridge.work.UploadWorker]'s own
     * backoff policy. */
    val uploadAttempts: Int = 0,
    val lastUploadErrorMessage: String? = null,
    val uploaded: Boolean = false,
)
