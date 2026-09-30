// Root build file -- see README.md for how to build/install this project.
// No secrets, tokens, or account-specific values live in this repo: the
// device_id/pairing_token/app_packages are all entered by the user, once,
// through the app's own settings screen (SettingsScreen/SettingsStore) --
// never hardcoded here or anywhere else in this module.
plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
    id("com.google.devtools.ksp") version "1.9.24-1.0.20" apply false
}
