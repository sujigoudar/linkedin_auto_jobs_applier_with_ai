"""Track 16: per-provider/per-source freshness config actually wired into
app/engine.py, conflict-resolution-policy wiring (app/signal_correlation.py),
transport-dedup vs. semantic-correlation read-path separation, canonical
signal lifecycle assembly, and position-ownership visibility.

Regression discipline covered throughout: every new behavior is a strict
superset -- a signal/provider with no registered `providers` row (every
signal this codebase produced before Track 14, and most of its adapters/
fixtures today) must behave EXACTLY as before Track 16."""
from datetime import datetime, timedelta, timezone

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule
from app.signal_correlation import ConflictResolutionPolicy, resolve_conflict
from app.signal_freshness import (
    FreshnessConfig,
    RecoveredEventBehavior,
    StalenessAction,
    TimestampSourcePreference,
    evaluate_signal_freshness,
    freshness_config_from_rows,
    resolve_effective_timestamp,
)


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _engine(store, source="buyalerts"):
    broker = PaperBroker()
    routing = RoutingConfig(
        rules=[RoutingRule(source=source, destinations=["acct1"])],
        accounts={"acct1": DestinationAccount(account_id="acct1", broker="paper")},
    )
    return SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store), broker


# === Freshness config resolution / evaluation (pure logic) ================


def test_freshness_config_disabled_for_unregistered_provider():
    """The strict-superset case: no `providers` row at all."""
    config = freshness_config_from_rows(None, None)
    assert config.is_configured is False
    signal = Signal(source="ghost", symbol="BTCUSDT", side=Side.BUY, received_at=datetime.now(timezone.utc) - timedelta(days=999))
    decision = evaluate_signal_freshness(signal, config)
    assert decision.ok is True
    assert decision.action is None


def test_freshness_config_disabled_for_registered_provider_with_no_freshness_fields_set():
    """A provider row exists but never set any freshness field -- still a
    real no-op, never enforced against defaults it never chose."""
    provider_row = {"id": "acme", "max_entry_age_seconds": None, "stale_exit_policy": None}
    config = freshness_config_from_rows(provider_row, None)
    assert config.is_configured is False


def test_resolve_effective_timestamp_never_reduces_to_received_at_when_preferred_field_present():
    received = datetime.now(timezone.utc)
    provider_ts = received - timedelta(minutes=30)
    signal = Signal(source="acme", symbol="BTCUSDT", side=Side.BUY, received_at=received, source_created_at=provider_ts)
    config = FreshnessConfig(timestamp_source_preference=TimestampSourcePreference.PROVIDER_TIMESTAMP, resolved_from="provider:acme")
    resolution = resolve_effective_timestamp(signal, config)
    assert resolution.timestamp == provider_ts
    assert resolution.fallback_used is False
    assert resolution.field_used == "source_created_at"


def test_resolve_effective_timestamp_falls_back_explicitly_when_preferred_field_is_none():
    received = datetime.now(timezone.utc)
    signal = Signal(source="acme", symbol="BTCUSDT", side=Side.BUY, received_at=received, source_created_at=None)
    config = FreshnessConfig(timestamp_source_preference=TimestampSourcePreference.PROVIDER_TIMESTAMP, resolved_from="provider:acme")
    resolution = resolve_effective_timestamp(signal, config)
    assert resolution.timestamp == received
    assert resolution.fallback_used is True  # never a SILENT fallback
    assert resolution.field_used == "received_at"


def test_stale_entry_always_rejected_regardless_of_behavior_config():
    """No behavior enum applies to entry staleness per the spec -- always
    the conservative floor (REJECT), even if exit_stale_behavior is set
    to ACT_ANYWAY (that field only governs EXIT staleness). Age (2min) is
    kept below the recovered-event threshold (10x max_entry_age_seconds)
    so this exercises ORDINARY staleness, not the recovered-event branch
    -- see the dedicated recovered-event tests below for that case."""
    old = datetime.now(timezone.utc) - timedelta(minutes=2)
    signal = Signal(source="acme", symbol="BTCUSDT", side=Side.BUY, received_at=old)
    config = FreshnessConfig(max_entry_age_seconds=60, exit_stale_behavior=StalenessAction.ACT_ANYWAY, resolved_from="provider:acme")
    decision = evaluate_signal_freshness(signal, config)
    assert decision.ok is False
    assert decision.action is StalenessAction.REJECT


