# ADR-0009: Escalation-only active phone-control retrieval, gated by a read-only action surface and a per-provider capability lifecycle

Status: Accepted
Date: 2026-09-30

## Context

Track 10 (`app/notification_bridge.py`) gave this service a passive
fallback: an Android `NotificationListenerService` forwards captured
notification text for providers with no official API/webhook/email
source. But a captured notification is sometimes incomplete by
construction — Android truncates long text, or a provider's
notification is a bare "New trade posted" pointer with the real detail
only visible inside the app itself
(`app.notification_bridge.ContentCompleteness.TRUNCATED`/`TITLE_ONLY`).
For those events, the only way to recover the full signal without an
official source is to actively look at the screen.

The user's own approved design: a hybrid, escalation-based
architecture — official sources first, passive capture second, active
AI-driven phone control ONLY as the last resort, and even then strictly
read-only for signal acquisition (see `app/phone_escalation.py`'s
module docstring for the full statement). This is not a general phone-
automation capability; it exists solely to close the gap between
"a notification arrived but wasn't enough" and "the pipeline has a
complete signal to evaluate," using the exact same dedup/freshness/
validation/risk/execution pipeline as every other source.

## Decision

- **Escalation-only trigger** (`app.phone_escalation.evaluate_escalation`):
  active retrieval is evaluated ONLY for an event that (1) no direct
  source is known to have already delivered (today: a hardcoded
  `covered_by_direct_source=False` at the one call site in
  `app/main.py`, since Track 12's cross-transport correlation layer
  had not landed on this branch at the time this was written — see
  `EscalationCandidate`'s own docstring for the documented interface
  this is expected to reconcile against), (2) has
  `ContentCompleteness` other than `COMPLETE`
  (`app.phone_escalation.needs_escalation`), and (3) has active
  retrieval `ENABLED`/`SHADOW` for that specific `app_package`
  (`ProviderEscalationConfig.capability_state`, `DISABLED` by default).
  Every event that reaches this function gets an
  `EscalationAttempt` recorded, whatever the outcome — no silent skip.

- **Read-only action surface** (`PhoneControlAdapter`): a hardcoded,
  six-method allow-list (`open_app`/`read_screen_text`/
  `get_accessibility_tree`/`tap`/`scroll`/`go_back`). There is
  deliberately no text-entry primitive at all, and `tap` accepts only
  an `AccessibilityNode` from this same adapter's own prior
  `get_accessibility_tree()` call, restricted to `role == "navigation"`
  — a send/submit/confirm/buy/sell-classified node is structurally
  untappable, not merely discouraged. Proven by
  `tests/test_t13_phone_escalation.py::
  test_adapter_public_surface_has_no_action_capable_of_submitting_or_typing`,
  an AST-based static check of the class definition itself.

- **Broker/banking deny-list** (`DENIED_APP_PACKAGES` /
  `is_denied_app_package`): `PhoneControlAdapter.open_app` refuses
  (raises `DeniedAppPackageError`) any package matching every broker
  this codebase integrates (`app/brokers/*.py`) or a banking/payment
  name pattern, BEFORE a subclass's own `_do_open_app` ever runs — a
  subclass cannot route around this without overriding `open_app`
  itself and deliberately dropping the base-class call.

- **Capability lifecycle** (`CapabilityState`: `DISABLED` ->
  `SHADOW` -> `ENABLED`): a fresh `ProviderEscalationConfig` row always
  starts `DISABLED` (`register_phone_escalation_config` takes no
  `capability_state` argument at all). Promotion is owner-gated
  (`POST /phone-escalation/configs/{app_package}/promote`, behind
  `Depends(require_owner)`) and can only move one step at a time
  (`validate_state_transition`) — `DISABLED` may never jump straight
  to `ENABLED`. `SHADOW` retrieval runs and is recorded
  (`phone_escalation_attempts`) but its `EscalationDisposition` is
  always `SHADOW_LOGGED_ONLY`, whose `evaluate_escalation` return value
  never carries an `ExtractionResult` the caller could route — there is
  nothing to accidentally feed live even by caller error.

- **No parallel execution path**: the only disposition that ever
  produces a routable candidate is `ENABLED_CANDIDATE_READY`, and the
  caller is expected to build an ordinary `Signal` from it (with
  `channel_id`/`message_id` set from the SAME device/notification
  identity as the triggering event) and call `engine.handle_signal` —
  exactly the path every other source uses, with the same dedup/SIG-01
  replay guard applying for free.

- **Bounded extraction boundary** (`SignalExtractor`): raw screen text
  in, a structured `ExtractionResult` (`CANDIDATE` with fields, or
  `UNKNOWN`) out — no tool access, no ability to call
  `PhoneControlAdapter` again or take any action. Mirrors
  `signal-portfolio-commercial`'s `model_gateway.py`-style permission
  boundary; this is the first such module in signal-copier.

## Consequences

- No real device backend exists yet. `AdbPhoneControlAdapter` is a
  documented stub (every method raises `NotImplementedError`) — the
  recommended real backend is a thin `adb shell`
  (`uiautomator dump`/`input tap`/`input swipe`) wrapper, not a
  third-party phone-automation framework, consistent with this
  session's preference for a direct/self-hosted implementation. Wiring
  a real adapter into `app/main.py`'s `_resolve_phone_control_adapter`
  (currently always returns `(None, None)`) is a follow-up that
  genuinely needs a physical device/emulator, out of this task's scope.
- Because `_resolve_phone_control_adapter` always returns `(None,
  None)` today, `evaluate_escalation` can never reach
  `ENABLED_CANDIDATE_READY` from the real `/ingest/notification-bridge`
  route yet, regardless of what `capability_state` an operator sets —
  an honest, structurally-enforced no-op until a real backend is
  wired in, never a state that silently pretends to work.
- Track 12's real "events needing escalation" query is not yet
  consumed — `EscalationCandidate` documents the expected interface so
  the eventual integration is a reconciliation, not a redesign.
- `app.notification_bridge.ContentCompleteness` was NOT modified (hard
  rule 3: no public-contract change without updating every caller) —
  `needs_escalation` currently maps `TRUNCATED`/`TITLE_ONLY` (the
  closest existing equivalents of the design brief's
  `PARTIAL`/`POINTER_ONLY`) to escalation-eligible, documented as the
  one place to update if Track 12 introduces a finer-grained enum.
