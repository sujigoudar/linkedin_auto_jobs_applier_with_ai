package com.signalcopier.notificationbridge.db

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Update

@Dao
interface QueuedNotificationDao {
    @Insert
    suspend fun insert(entity: QueuedNotificationEntity): Long

    /** Never-yet-uploaded rows, oldest first -- what
     * [com.signalcopier.notificationbridge.work.UploadWorker] batches and
     * sends on each run. Bounded by [limit] so one worker run posts a
     * reasonably sized batch rather than the whole backlog at once. */
    @Query("SELECT * FROM queued_notifications WHERE uploaded = 0 ORDER BY capturedAtEpochMillis ASC LIMIT :limit")
    suspend fun pendingBatch(limit: Int = 50): List<QueuedNotificationEntity>

    @Query("SELECT COUNT(*) FROM queued_notifications WHERE uploaded = 0")
    suspend fun pendingCount(): Int

    @Update
    suspend fun update(entity: QueuedNotificationEntity)

    @Query("UPDATE queued_notifications SET uploaded = 1 WHERE id IN (:ids)")
    suspend fun markUploaded(ids: List<Long>)

    @Query(
        "UPDATE queued_notifications SET uploadAttempts = uploadAttempts + 1, lastUploadErrorMessage = :error " +
            "WHERE id = :id"
    )
    suspend fun recordUploadFailure(id: Long, error: String)

    /** Retention: drop already-uploaded rows older than [olderThanEpochMillis]
     * -- keeps the local queue from growing forever on a device that's
     * been paired for months. Never deletes an un-uploaded row, no matter
     * how old -- see this table's own "never silently drop a captured
     * notification" requirement in the module docstring. */
    @Query("DELETE FROM queued_notifications WHERE uploaded = 1 AND capturedAtEpochMillis < :olderThanEpochMillis")
    suspend fun pruneUploaded(olderThanEpochMillis: Long)
}