def test_stale_exit_hold_default_when_behavior_unset():
    old = datetime.now(timezone.utc) - timedelta(minutes=2)
    signal = Signal(source="acme", symbol="BTCUSDT", side=Side.CLOSE, received_at=old)
    config = FreshnessConfig(max_entry_age_seconds=60, resolved_from="provider:acme")
    decision = evaluate_signal_freshness(signal, config)
    assert decision.ok is False
    assert decision.action is StalenessAction.HOLD


def test_stale_exit_act_anyway_proceeds():
    old = datetime.now(timezone.utc) - timedelta(minutes=2)
    signal = Signal(source="acme", symbol="BTCUSDT", side=Side.CLOSE, received_at=old)
    config = FreshnessConfig(max_entry_age_seconds=60, exit_stale_behavior=StalenessAction.ACT_ANYWAY, resolved_from="provider:acme")
    decision = evaluate_signal_freshness(signal, config)
    assert decision.ok is True


def test_fresh_signal_always_ok():
    now = datetime.now(timezone.utc)
    signal = Signal(source="acme", symbol="BTCUSDT", side=Side.BUY, received_at=now)
    config = FreshnessConfig(max_entry_age_seconds=3600, resolved_from="provider:acme")
    decision = evaluate_signal_freshness(signal, config)
    assert decision.ok is True


def test_recovered_event_uses_its_own_behavior_not_ordinary_stale_behavior():
    very_old = datetime.now(timezone.utc) - timedelta(seconds=10000)
    signal = Signal(source="acme", symbol="BTCUSDT", side=Side.CLOSE, received_at=very_old)
    config = FreshnessConfig(
        max_entry_age_seconds=60,  # 10000s is >= 10x60s -- a "recovered" event
        exit_stale_behavior=StalenessAction.ACT_ANYWAY,  # would otherwise pass
        recovered_event_behavior=RecoveredEventBehavior.HOLD_FOR_REVIEW,
        resolved_from="provider:acme",
    )
    decision = evaluate_signal_freshness(signal, config)
    assert decision.ok is False
    assert decision.recovered_event is True
    assert decision.action is StalenessAction.HOLD


def test_recovered_event_process_normally_falls_through_to_ordinary_evaluation():
    very_old = datetime.now(timezone.utc) - timedelta(seconds=10000)
    signal = Signal(source="acme", symbol="BTCUSDT", side=Side.CLOSE, received_at=very_old)
    config = FreshnessConfig(
        max_entry_age_seconds=60,
        exit_stale_behavior=StalenessAction.ACT_ANYWAY,
        recovered_event_behavior=RecoveredEventBehavior.PROCESS_NORMALLY,
        resolved_from="provider:acme",
    )
    decision = evaluate_signal_freshness(signal, config)
    assert decision.ok is True  # ordinary exit evaluation with ACT_ANYWAY -> proceeds


def test_source_level_freshness_policy_overrides_provider_default():
    provider_row = {"id": "acme", "max_entry_age_seconds": 3600, "stale_exit_policy": "HOLD"}
    source_row = {"freshness_policy": {"max_entry_age_seconds": 60}}
    config = freshness_config_from_rows(provider_row, source_row)
    assert config.max_entry_age_seconds == 60
    assert config.resolved_from == "source:acme"


# === Engine integration: freshness gating ==================================


