plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.google.devtools.ksp")
}

android {
    namespace = "com.signalcopier.notificationbridge"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.signalcopier.notificationbridge"
        minSdk = 26 // NotificationListenerService's BigText/MessagingStyle extraction path used here
        // targets Android 8.0+; WorkManager periodic heartbeat also requires nothing newer.
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        viewBinding = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")

    // Local durable queue for captured notifications -- survives a
    // network outage / app restart before successful upload (Track 10
    // brief, Part B point 2).
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    ksp("androidx.room:room-compiler:2.6.1")

    // Periodic heartbeat + durable, backed-off retry of queued uploads --
    // Android restricts always-on background services, so WorkManager
    // (not a raw background thread/coroutine in the listener service
    // itself) is what survives process death/Doze (Track 10 brief, Part
    // B points 2 and 4).
    implementation("androidx.work:work-runtime-ktx:2.9.1")

    // HTTP client for POST /ingest/notification-bridge/{device_id}.
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.json:json:20240303")

    // Plain SharedPreferences-backed settings storage (device_id/pairing
    // token/authorized app_packages) -- deliberately NOT DataStore/Proto
    // to keep this a minimal, dependency-light reference implementation;
    // see SettingsStore.kt's own docstring.

    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
}
