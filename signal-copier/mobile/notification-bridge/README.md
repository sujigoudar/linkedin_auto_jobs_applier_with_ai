# Signal Copier Notification Bridge (Android)

A minimal, real Android Studio / Gradle project implementing the
Android-side fallback capture path described in the Track 10 brief: a
`NotificationListenerService` that captures trading-alert notifications
from a configured app and forwards them to signal-copier's own
`POST /ingest/notification-bridge/{device_id}` endpoint
(`signal-copier/app/main.py`, registry/vocabulary in
`signal-copier/app/notification_bridge.py`).

## What this is for

Some trading-alert providers expose their alerts ONLY as a mobile push
notification — no webhook, no bot, no public API. This app is a
dedicated Android device's own "bridge": it watches for notifications
from whichever app(s) you authorize, captures the fullest text it can,
and uploads them to your own signal-copier deployment, where they're
parsed and (subject to the same qualification/staleness/completeness
gates every other source goes through) routed like any other signal.

**This is a fallback of last resort**, not a replacement for a real
webhook/API/bot integration when one exists — see the Track 10 brief's
own framing.

## Important, upfront honesty notice

**This project was written and reviewed in an environment with no
physical Android device or emulator available.** It has NOT been built,
installed, or run against a real device in this session. The Kotlin code
is believed correct against the documented Android/AndroidX/Room/
WorkManager/OkHttp APIs it uses, and its structure directly mirrors the
signal-copier server route it talks to (see
`signal-copier/app/notification_bridge.py` and the tests in
`signal-copier/tests/test_notification_bridge_api.py`), but **you must
build it yourself, install it on a real device, and verify the actual
pairing → capture → upload → dashboard flow end to end.** Please report
back what you find (a successful pairing, a captured notification
actually reaching the server's `/notification-bridge/devices/{id}/events`
audit log, and its correct `health_state`) so any real bugs can be fixed
with real evidence, not assumed away.

## What you need to do (step by step)

### 1. Register the device server-side (owner-gated, on your signal-copier host)

```
POST /notification-bridge/devices
{
  "device_id": "my-pixel-phone",
  "app_packages": ["com.example.tradingapp"],
  "provider_mapping": {
    "com.example.tradingapp": {"provider_name": "my_provider", "analyst": null}
  }
}
```