@pytest.mark.asyncio
async def test_stale_entry_signal_is_held_and_never_routed(store):
    engine, broker = _engine(store)
    store.register_provider(provider_id="buyalerts", display_name="Buy Alerts", max_entry_age_seconds=60)

    place_order_calls = []
    original_place_order = broker.place_order

    async def tracking(*args, **kwargs):
        place_order_calls.append(args)
        return await original_place_order(*args, **kwargs)

    broker.place_order = tracking

    old_signal = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65000.0,
        received_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    results = await engine.handle_signal(old_signal)
    assert len(place_order_calls) == 0
    assert len(results) == 1
    assert results[0].status == OrderStatus.REJECTED
    assert "STALE_SIGNAL" in results[0].message


@pytest.mark.asyncio
async def test_signal_with_no_registered_provider_is_never_gated_by_freshness(store):
    """Strict superset: no providers row at all for this source -- ancient
    signal still routes exactly as before Track 16."""
    engine, broker = _engine(store, source="legacy_source")
    old_signal = Signal(
        source="legacy_source", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65000.0,
        received_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    results = await engine.handle_signal(old_signal)
    assert results[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_fresh_entry_signal_with_registered_provider_routes_normally(store):
    engine, broker = _engine(store)
    store.register_provider(provider_id="buyalerts", display_name="Buy Alerts", max_entry_age_seconds=3600)
    fresh_signal = Signal(source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65000.0)
    results = await engine.handle_signal(fresh_signal)
    assert results[0].status == OrderStatus.FILLED


# === Conflict-resolution policy: pure `resolve_conflict` ====================


def test_resolve_conflict_hold_always_holds():
    resolution = resolve_conflict(policy=ConflictResolutionPolicy.HOLD, new_channel_id="a", candidate_channel_id="b")
    assert resolution.action == "hold"


def test_resolve_conflict_require_primary_source_trusts_new_when_it_matches():
    resolution = resolve_conflict(
        policy=ConflictResolutionPolicy.REQUIRE_PRIMARY_SOURCE,
        new_channel_id="telegram:1",
        candidate_channel_id="whop:1",
        primary_source_native_id="telegram:1",
        primary_source_count=1,
    )
    assert resolution.action == "trust_new"


def test_resolve_conflict_require_primary_source_trusts_candidate_when_it_matches():
    resolution = resolve_conflict(
        policy=ConflictResolutionPolicy.REQUIRE_PRIMARY_SOURCE,
        new_channel_id="whop:1",
        candidate_channel_id="telegram:1",
        primary_source_native_id="telegram:1",
        primary_source_count=1,
    )
    assert resolution.action == "trust_candidate"


def test_resolve_conflict_require_primary_source_falls_back_to_hold_when_ambiguous():
    # Zero PRIMARY sources.
    assert resolve_conflict(
        policy=ConflictResolutionPolicy.REQUIRE_PRIMARY_SOURCE,
        new_channel_id="a", candidate_channel_id="b", primary_source_native_id=None, primary_source_count=0,
    ).action == "hold"
    # More than one PRIMARY source (ambiguous).
    assert resolve_conflict(
        policy=ConflictResolutionPolicy.REQUIRE_PRIMARY_SOURCE,
        new_channel_id="a", candidate_channel_id="b", primary_source_native_id="a", primary_source_count=2,
    ).action == "hold"
    # Exactly one PRIMARY source, but NEITHER side matches it.
    assert resolve_conflict(
        policy=ConflictResolutionPolicy.REQUIRE_PRIMARY_SOURCE,
        new_channel_id="a", candidate_channel_id="b", primary_source_native_id="c", primary_source_count=1,
    ).action == "hold"


def test_resolve_conflict_provider_deterministic_trusts_configured_source():
    resolution = resolve_conflict(
        policy=ConflictResolutionPolicy.PROVIDER_DETERMINISTIC,
        new_channel_id="telegram:1", candidate_channel_id="whop:1",
        deterministic_source_native_id="telegram:1",
    )
    assert resolution.action == "trust_new"


def test_resolve_conflict_provider_deterministic_falls_back_to_hold_when_unset():
    resolution = resolve_conflict(
        policy=ConflictResolutionPolicy.PROVIDER_DETERMINISTIC,
        new_channel_id="telegram:1", candidate_channel_id="whop:1",
        deterministic_source_native_id=None,
    )
    assert resolution.action == "hold"


def test_resolve_conflict_require_matching_sources_never_trusts_either_side():
    """A material conflict is NEVER resolved by this policy -- only
    single-source under-corroboration is gated by it (at the engine
    layer, before a conflict is even possible to observe -- see the
    engine integration test below)."""
    resolution = resolve_conflict(
        policy=ConflictResolutionPolicy.REQUIRE_MATCHING_SOURCES,
        new_channel_id="a", candidate_channel_id="b",
    )
    assert resolution.action == "hold"


def test_conflict_resolution_policy_has_no_llm_or_ai_dependency():
    """Structural proof, not just a docstring claim: `resolve_conflict`'s
    own module imports NOTHING network/model-related (checked via the
    module's own real `import` statements, never its prose -- this
    module's docstrings freely mention "LLM"/"AI" while explaining why
    there is none, which a naive substring-over-the-whole-file check
    would misfire on), and it is a pure function of its arguments (same
    inputs -> same output, called twice)."""
    import ast

    import app.signal_correlation as sc_module

    banned_modules = ("openai", "anthropic", "requests", "httpx", "http.client", "urllib")
    tree = ast.parse(open(sc_module.__file__).read())
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
    for banned in banned_modules:
        assert not any(name == banned or name.startswith(banned + ".") for name in imported_names), (
            f"unexpected import of {banned!r} in app/signal_correlation.py: {imported_names}"
        )

    kwargs = dict(
        policy=ConflictResolutionPolicy.PROVIDER_DETERMINISTIC,
        new_channel_id="telegram:1", candidate_channel_id="whop:1",
        deterministic_source_native_id="telegram:1",
    )
    first = resolve_conflict(**kwargs)
    second = resolve_conflict(**kwargs)
    assert first.action == second.action == "trust_new"


# === Engine integration: conflict-resolution-policy wiring =================


@pytest.mark.asyncio
async def test_require_matching_sources_does_not_route_a_single_source_signal(store):
    """The opt-in change: with this policy configured, a single-source
    signal (no corroborating candidate yet) must NOT route immediately
    -- the pre-Track-16 default behavior (single source routes right
    away) only still applies when this policy is NOT configured."""
    engine, broker = _engine(store)
    store.register_provider(
        provider_id="buyalerts", display_name="Buy Alerts",
        conflict_resolution_policy=ConflictResolutionPolicy.REQUIRE_MATCHING_SOURCES.value,
    )

    place_order_calls = []
    original_place_order = broker.place_order

    async def tracking(*args, **kwargs):
        place_order_calls.append(args)
        return await original_place_order(*args, **kwargs)

    broker.place_order = tracking

    now = datetime.now(timezone.utc)
    from_telegram = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65000.0,
        channel_id="telegram:chan1", message_id="msg-1", received_at=now,
    )
    first = await engine.handle_signal(from_telegram)
    assert len(place_order_calls) == 0
    assert first[0].status == OrderStatus.REJECTED
    assert "INSUFFICIENT_CORROBORATION" in first[0].message

    # A second, independent, corroborating source now arrives -- THIS one
    # should actually route (canonicalized onto the first signal's id).
    from_whop = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65010.0,
        channel_id="phone-1:com.whop.whop", message_id="whop-key-1", received_at=now + timedelta(minutes=1),
    )
    second = await engine.handle_signal(from_whop)
    assert len(place_order_calls) == 1
    assert second[0].status == OrderStatus.FILLED
    assert from_whop.id == from_telegram.id


