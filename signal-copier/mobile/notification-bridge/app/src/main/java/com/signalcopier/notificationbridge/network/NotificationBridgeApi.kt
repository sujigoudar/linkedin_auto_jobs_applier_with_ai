package com.signalcopier.notificationbridge.network

import com.signalcopier.notificationbridge.db.QueuedNotificationEntity
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.TimeUnit

/**
 * The thin HTTP client for `POST /ingest/notification-bridge/{device_id}`
 * -- signal-copier's own `app/main.py` route. Deliberately plain OkHttp +
 * `org.json` rather than Retrofit/Moshi/kotlinx.serialization: this is a
 * small, fixed request/response shape and pulling in a full REST client
 * stack would be disproportionate for one endpoint.
 */
class NotificationBridgeApi(
    private val serverBaseUrl: String,
    private val deviceId: String,
    private val pairingToken: String,
) {
    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .writeTimeout(15, TimeUnit.SECONDS)
        .build()

    private val isoFormat = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US).apply {
        timeZone = TimeZone.getTimeZone("UTC")
    }

    /**
     * Uploads a batch of already-queued notifications (or an EMPTY batch,
     * used by [com.signalcopier.notificationbridge.work.HeartbeatWorker]
     * purely to register a heartbeat -- see that route's own docstring
     * server-side for why any authenticated contact counts as one).
     *
     * Returns the parsed `results` array (one entry per submitted event,
     * in the SAME order) on a 200 response, or throws
     * [NotificationBridgeApiException] for anything else (auth failure,
     * network error, non-200 status) -- the caller (UploadWorker) decides
     * retry/backoff policy from there. This method NEVER silently treats
     * a failed upload as successful.
     */
    fun uploadBatch(events: List<QueuedNotificationEntity>): List<JSONObject> {
        val eventsArray = JSONArray()
        events.forEach { entity ->
            val event = JSONObject()
            event.put("app_package", entity.appPackage)
            event.put("notification_key", entity.notificationKey)
            event.put("posted_at", isoFormat.format(Date(entity.postedAtEpochMillis)))
            event.put("title", entity.title ?: JSONObject.NULL)
            event.put("text", entity.text ?: JSONObject.NULL)
            event.put("expanded_text", entity.expandedText ?: JSONObject.NULL)
            event.put("is_group_conversation", entity.isGroupConversation)
            if (entity.conversationParticipantsJson != null) {
                event.put("conversation_participants", JSONArray(entity.conversationParticipantsJson))
            }
            event.put("content_completeness", entity.contentCompleteness)
            event.put("received_at", isoFormat.format(Date(entity.capturedAtEpochMillis)))
            eventsArray.put(event)
        }
        val body = JSONObject().put("events", eventsArray).toString()

        val request = Request.Builder()
            .url("$serverBaseUrl/ingest/notification-bridge/$deviceId")
            .addHeader("Authorization", "Bearer $pairingToken")
            .post(body.toRequestBody("application/json".toMediaType()))
            .build()

        client.newCall(request).execute().use { response ->
            val responseBody = response.body?.string() ?: ""
            if (!response.isSuccessful) {
                throw NotificationBridgeApiException(
                    "upload failed: HTTP ${response.code} -- $responseBody",
                    httpStatus = response.code,
                )
            }
            val parsed = JSONObject(responseBody)
            val results = parsed.optJSONArray("results") ?: JSONArray()
            return (0 until results.length()).map { results.getJSONObject(it) }
        }
    }
}

class NotificationBridgeApiException(message: String, val httpStatus: Int? = null) : Exception(message)