(Send this the same way you'd call any other owner-gated signal-copier
endpoint — with your session cookie + `X-CSRF-Token`, e.g. via `curl` or
the dashboard's own API console if it has one.)

The response includes a **`pairing_token`** field. **Copy it down now —
it is shown exactly once and is never stored anywhere in recoverable
form** (the server only ever keeps an argon2id hash of it, the same way
it hashes the owner's own login password — see
`app/notification_bridge.py`'s `hash_pairing_token`). If you lose it,
re-run the same `POST /notification-bridge/devices` call with the same
`device_id` to get a fresh token (this replaces the old one).

### 2. Build the app

Open this `mobile/notification-bridge/` directory as a project in
[Android Studio](https://developer.android.com/studio) (a current stable
release — this project targets `compileSdk 34`, Kotlin 1.9, AGP 8.5).
Android Studio will generate the Gradle wrapper (`gradlew`/`gradlew.bat`)
automatically on first sync if it isn't already present; **this repo does
not commit the wrapper jar itself**, so if you're building from the
command line instead of Android Studio, run:

```
gradle wrapper --gradle-version 8.7
```

once (using a local Gradle install) to generate it, then from then on:

```
./gradlew assembleDebug
```

The built APK lands at `app/build/outputs/apk/debug/app-debug.apk`.

### 3. Side-load it onto your dedicated device

With the device connected over USB (Developer Options → USB debugging
enabled):

```
./gradlew installDebug
```

or copy the APK to the device and install it directly (you'll need to
allow "install from unknown sources" for whichever app you use to open
the APK file, since this isn't distributed through Google Play).

### 4. Grant the notification-listener permission (manual — no way around this)

Android deliberately has **no programmatic way to request
`NotificationListenerService` access** — an app can only ever open the
system settings screen and ask the user to flip it on by hand (that's
what this app's "Open notification access settings" button does).

On the device:

1. Open the **Signal Copier Notification Bridge** app.
2. Tap **"Open notification access settings"** (or go manually: **Settings
   → Apps → Special app access → Notification access**, or on some OEM
   skins **Settings → Notifications → Advanced → Notification access** /
   **Settings → Security → App permissions → Notification access**
   — the exact path varies by Android version/OEM).
3. Find **Signal Copier Notification Bridge** in the list and toggle it
   on. You'll get an OS warning about the scope of what this grants
   (visibility into every notification's own content) — accept it.
4. Return to the app; "Notification access" should now read **GRANTED**.

### 5. Configure pairing in the app

On the app's main (and only) screen, fill in:

- **Server base URL** — e.g. `https://your-signal-copier-host` (no
  trailing slash needed).
- **Device ID** — must match EXACTLY the `device_id` you registered in
  step 1.
- **Pairing token** — the token from step 1's response, typed or pasted
  in. Never hardcoded anywhere in this app's source.
- **Authorized app packages** — either type comma-separated Android
  package names directly, or tap **"Pick from installed apps"** for a
  checklist of currently-installed (non-system) apps. This MUST match
  the `app_packages` you registered server-side — a notification from a
  package not listed here is filtered out on-device before it's even
  queued, and independently rejected server-side too if it somehow gets
  through.

Tap **"Save and start bridging"**. This schedules the periodic heartbeat
and upload workers (WorkManager).

### 6. Verify it's working

- The app's own **Status** section shows "Last successful upload" and
  any upload error, updated every time you reopen the app.
- Trigger a real notification from your configured trading-alert app (or
  wait for a real one to arrive).
- Server-side, check `GET /notification-bridge/devices/{device_id}` for
  the device's current `health_state`, and
  `GET /notification-bridge/devices/{device_id}/events` for the actual
  recorded events and their `classification` (`live`,
  `needs_review_incomplete_content`, `stale_backlog_import_only`,
  `duplicate_retry`, `rejected_unauthorized_app_package`).

**Please report back the real results of this test** (screenshots of the
app's status screen, and the server's own `/notification-bridge/devices/...`
JSON) so the actual on-device behavior can be confirmed or fixed — none
of it has been verified from this session.

## What gets extracted from a notification, and why

| Android field | Used for | Notes |
|---|---|---|
| `Notification.EXTRA_TITLE` | `title` | |
| `Notification.EXTRA_TEXT` | `text` (short text) | |
| `Notification.EXTRA_BIG_TEXT` (`BigTextStyle`) | `expanded_text` | Preferred first when present |
| `MessagingStyle` messages (`NotificationCompat.MessagingStyle`) | `expanded_text` + `conversation_participants` + `is_group_conversation` | Highest preference — the richest available structure |
| `Notification.EXTRA_TEXT_LINES` (`InboxStyle`) | `expanded_text` (joined) | Used if neither of the above is present |

**`content_completeness`** is set from what was ACTUALLY extractable, not
guessed:

- `"complete"` — an expanded field (BigText/MessagingStyle/InboxStyle)
  was present, OR the short text alone doesn't look elided (no trailing
  `…`/`...`).
- `"truncated"` — only short text was available AND it looks elided.
- `"title_only"` — no text/expanded text at all, only a title.

A notification with genuinely nothing extractable at all (no title, no
text) is not queued.

### A specific bug this implementation deliberately avoids

The Track 10 research flagged a real bug in a reference implementation
(`ItsAzni/NotificationForwarder`): it reads the *expanded* text to detect
duplicates, but only ever enqueues the *shorter* field, silently losing
content. This app's `ExtractedContent.bestAvailableText` (see
`app/src/main/java/com/signalcopier/notificationbridge/capture/ExtractedContent.kt`)
always forwards the fullest text that was actually extracted — the short
`text` field is never sent in place of a richer `expanded_text` when both
exist.

## Architecture

```
NotificationBridgeListenerService (captures + filters by app_package)
        │
        ▼
  Room database (queued_notifications) — durable local queue,
  survives app/process restart and network outages
        │
        ▼
  UploadWorker (WorkManager, immediate + periodic, retry/backoff)
        │
        ▼
  POST /ingest/notification-bridge/{device_id}  (signal-copier server)

HeartbeatWorker (WorkManager, periodic, independent of the above) posts
an empty event batch to the same endpoint purely so the server can tell
"device online, nothing to forward" apart from "device offline".
```

## Known limitations (disclosed, not silently glossed over)

- **Not tested on a real device or emulator in this session** — see the
  honesty notice at the top of this file.
- The pairing token is stored in plain `SharedPreferences`
  (`SettingsStore`), protected only by Android's normal per-app private
  storage sandbox. A hardened deployment should switch this one field to
  `androidx.security.crypto.EncryptedSharedPreferences` — not done here
  to keep this reference implementation's dependency footprint minimal.
- WorkManager's `PeriodicWorkRequest` has an OS-enforced minimum interval
  of 15 minutes — `HeartbeatWorker`/`UploadWorker` cannot heartbeat/flush
  more often than that in the background. The immediate one-off upload
  triggered right after each capture (`NotificationBridgeListenerService`)
  is what actually delivers a fresh notification promptly; the periodic
  workers are the offline-recovery safety net, not the primary delivery
  path.
- No unit/instrumented tests are included for the Kotlin code — this
  environment has no Android SDK/emulator to run them against. The
  extraction logic (`NotificationContentExtractor`) is written as a
  small, pure function specifically so it COULD be unit-tested with
  Robolectric or a JVM test in a real Android dev environment; that test
  suite is left for you to add and run there.
- `app_packages` client-side filtering and server-side authorization are
  independent, redundant checks by design — but neither can stop a
  malicious actor who has the pairing token AND controls what's on the
  authorized app's own notification shade from injecting fabricated
  content. This bridge's trust model is the same as every other
  signal-copier source: it trusts what the configured provider posts,
  same as a Telegram channel or a webhook sender.
