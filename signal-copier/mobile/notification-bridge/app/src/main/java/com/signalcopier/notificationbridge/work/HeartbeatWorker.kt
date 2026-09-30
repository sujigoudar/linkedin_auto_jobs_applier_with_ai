package com.signalcopier.notificationbridge.work

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.signalcopier.notificationbridge.network.NotificationBridgeApi
import com.signalcopier.notificationbridge.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Track 10, Part B point 4: a periodic heartbeat so the server can
 * detect `no_heartbeat_recently` (app/notification_bridge.py's
 * `DeviceHealth`) -- Android restricts always-on background services, so
 * this is a WorkManager PERIODIC request ([WorkScheduler]), not a raw
 * long-lived thread/foreground service.
 *
 * Posts an EMPTY event batch to the same ingest endpoint
 * [com.signalcopier.notificationbridge.work.UploadWorker] uses -- the
 * server's own route treats any successfully authenticated contact as a
 * heartbeat regardless of whether it carries real notification events
 * (see that route's own docstring server-side). This worker does NOT
 * also flush the real notification queue -- that's [UploadWorker]'s own
 * job, enqueued separately and more eagerly (immediately after each
 * capture) -- keeping the two concerns separate means a notification
 * backlog being slow to flush never delays the heartbeat signal the
 * server needs to tell "device offline" apart from "device online but
 * nothing to forward right now."
 */
class HeartbeatWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val settings = SettingsStore(applicationContext)
        if (!settings.isConfigured()) {
            return@withContext Result.success()
        }
        val api = NotificationBridgeApi(settings.serverBaseUrl, settings.deviceId, settings.pairingToken)
        try {
            api.uploadBatch(emptyList())
            Result.success()
        } catch (exc: Exception) {
            settings.lastUploadErrorMessage = exc.message
            Result.retry()
        }
    }

    companion object {
        const val PERIODIC_WORK_NAME = "notification_bridge_heartbeat_periodic"
    }
}
