"""Track 13: escalation-only active phone-control retrieval -- the hard
safety rails (structural, not just documented), the CapabilityState
DISABLED-by-default/SHADOW-never-live-routed lifecycle, the broker/
banking deny-list refusal, and the narrow read-only action-surface
allow-list. Mirrors tests/test_notification_bridge_registry.py's own
structure (`store` fixture, direct module-level calls, no HTTP layer
needed for the registry/logic tests)."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.db import SignalStore
from app.notification_bridge import ContentCompleteness
from app.phone_escalation import (
    AccessibilityNode,
    AdbPhoneControlAdapter,
    BROKER_APP_PACKAGES,
    CapabilityState,
    DeniedAppPackageError,
    EscalationDisposition,
    ExtractionResult,
    ExtractionStatus,
    MockPhoneControlAdapter,
    MockSignalExtractor,
    PhoneControlAdapter,
    PhoneEscalationError,
    ProviderEscalationConfig,
    evaluate_escalation,
    is_denied_app_package,
    needs_escalation,
    validate_config_registration,
    validate_state_transition,
)


@pytest.fixture
def store(tmp_path: Path) -> SignalStore:
    return SignalStore(tmp_path / "test.db")


# ---------------------------------------------------------------------------
# needs_escalation / content-completeness gate
# ---------------------------------------------------------------------------


def test_complete_content_never_needs_escalation():
    assert needs_escalation(ContentCompleteness.COMPLETE) is False


@pytest.mark.parametrize("completeness", [ContentCompleteness.TRUNCATED, ContentCompleteness.TITLE_ONLY])
def test_incomplete_content_needs_escalation(completeness):
    assert needs_escalation(completeness) is True


# ---------------------------------------------------------------------------
# Broker-app deny-list -- STRUCTURAL refusal (point 2), not just logged
# ---------------------------------------------------------------------------


def test_every_broker_package_is_denied():
    for pkg in BROKER_APP_PACKAGES:
        assert is_denied_app_package(pkg) is True


def test_banking_pattern_catch_all_denies_unlisted_bank_app():
    assert is_denied_app_package("com.examplebank.mobilebanking") is True
    assert is_denied_app_package("com.chase.sig.android") is True
    assert is_denied_app_package("com.paypal.android.p2pmobile") is True


def test_ordinary_non_broker_non_banking_app_is_not_denied():
    assert is_denied_app_package("com.whatsapp") is False
    assert is_denied_app_package("com.discord") is False


class _DenyListProbeAdapter(PhoneControlAdapter):
    """A minimal concrete adapter used ONLY to prove the base class's
    `open_app` refuses before any subclass hook runs -- if the deny-list
    check were merely a documented convention instead of enforced in the
    base class, this probe's `_do_open_app` would be reachable for a
    denied package; it must never be."""

    backend_name = "deny_list_probe"

    def __init__(self) -> None:
        self.opened: list[str] = []

    async def _do_open_app(self, app_package: str) -> None:
        self.opened.append(app_package)

    async def _do_read_screen_text(self) -> str:
        return ""

    async def _do_get_accessibility_tree(self) -> AccessibilityNode:
        return AccessibilityNode(node_id="root", text=None, content_description=None, role="navigation")

    async def _do_tap(self, node: AccessibilityNode) -> None:
        return None

    async def _do_scroll(self, direction: str) -> None:
        return None

    async def _do_go_back(self) -> None:
        return None


@pytest.mark.asyncio
async def test_open_app_structurally_refuses_denied_broker_package():
    adapter = _DenyListProbeAdapter()
    with pytest.raises(DeniedAppPackageError):
        await adapter.open_app("com.robinhood.android")
    # The subclass's own _do_open_app hook must never have been reached --
    # a raise, not a logged-and-continued call.
    assert adapter.opened == []


@pytest.mark.asyncio
async def test_open_app_allows_a_non_denied_package():
    adapter = _DenyListProbeAdapter()
    await adapter.open_app("com.whatsapp")
    assert adapter.opened == ["com.whatsapp"]


@pytest.mark.asyncio
async def test_tap_refuses_non_navigation_node():
    adapter = MockPhoneControlAdapter()
    submit_node = AccessibilityNode(
        node_id="submit_trade_button", text="Submit Order", content_description="Buy", role="submit"
    )
    with pytest.raises(PermissionError):
        await adapter.tap(submit_node)
    assert adapter.tapped_node_ids == []


@pytest.mark.asyncio
async def test_tap_allows_navigation_node():
    adapter = MockPhoneControlAdapter()
    nav_node = AccessibilityNode(node_id="channel_row", text="BuyAlerts", content_description=None, role="navigation")
    await adapter.tap(nav_node)
    assert adapter.tapped_node_ids == ["channel_row"]


def test_config_registration_refuses_denied_app_package():
    with pytest.raises(PhoneEscalationError):
        validate_config_registration(app_package="com.robinhood.android", provider_name="robinhood-alerts")


# ---------------------------------------------------------------------------
# Narrow action-surface allow-list -- AST/reflection static proof (point 1)
# ---------------------------------------------------------------------------

_ALLOWED_PUBLIC_METHODS = {"open_app", "read_screen_text", "get_accessibility_tree", "tap", "scroll", "go_back"}

#: Substrings that would make a method name send/trade/submit/type-
#: capable if one ever appeared on this class -- the static check fails
#: loudly (not silently) if any future method name matches.
_FORBIDDEN_NAME_FRAGMENTS = ("type", "send", "submit", "confirm", "buy", "sell", "trade", "text_input", "input_text")


def test_adapter_public_surface_has_no_action_capable_of_submitting_or_typing():
    """AST-based static check of `PhoneControlAdapter` itself (not just a
    runtime instance) -- mirrors the static-analysis test style referenced
    in the task brief. Parses the class definition directly from source so
    this fails even for a method added but never called anywhere in the
    test suite."""
    source = inspect.getsource(PhoneControlAdapter)
    tree = ast.parse(source)
    class_def = tree.body[0]
    assert isinstance(class_def, ast.ClassDef)

    public_methods = set()
    for node in class_def.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            public_methods.add(node.name)

    assert public_methods == _ALLOWED_PUBLIC_METHODS, (
        f"PhoneControlAdapter's public method surface changed to {public_methods!r} -- "
        f"only {_ALLOWED_PUBLIC_METHODS!r} are allowed; a new public method must be justified "
        "against the read-only safety rail before being added here"
    )
    for name in public_methods:
        lowered = name.lower()
        assert not any(fragment in lowered for fragment in _FORBIDDEN_NAME_FRAGMENTS), (
            f"public method {name!r} looks send/trade/submit/type-capable by name alone"
        )
    # No generic "tap anywhere (x, y)" primitive: `tap`'s only parameter
    # (besides self) must be a single structured node, never raw
    # coordinates or a free-form string a caller could point anywhere.
    tap_def = next(n for n in class_def.body if getattr(n, "name", None) == "tap")
    param_names = [a.arg for a in tap_def.args.args if a.arg != "self"]
    assert param_names == ["node"]


# ---------------------------------------------------------------------------
# CapabilityState lifecycle -- disabled by default, owner-gated promotion
# ---------------------------------------------------------------------------


def test_fresh_provider_config_always_starts_disabled(store: SignalStore):
    config = store.register_phone_escalation_config(app_package="com.whopwaits.app", provider_name="whop-waits")
    assert config["capability_state"] == CapabilityState.DISABLED.value


def test_reregistering_a_config_never_changes_its_capability_state(store: SignalStore):
    store.register_phone_escalation_config(app_package="com.whopwaits.app", provider_name="whop-waits")
    store.set_phone_escalation_capability_state("com.whopwaits.app", "shadow")
    updated = store.register_phone_escalation_config(
        app_package="com.whopwaits.app", provider_name="whop-waits-renamed", notes="re-described"
    )
    assert updated["capability_state"] == CapabilityState.SHADOW.value
    assert updated["provider_name"] == "whop-waits-renamed"


def test_promotion_cannot_skip_shadow():
    with pytest.raises(PhoneEscalationError):
        validate_state_transition(CapabilityState.DISABLED, CapabilityState.ENABLED)


def test_promotion_disabled_to_shadow_is_allowed():
    validate_state_transition(CapabilityState.DISABLED, CapabilityState.SHADOW)  # no raise


def test_promotion_shadow_to_enabled_is_allowed():
    validate_state_transition(CapabilityState.SHADOW, CapabilityState.ENABLED)  # no raise


def test_demotion_from_enabled_straight_to_disabled_is_allowed():
    validate_state_transition(CapabilityState.ENABLED, CapabilityState.DISABLED)  # no raise


def test_enabled_cannot_skip_straight_to_shadow_since_thats_a_no_op_not_a_transition():
    # ENABLED -> SHADOW isn't an offered demotion path (only -> DISABLED);
    # asserting this documents the policy explicitly rather than leaving
    # it implicit.
    with pytest.raises(PhoneEscalationError):
        validate_state_transition(CapabilityState.ENABLED, CapabilityState.SHADOW)


def test_store_promotion_enforces_transition_policy(store: SignalStore):
    store.register_phone_escalation_config(app_package="com.example.alerts", provider_name="example-alerts")
    with pytest.raises(PhoneEscalationError):
        store.set_phone_escalation_capability_state("com.example.alerts", "enabled")
    store.set_phone_escalation_capability_state("com.example.alerts", "shadow")
    promoted = store.set_phone_escalation_capability_state("com.example.alerts", "enabled")
    assert promoted["capability_state"] == "enabled"


def test_promoting_unknown_config_raises_keyerror(store: SignalStore):
    with pytest.raises(KeyError):
        store.set_phone_escalation_capability_state("com.never.registered", "shadow")


# ---------------------------------------------------------------------------
# evaluate_escalation: the end-to-end decision function
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_covered_by_direct_source_never_attempts_retrieval():
    adapter = MockPhoneControlAdapter()
    attempt, extraction = await evaluate_escalation(
        device_id="d1",
        app_package="com.example.alerts",
        notification_key="k1",
        content_hash="h1",
        completeness=ContentCompleteness.TITLE_ONLY,
        covered_by_direct_source=True,
        config=ProviderEscalationConfig(
            id="cfg1", app_package="com.example.alerts", provider_name="p", capability_state=CapabilityState.ENABLED
        ),
        adapter=adapter,
        extractor=MockSignalExtractor(),
    )
    assert attempt.disposition is EscalationDisposition.COVERED_BY_DIRECT_SOURCE
    assert extraction is None
    assert adapter.opened_packages == []


@pytest.mark.asyncio
async def test_complete_content_never_attempts_retrieval():
    adapter = MockPhoneControlAdapter()
    attempt, extraction = await evaluate_escalation(
        device_id="d1",
        app_package="com.example.alerts",
        notification_key="k1",
        content_hash="h1",
        completeness=ContentCompleteness.COMPLETE,
        covered_by_direct_source=False,
        config=ProviderEscalationConfig(
            id="cfg1", app_package="com.example.alerts", provider_name="p", capability_state=CapabilityState.ENABLED
        ),
        adapter=adapter,
        extractor=MockSignalExtractor(),
    )
    assert attempt.disposition is EscalationDisposition.CONTENT_ALREADY_COMPLETE
    assert extraction is None
    assert adapter.opened_packages == []


@pytest.mark.asyncio
async def test_disabled_capability_never_attempts_retrieval_even_with_adapter_present():
    adapter = MockPhoneControlAdapter()
    attempt, extraction = await evaluate_escalation(
        device_id="d1",
        app_package="com.example.alerts",
        notification_key="k1",
        content_hash="h1",
        completeness=ContentCompleteness.TRUNCATED,
        covered_by_direct_source=False,
        config=ProviderEscalationConfig(
            id="cfg1", app_package="com.example.alerts", provider_name="p", capability_state=CapabilityState.DISABLED
        ),
        adapter=adapter,
        extractor=MockSignalExtractor(),
    )
    assert attempt.disposition is EscalationDisposition.CAPABILITY_DISABLED
    assert extraction is None
    assert adapter.opened_packages == []


@pytest.mark.asyncio
async def test_no_config_row_at_all_is_treated_as_disabled():
    attempt, extraction = await evaluate_escalation(
        device_id="d1",
        app_package="com.unregistered.app",
        notification_key="k1",
        content_hash="h1",
        completeness=ContentCompleteness.TRUNCATED,
        covered_by_direct_source=False,
        config=None,
        adapter=MockPhoneControlAdapter(),
        extractor=MockSignalExtractor(),
    )
    assert attempt.disposition is EscalationDisposition.CAPABILITY_DISABLED
    assert extraction is None


@pytest.mark.asyncio
async def test_missing_adapter_or_extractor_is_treated_as_disabled_even_if_enabled():
    config = ProviderEscalationConfig(
        id="cfg1", app_package="com.example.alerts", provider_name="p", capability_state=CapabilityState.ENABLED
    )
    attempt, extraction = await evaluate_escalation(
        device_id="d1",
        app_package="com.example.alerts",
        notification_key="k1",
        content_hash="h1",
        completeness=ContentCompleteness.TRUNCATED,
        covered_by_direct_source=False,
        config=config,
        adapter=None,
        extractor=None,
    )
    assert attempt.disposition is EscalationDisposition.CAPABILITY_DISABLED
    assert extraction is None


@pytest.mark.asyncio
async def test_shadow_mode_result_never_reaches_live_pipeline():
    """The core SHADOW-mode guarantee: even when extraction produces a
    real candidate, the disposition is SHADOW_LOGGED_ONLY and the
    returned `extraction` is `None` -- there is nothing a caller could
    accidentally feed into `engine.handle_signal` for this disposition,
    since only ENABLED_CANDIDATE_READY ever returns a non-None
    extraction (see evaluate_escalation's own return-value contract)."""
    adapter = MockPhoneControlAdapter(screen_text="BTCUSD long entry 50000 stop 49000 target 52000")
    extractor = MockSignalExtractor(
        ExtractionResult(status=ExtractionStatus.CANDIDATE, fields={"symbol": "BTCUSD", "side": "buy"})
    )
    config = ProviderEscalationConfig(
        id="cfg1", app_package="com.example.alerts", provider_name="p", capability_state=CapabilityState.SHADOW
    )
    attempt, extraction = await evaluate_escalation(
        device_id="d1",
        app_package="com.example.alerts",
        notification_key="k1",
        content_hash="h1",
        completeness=ContentCompleteness.TITLE_ONLY,
        covered_by_direct_source=False,
        config=config,
        adapter=adapter,
        extractor=extractor,
    )
    assert attempt.disposition is EscalationDisposition.SHADOW_LOGGED_ONLY
    assert extraction is None
    assert attempt.extraction_status is ExtractionStatus.CANDIDATE
    # Retrieval genuinely ran (read-only) -- SHADOW means "log, never
    # route," not "never attempt."
    assert adapter.opened_packages == ["com.example.alerts"]


@pytest.mark.asyncio
async def test_shadow_mode_never_calls_open_app_on_a_denied_package_either():
    """Even in SHADOW mode, the deny-list is still the adapter's own
    structural refusal -- SHADOW never bypasses it."""
    adapter = MockPhoneControlAdapter()
    config = ProviderEscalationConfig(
        id="cfg1", app_package="com.robinhood.android", provider_name="p", capability_state=CapabilityState.SHADOW
    )
    with pytest.raises(DeniedAppPackageError):
        await evaluate_escalation(
            device_id="d1",
            app_package="com.robinhood.android",
            notification_key="k1",
            content_hash="h1",
            completeness=ContentCompleteness.TRUNCATED,
            covered_by_direct_source=False,
            config=config,
            adapter=adapter,
            extractor=MockSignalExtractor(),
        )


@pytest.mark.asyncio
async def test_enabled_mode_with_real_candidate_returns_candidate_for_caller_to_route():
    adapter = MockPhoneControlAdapter(screen_text="BTCUSD long entry 50000 stop 49000 target 52000")
    extractor = MockSignalExtractor(
        ExtractionResult(status=ExtractionStatus.CANDIDATE, fields={"symbol": "BTCUSD", "side": "buy"})
    )
    config = ProviderEscalationConfig(
        id="cfg1", app_package="com.example.alerts", provider_name="p", capability_state=CapabilityState.ENABLED
    )
    attempt, extraction = await evaluate_escalation(
        device_id="d1",
        app_package="com.example.alerts",
        notification_key="k1",
        content_hash="h1",
        completeness=ContentCompleteness.TITLE_ONLY,
        covered_by_direct_source=False,
        config=config,
        adapter=adapter,
        extractor=extractor,
    )
    assert attempt.disposition is EscalationDisposition.ENABLED_CANDIDATE_READY
    assert extraction is not None
    assert extraction.fields["symbol"] == "BTCUSD"


@pytest.mark.asyncio
async def test_enabled_mode_with_unknown_extraction_records_extraction_unknown():
    adapter = MockPhoneControlAdapter(screen_text="")
    extractor = MockSignalExtractor()  # defaults to UNKNOWN
    config = ProviderEscalationConfig(
        id="cfg1", app_package="com.example.alerts", provider_name="p", capability_state=CapabilityState.ENABLED
    )
    attempt, extraction = await evaluate_escalation(
        device_id="d1",
        app_package="com.example.alerts",
        notification_key="k1",
        content_hash="h1",
        completeness=ContentCompleteness.TITLE_ONLY,
        covered_by_direct_source=False,
        config=config,
        adapter=adapter,
        extractor=extractor,
    )
    assert attempt.disposition is EscalationDisposition.EXTRACTION_UNKNOWN
    assert extraction is None


# ---------------------------------------------------------------------------
# Attempt audit trail persistence
# ---------------------------------------------------------------------------


def test_record_and_list_phone_escalation_attempts(store: SignalStore):
    store.record_phone_escalation_attempt(
        device_id="d1",
        app_package="com.example.alerts",
        notification_key="k1",
        content_hash="h1",
        capability_state_at_attempt="shadow",
        disposition="shadow_logged_only",
        extraction_status="candidate",
        extraction_detail=None,
        signal_id=None,
    )
    attempts = store.list_phone_escalation_attempts(device_id="d1")
    assert len(attempts) == 1
    assert attempts[0]["disposition"] == "shadow_logged_only"
    assert attempts[0]["signal_id"] is None


# ---------------------------------------------------------------------------
# AdbPhoneControlAdapter -- documented stub, honestly not implemented
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adb_adapter_is_an_honest_unimplemented_stub_not_a_working_backend():
    adapter = AdbPhoneControlAdapter(adb_serial="emulator-5554")
    with pytest.raises(NotImplementedError):
        await adapter.open_app("com.whatsapp")