@pytest.mark.asyncio
async def test_require_matching_sources_still_holds_a_genuine_conflict(store):
    engine, broker = _engine(store)
    store.register_provider(
        provider_id="buyalerts", display_name="Buy Alerts",
        conflict_resolution_policy=ConflictResolutionPolicy.REQUIRE_MATCHING_SOURCES.value,
    )
    now = datetime.now(timezone.utc)
    from_telegram = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65000.0,
        channel_id="telegram:chan1", message_id="msg-1", received_at=now,
    )
    from_whop = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=95000.0,
        channel_id="phone-1:com.whop.whop", message_id="whop-key-1", received_at=now + timedelta(minutes=1),
    )
    first = await engine.handle_signal(from_telegram)
    second = await engine.handle_signal(from_whop)
    assert first[0].status == OrderStatus.REJECTED  # single-source, never routed under this policy either
    assert second[0].status == OrderStatus.REJECTED
    assert "CONFLICTING_SOURCE_DATA" in second[0].message


@pytest.mark.asyncio
async def test_require_primary_source_trusts_the_primary_signal_over_a_conflicting_one(store):
    engine, broker = _engine(store)
    store.register_provider(
        provider_id="buyalerts", display_name="Buy Alerts",
        conflict_resolution_policy=ConflictResolutionPolicy.REQUIRE_PRIMARY_SOURCE.value,
    )
    store.register_source(
        source_id="buyalerts-telegram", provider_id="buyalerts", platform="telegram",
        source_native_id="telegram:chan1", role="PRIMARY",
    )

    place_order_calls = []
    original_place_order = broker.place_order

    async def tracking(*args, **kwargs):
        place_order_calls.append(args)
        return await original_place_order(*args, **kwargs)

    broker.place_order = tracking

    now = datetime.now(timezone.utc)
    from_telegram = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65000.0,
        channel_id="telegram:chan1", message_id="msg-1", received_at=now,
    )
    from_whop = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=95000.0,  # conflicting price
        channel_id="phone-1:com.whop.whop", message_id="whop-key-1", received_at=now + timedelta(minutes=1),
    )
    first = await engine.handle_signal(from_telegram)
    second = await engine.handle_signal(from_whop)
    assert first[0].status == OrderStatus.FILLED
    # The PRIMARY (telegram) signal is trusted -- the conflicting Whop
    # signal is NOT routed as a second order, and never REJECTED either
    # (trust_candidate: canonicalized onto the already-filled telegram
    # order, same shape as an ordinary corroboration).
    assert len(place_order_calls) == 1
    assert second[0].status == OrderStatus.FILLED
    assert from_whop.id == from_telegram.id


