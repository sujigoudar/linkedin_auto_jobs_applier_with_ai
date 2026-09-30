package com.signalcopier.notificationbridge.work

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkRequest
import java.util.concurrent.TimeUnit

/**
 * Schedules the two periodic background workers -- called once from
 * [com.signalcopier.notificationbridge.ui.SettingsActivity] after the
 * user finishes pairing (and it's safe/idempotent to call again any time
 * settings change; `ExistingPeriodicWorkPolicy.UPDATE` replaces the prior
 * request rather than stacking a second one).
 */
object WorkScheduler {
    /** Android's own WorkManager minimum for PeriodicWorkRequest. A
     * shorter true heartbeat interval isn't available through this API --
     * documented here rather than silently promising something tighter
     * (see README.md's "Known limitations"). */
    private val UPLOAD_INTERVAL_MINUTES = 15L
    private val HEARTBEAT_INTERVAL_MINUTES = 15L

    fun scheduleAll(context: Context) {
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()

        val uploadRequest = PeriodicWorkRequestBuilder<UploadWorker>(UPLOAD_INTERVAL_MINUTES, TimeUnit.MINUTES)
            .setConstraints(constraints)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, WorkRequest.MIN_BACKOFF_MILLIS, TimeUnit.MILLISECONDS)
            .build()

        val heartbeatRequest = PeriodicWorkRequestBuilder<HeartbeatWorker>(HEARTBEAT_INTERVAL_MINUTES, TimeUnit.MINUTES)
            .setConstraints(constraints)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, WorkRequest.MIN_BACKOFF_MILLIS, TimeUnit.MILLISECONDS)
            .build()

        val manager = WorkManager.getInstance(context)
        manager.enqueueUniquePeriodicWork(
            UploadWorker.PERIODIC_WORK_NAME, ExistingPeriodicWorkPolicy.UPDATE, uploadRequest
        )
        manager.enqueueUniquePeriodicWork(
            HeartbeatWorker.PERIODIC_WORK_NAME, ExistingPeriodicWorkPolicy.UPDATE, heartbeatRequest
        )
    }

    fun cancelAll(context: Context) {
        val manager = WorkManager.getInstance(context)
        manager.cancelUniqueWork(UploadWorker.PERIODIC_WORK_NAME)
        manager.cancelUniqueWork(HeartbeatWorker.PERIODIC_WORK_NAME)
    }
}
