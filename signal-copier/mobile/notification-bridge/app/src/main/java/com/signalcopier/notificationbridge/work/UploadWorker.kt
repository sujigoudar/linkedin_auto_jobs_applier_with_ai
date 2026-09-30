package com.signalcopier.notificationbridge.work

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.signalcopier.notificationbridge.db.AppDatabase
import com.signalcopier.notificationbridge.network.NotificationBridgeApi
import com.signalcopier.notificationbridge.network.NotificationBridgeApiException
import com.signalcopier.notificationbridge.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Track 10, Part B point 2: flushes the local durable queue
 * ([com.signalcopier.notificationbridge.db.QueuedNotificationEntity]) to
 * the server, with WorkManager's own built-in retry/backoff on failure --
 * a captured notification is marked uploaded ONLY after a genuine 200
 * response names it as processed; anything else (no network, a 401/500,
 * an exception) leaves it in the queue for the NEXT run rather than being
 * silently dropped.
 *
 * Enqueued two ways: once immediately after every capture (see
 * [com.signalcopier.notificationbridge.capture.NotificationBridgeListenerService]),
 * and periodically by [WorkScheduler] as a safety net that eventually
 * flushes the queue even if the immediate attempt was offline when it
 * ran.
 */
class UploadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val settings = SettingsStore(applicationContext)
        if (!settings.isConfigured()) {
            return@withContext Result.success() // nothing to do until paired
        }

        val dao = AppDatabase.get(applicationContext).queuedNotificationDao()
        val batch = dao.pendingBatch(limit = BATCH_SIZE)
        if (batch.isEmpty()) {
            return@withContext Result.success()
        }

        val api = NotificationBridgeApi(settings.serverBaseUrl, settings.deviceId, settings.pairingToken)
        try {
            val results = api.uploadBatch(batch)
            // The server processes events in the SAME order they were
            // submitted (see app/main.py's ingest_notification_bridge) --
            // every entry in `batch` has a corresponding entry in
            // `results` at the same index, whatever its own
            // classification (live/duplicate/needs_review/rejected/
            // stale) turned out to be. A per-event REJECTION
            // (unauthorized_app_package, invalid content_completeness)
            // is still a successful, authoritative answer from the
            // server -- it is marked uploaded, not retried forever,
            // since retrying would never change that verdict.
            if (results.size == batch.size) {
                dao.markUploaded(batch.map { it.id })
                settings.lastSuccessfulUploadAtMillis = System.currentTimeMillis()
                settings.lastUploadErrorMessage = null
            } else {
                // The server's own response didn't line up 1:1 with what
                // was sent -- treat this as a failure rather than
                // guessing which entries succeeded.
                throw NotificationBridgeApiException(
                    "server returned ${results.size} results for a batch of ${batch.size} events"
                )
            }
            dao.pruneUploaded(System.currentTimeMillis() - RETENTION_MILLIS)
            Result.success()
        } catch (exc: NotificationBridgeApiException) {
            batch.forEach { dao.recordUploadFailure(it.id, exc.message ?: "unknown upload error") }
            settings.lastUploadErrorMessage = exc.message
            if (exc.httpStatus == 401) {
                // A bad/rotated pairing token will never succeed on
                // retry without the user re-pairing -- still a real,
                // surfaced failure (settings screen shows
                // lastUploadErrorMessage), but WorkManager should stop
                // hammering the server with the same doomed request.
                Result.failure()
            } else {
                Result.retry()
            }
        } catch (exc: Exception) {
            batch.forEach { dao.recordUploadFailure(it.id, exc.message ?: "unknown error") }
            settings.lastUploadErrorMessage = exc.message
            Result.retry()
        }
    }

    companion object {
        const val IMMEDIATE_WORK_NAME = "notification_bridge_upload_immediate"
        const val PERIODIC_WORK_NAME = "notification_bridge_upload_periodic"
        private const val BATCH_SIZE = 50
        private val RETENTION_MILLIS = java.util.concurrent.TimeUnit.DAYS.toMillis(7)
    }
}
