package com.signalcopier.notificationbridge.ui

import android.content.Intent
import android.content.pm.ApplicationInfo
import android.os.Bundle
import android.provider.Settings
import android.text.format.DateUtils
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.NotificationManagerCompat
import com.signalcopier.notificationbridge.databinding.ActivitySettingsBinding
import com.signalcopier.notificationbridge.settings.SettingsStore
import com.signalcopier.notificationbridge.work.WorkScheduler

/**
 * Track 10, Part B point 5: "clear, minimal settings UI: pairing token
 * entry ..., app_package selection ..., a visible 'last successful
 * upload' status." This activity is the ONLY place any of
 * [SettingsStore]'s values are ever written -- the pairing token, device
 * id, and authorized app packages all come from what the user types or
 * picks here, never from a hardcoded constant anywhere in this app.
 */
class SettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySettingsBinding
    private lateinit var settings: SettingsStore

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)
        settings = SettingsStore(this)

        loadCurrentSettingsIntoFields()
        refreshStatus()

        binding.openNotificationAccessSettingsButton.setOnClickListener {
            // SEC / point 6 of the README's own requirement: there is NO
            // programmatic way to grant NotificationListenerService
            // access -- this intent only OPENS the system settings screen
            // where the user must flip it on by hand.
            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
        }

        binding.pickInstalledAppsButton.setOnClickListener { showInstalledAppsPicker() }

        binding.saveButton.setOnClickListener { saveSettings() }
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
    }

    private fun loadCurrentSettingsIntoFields() {
        binding.serverBaseUrlInput.setText(settings.serverBaseUrl)
        binding.deviceIdInput.setText(settings.deviceId)
        binding.pairingTokenInput.setText(settings.pairingToken)
        binding.appPackagesInput.setText(settings.authorizedPackages.joinToString(", "))
    }

    private fun refreshStatus() {
        val enabledListeners = NotificationManagerCompat.getEnabledListenerPackages(this)
        val granted = packageName in enabledListeners
        binding.listenerAccessStatus.text = getString(
            if (granted) {
                com.signalcopier.notificationbridge.R.string.notification_access_granted
            } else {
                com.signalcopier.notificationbridge.R.string.notification_access_not_granted
            }
        )

        val lastUpload = settings.lastSuccessfulUploadAtMillis
        binding.lastUploadStatus.text = if (lastUpload <= 0) {
            getString(com.signalcopier.notificationbridge.R.string.no_upload_yet)
        } else {
            val relative = DateUtils.getRelativeTimeSpanString(lastUpload)
            getString(com.signalcopier.notificationbridge.R.string.last_upload_at, relative)
        }

        val lastError = settings.lastUploadErrorMessage
        binding.lastErrorStatus.text = if (lastError.isNullOrBlank()) {
            ""
        } else {
            getString(com.signalcopier.notificationbridge.R.string.last_error, lastError)
        }
    }

    /** A simple multi-select dialog over every currently-installed
     * launchable app (label + package name) -- an alternative to typing
     * package names by hand into [ActivitySettingsBinding.appPackagesInput].
     * Selections are merged into (not replacing) the manual text field so
     * both entry methods compose. */
    private fun showInstalledAppsPicker() {
        val pm = packageManager
        val apps = pm.getInstalledApplications(0)
            .filter { it.flags and ApplicationInfo.FLAG_SYSTEM == 0 } // user-installed apps only, for a shorter list
            .sortedBy { pm.getApplicationLabel(it).toString().lowercase() }

        val labels = apps.map { "${pm.getApplicationLabel(it)} (${it.packageName})" }.toTypedArray()
        val currentlySelected = settings.authorizedPackages
        val checked = apps.map { it.packageName in currentlySelected }.toBooleanArray()

        AlertDialog.Builder(this)
            .setTitle(com.signalcopier.notificationbridge.R.string.pick_from_installed_apps)
            .setMultiChoiceItems(labels, checked) { _, which, isChecked -> checked[which] = isChecked }
            .setPositiveButton(android.R.string.ok) { _, _ ->
                val selectedPackages = apps.filterIndexed { index, _ -> checked[index] }.map { it.packageName }
                binding.appPackagesInput.setText(selectedPackages.joinToString(", "))
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun saveSettings() {
        val baseUrl = binding.serverBaseUrlInput.text.toString().trim()
        val deviceId = binding.deviceIdInput.text.toString().trim()
        val pairingToken = binding.pairingTokenInput.text.toString().trim()
        val packages = binding.appPackagesInput.text.toString()
            .split(",")
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .toSet()

        if (baseUrl.isEmpty() || deviceId.isEmpty() || pairingToken.isEmpty() || packages.isEmpty()) {
            Toast.makeText(this, "All fields are required (server URL, device ID, pairing token, at least one app package)", Toast.LENGTH_LONG).show()
            return
        }

        settings.serverBaseUrl = baseUrl
        settings.deviceId = deviceId
        settings.pairingToken = pairingToken
        settings.authorizedPackages = packages

        WorkScheduler.scheduleAll(applicationContext)
        Toast.makeText(this, "Saved. Background heartbeat/upload scheduled.", Toast.LENGTH_SHORT).show()
        refreshStatus()
    }
}