# === Transport-dedup vs. semantic-correlation: two distinct read paths =====


@pytest.mark.asyncio
async def test_transport_duplicates_and_semantic_correlations_are_distinct_read_paths(store):
    engine, broker = _engine(store)
    now = datetime.now(timezone.utc)

    # Same channel/message, edited once -- a TRANSPORT-level revision.
    original = Signal(
        source="buyalerts", symbol="ETHUSDT", side=Side.BUY, quantity=1, price=3000.0,
        channel_id="telegram:chanX", message_id="msg-edit-1", revision_id=None, received_at=now,
    )
    edit = Signal(
        source="buyalerts", symbol="ETHUSDT", side=Side.BUY, quantity=1, price=3005.0,
        channel_id="telegram:chanX", message_id="msg-edit-1", revision_id="rev-2",
        original_message_id="msg-edit-1", received_at=now + timedelta(seconds=5),
    )
    await engine.handle_signal(original)
    await engine.handle_signal(edit)

    # A DIFFERENT trade, cross-transport corroboration -- a SEMANTIC match.
    a = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65000.0,
        channel_id="telegram:chan1", message_id="sem-1", received_at=now,
    )
    b = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65010.0,
        channel_id="phone-1:com.whop.whop", message_id="sem-whop-1", received_at=now + timedelta(minutes=1),
    )
    await engine.handle_signal(a)
    await engine.handle_signal(b)

    transport_groups = store.list_transport_duplicate_groups()
    semantic = store.list_semantic_correlations()

    assert any(g["channel_id"] == "telegram:chanX" and g["message_id"] == "msg-edit-1" for g in transport_groups)
    # The semantic (cross-transport) pair must NEVER show up as a transport
    # duplicate group (different channel_ids entirely).
    assert not any(g["channel_id"] == "telegram:chan1" for g in transport_groups)

    assert any(s["canonical_signal_id"] == a.id for s in semantic)
    # The transport-revision pair must NEVER show up in the semantic
    # correlation listing (no cross-transport fingerprint evidence was
    # ever recorded for it -- it never had a different channel_id).
    assert not any(s["canonical_signal_id"] == original.id for s in semantic)


