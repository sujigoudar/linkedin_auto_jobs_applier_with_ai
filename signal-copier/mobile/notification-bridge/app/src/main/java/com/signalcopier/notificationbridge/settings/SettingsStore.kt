package com.signalcopier.notificationbridge.settings

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray

/**
 * Everything the app needs to know to pair with the server and decide
 * which notifications to forward -- entered ONCE by the user through
 * [com.signalcopier.notificationbridge.ui.SettingsActivity], never
 * hardcoded anywhere in this codebase.
 *
 * Deliberately plain [SharedPreferences] rather than Jetpack DataStore or
 * a database: this is a minimal reference implementation and the values
 * here (server base URL, device_id, a small list of package names) are
 * tiny and read far more often than written. The pairing token IS a
 * credential (the per-device bearer token the server issued when the
 * owner registered this device via `POST /notification-bridge/devices`
 * -- see signal-copier's own app/notification_bridge.py) -- stored here
 * in plain SharedPreferences, which is only as protected as Android's own
 * per-app private storage sandbox. A production-hardened build should
 * upgrade this one field to `androidx.security.crypto.EncryptedSharedPreferences`;
 * this reference implementation calls that out explicitly (see
 * README.md's "Known limitations" section) rather than silently
 * pretending it already did that hardening.
 */
class SettingsStore(context: Context) {
    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    var serverBaseUrl: String
        get() = prefs.getString(KEY_SERVER_BASE_URL, "") ?: ""
        set(value) = prefs.edit().putString(KEY_SERVER_BASE_URL, value.trimEnd('/')).apply()

    var deviceId: String
        get() = prefs.getString(KEY_DEVICE_ID, "") ?: ""
        set(value) = prefs.edit().putString(KEY_DEVICE_ID, value).apply()

    /** The raw pairing token, exactly as the owner typed it in -- never
     * transmitted anywhere except as this device's own `Authorization:
     * Bearer <token>` header on every call to the server. The server
     * itself never stores this raw value either (see
     * app/notification_bridge.py's hash_pairing_token) -- this is the
     * ONE place in the whole system the plain token is ever held at
     * rest, by design (the owner is the only party who is ever shown it,
     * once, at registration time, and types it in here themselves). */
    var pairingToken: String
        get() = prefs.getString(KEY_PAIRING_TOKEN, "") ?: ""
        set(value) = prefs.edit().putString(KEY_PAIRING_TOKEN, value).apply()

    /** Which installed app package names this device forwards
     * notifications for -- a checklist/manual-entry list the user
     * configures, matching EXACTLY the app_packages the owner registered
     * this device_id for server-side (see `POST
     * /notification-bridge/devices`). A package not in this set is
     * filtered out client-side before it's even queued -- the server's
     * own `unauthorized_app_package` rejection is the second, independent
     * line of defense, not the only one. */
    var authorizedPackages: Set<String>
        get() {
            val raw = prefs.getString(KEY_AUTHORIZED_PACKAGES, "[]") ?: "[]"
            val array = JSONArray(raw)
            return (0 until array.length()).map { array.getString(it) }.toSet()
        }
        set(value) {
            val array = JSONArray()
            value.forEach { array.put(it) }
            prefs.edit().putString(KEY_AUTHORIZED_PACKAGES, array.toString()).apply()
        }

    /** Set by [com.signalcopier.notificationbridge.work.UploadWorker] on
     * every successful upload -- the settings screen reads this back so
     * the user can see, FROM THE DEVICE ITSELF, that the pairing/capture/
     * upload flow is actually working, per the Track 10 brief's point 5
     * ("a visible last successful upload status"). Never fabricated:
     * only ever written after a real 200 response from the server. */
    var lastSuccessfulUploadAtMillis: Long
        get() = prefs.getLong(KEY_LAST_SUCCESS_AT, -1L)
        set(value) = prefs.edit().putLong(KEY_LAST_SUCCESS_AT, value).apply()

    var lastUploadErrorMessage: String?
        get() = prefs.getString(KEY_LAST_ERROR, null)
        set(value) = prefs.edit().putString(KEY_LAST_ERROR, value).apply()

    fun isConfigured(): Boolean =
        serverBaseUrl.isNotBlank() && deviceId.isNotBlank() && pairingToken.isNotBlank() && authorizedPackages.isNotEmpty()

    companion object {
        private const val PREFS_NAME = "notification_bridge_settings"
        private const val KEY_SERVER_BASE_URL = "server_base_url"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_PAIRING_TOKEN = "pairing_token"
        private const val KEY_AUTHORIZED_PACKAGES = "authorized_packages"
        private const val KEY_LAST_SUCCESS_AT = "last_success_at"
        private const val KEY_LAST_ERROR = "last_error"
    }
}
