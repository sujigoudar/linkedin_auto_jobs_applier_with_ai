package com.signalcopier.notificationbridge.capture

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import com.signalcopier.notificationbridge.db.AppDatabase
import com.signalcopier.notificationbridge.db.QueuedNotificationEntity
import com.signalcopier.notificationbridge.settings.SettingsStore
import com.signalcopier.notificationbridge.work.UploadWorker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.json.JSONArray

/**
 * Track 10, Part B point 1: captures notifications from ONLY the
 * app_package(s) configured in [SettingsStore.authorizedPackages] (set
 * once, by the user, through the settings screen -- never hardcoded
 * here), extracts the fullest available text via
 * [NotificationContentExtractor], and durably queues it (Room --
 * [AppDatabase]) before handing off to [UploadWorker] for the actual
 * network call.
 *
 * This service NEVER makes the network call itself -- `onNotificationPosted`
 * must return quickly (the system can and does kill a listener service
 * that blocks its callback thread), and a captured notification must
 * survive this PROCESS dying before it's uploaded, not just survive a
 * slow network call inside this callback. Persisting to Room first, then
 * enqueueing a WorkManager one-off request to flush the queue, gives
 * both properties: the notification is safe on disk before this method
 * returns, and the actual upload (with WorkManager's own retry/backoff,
 * see [UploadWorker]) happens on a proper background executor that
 * survives this service being torn down and restarted.
 */
class NotificationBridgeListenerService : NotificationListenerService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onListenerConnected() {
        super.onListenerConnected()
        Log.i(TAG, "notification listener connected")
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        handle(sbn)
    }

    /** An UPDATE to an already-posted notification (same `sbn.key`, new
     * content) fires `onNotificationPosted` again with the SAME key --
     * this is exactly the Android behavior the server's own dedup
     * (`app/notification_bridge.py`'s `content_fingerprint`) is built to
     * recognize as a real edit/revision, not a duplicate. This service
     * does nothing special for that case beyond queuing it like any
     * other post -- the key/content-hash comparison happens server-side
     * (see that module's own docstring), which is the one place that
     * already has the PREVIOUS revision's content to compare against. */
    private fun handle(sbn: StatusBarNotification) {
        val settings = SettingsStore(applicationContext)
        val packageName = sbn.packageName

        if (!settings.isConfigured()) {
            // Not paired yet -- nothing to forward to, and nothing to
            // authorize against. Silently ignored (this is the expected
            // state before the user completes setup), not an error.
            return
        }
        if (packageName !in settings.authorizedPackages) {
            // Client-side filter -- the FIRST line of defense (Part B
            // point 1: "filters to only the configured app_package(s)").
            // The server's own unauthorized_app_package rejection is a
            // second, independent check -- this filter existing does not
            // relax that one.
            return
        }

        val content = NotificationContentExtractor.extract(sbn)
        if (content.isEmpty) {
            // A real notification with genuinely nothing extractable
            // (e.g. a bare progress/media-control notification) -- never
            // queued; there is nothing honest to report even as
            // title_only.
            return
        }

        val entity = QueuedNotificationEntity(
            appPackage = packageName,
            notificationKey = sbn.key,
            postedAtEpochMillis = sbn.postTime,
            title = content.title,
            text = content.shortText,
            expandedText = content.expandedText,
            isGroupConversation = content.isGroupConversation,
            conversationParticipantsJson = if (content.conversationParticipants.isEmpty()) {
                null
            } else {
                JSONArray(content.conversationParticipants).toString()
            },
            contentCompleteness = content.completeness,
            capturedAtEpochMillis = System.currentTimeMillis(),
        )

        scope.launch {
            AppDatabase.get(applicationContext).queuedNotificationDao().insert(entity)
            // Kick an immediate upload attempt (near-real-time forwarding
            // for a fresh signal) in ADDITION to the periodic worker --
            // ExistingWorkPolicy.KEEP so a burst of notifications doesn't
            // enqueue redundant overlapping upload runs.
            WorkManager.getInstance(applicationContext).enqueueUniqueWork(
                UploadWorker.IMMEDIATE_WORK_NAME,
                ExistingWorkPolicy.KEEP,
                OneTimeWorkRequestBuilder<UploadWorker>().build(),
            )
        }
    }

    companion object {
        private const val TAG = "NotificationBridge"
    }
}