# === Canonical signal lifecycle timeline ====================================


@pytest.mark.asyncio
async def test_signal_lifecycle_includes_received_decision_and_order_events(store):
    engine, broker = _engine(store)
    signal = Signal(source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65000.0)
    results = await engine.handle_signal(signal)
    assert results[0].status == OrderStatus.FILLED

    lifecycle = store.get_signal_lifecycle(signal.id)
    assert lifecycle is not None
    kinds = [e["kind"] for e in lifecycle["events"]]
    assert "received" in kinds
    assert "decision" in kinds
    assert "order_submitted" in kinds
    assert "order_filled" in kinds
    # events are chronologically ordered
    assert lifecycle["events"] == sorted(lifecycle["events"], key=lambda e: e["at"])


def test_signal_lifecycle_returns_none_for_unknown_signal(store):
    assert store.get_signal_lifecycle("does-not-exist") is None


@pytest.mark.asyncio
async def test_signal_lifecycle_includes_correlated_event_for_corroborating_signals(store):
    engine, broker = _engine(store)
    now = datetime.now(timezone.utc)
    a = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65000.0,
        channel_id="telegram:chan1", message_id="lc-1", received_at=now,
    )
    b = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65010.0,
        channel_id="phone-1:com.whop.whop", message_id="lc-whop-1", received_at=now + timedelta(minutes=1),
    )
    await engine.handle_signal(a)
    await engine.handle_signal(b)
    lifecycle = store.get_signal_lifecycle(a.id)
    kinds = [e["kind"] for e in lifecycle["events"]]
    assert "correlated" in kinds


# === Position ownership: existing protection + read-only visibility ========


@pytest.mark.asyncio
async def test_exit_from_account_with_no_open_position_creates_no_order(store):
    """Verifies the EXISTING protection this task investigated (see this
    task's own final report): a CLOSE signal against an account/symbol
    with no tracked position at all resolves to REJECTED, never a live
    order -- app/engine.py's own `_resolve_close`/`_resolve_and_submit_
    plain_close` (position == 0 -> None -> REJECTED "nothing to close")."""
    engine, broker = _engine(store)
    close_signal = Signal(source="buyalerts", symbol="BTCUSDT", side=Side.CLOSE)
    results = await engine.handle_signal(close_signal)
    assert len(results) == 1
    assert results[0].status == OrderStatus.REJECTED


@pytest.mark.asyncio
async def test_position_provider_allocations_reports_per_provider_contribution(store):
    engine, broker = _engine(store)
    signal = Signal(source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.5, price=65000.0)
    results = await engine.handle_signal(signal)
    assert results[0].status == OrderStatus.FILLED

    allocations = store.get_position_provider_allocations("BTCUSDT")
    assert allocations["symbol"] == "BTCUSDT"
    accounts = allocations["accounts"]
    assert len(accounts) == 1
    assert accounts[0]["account_id"] == "acct1"
    providers = {p["provider"]: p["attributable_quantity"] for p in accounts[0]["provider_allocations"]}
    assert providers.get("buyalerts") == pytest.approx(0.5)
    assert accounts[0]["unattributed_quantity"] == pytest.approx(0.0)


def test_position_provider_allocations_empty_for_flat_symbol(store):
    allocations = store.get_position_provider_allocations("NEVERTRADED")
    assert allocations == {"symbol": "NEVERTRADED", "accounts": []}
