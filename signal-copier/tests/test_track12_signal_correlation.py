"""Track 12: cross-transport signal correlation/dedup.

Covers `app/signal_correlation.py`'s pure fingerprint/tolerance logic,
`SignalStore`'s new correlation-evidence read/write methods, and the
full `app/engine.py` integration -- corroborating evidence folds a
second transport's signal onto the same order (no duplicate submission),
while a materially conflicting one is held out of live routing and
surfaced as `CONFLICTING_SOURCE_DATA`, never silently resolved either
way. Composes with -- and must not weaken -- the existing within-
transport dedup covered by tests/test_telegram_cross_collector_dedup.py
and tests/test_sig01_duplicate_submission_protection.py."""
from datetime import datetime, timedelta, timezone

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OptionContractSpec, OrderStatus, AssetClass, Side, Signal
from app.routing import RoutingConfig, RoutingRule
from app.signal_correlation import (
    CorrelationOutcome,
    classify_candidate,
    fingerprint_key,
    prices_within_tolerance,
    within_timestamp_window,
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


# -- Pure fingerprint/tolerance logic -------------------------------------


def test_fingerprint_key_is_stable_across_source_case_and_symbol_case():
    a = Signal(source="BuyAlerts", symbol="btcusdt", side=Side.BUY)
    b = Signal(source="buyalerts", symbol="BTCUSDT", side=Side.BUY)
    assert fingerprint_key(a) == fingerprint_key(b)


@pytest.mark.parametrize(
    "other",
    [
        Signal(source="buyalerts", symbol="ETHUSDT", side=Side.BUY),
        Signal(source="buyalerts", symbol="BTCUSDT", side=Side.SELL),
        Signal(source="otherprovider", symbol="BTCUSDT", side=Side.BUY),
        Signal(source="buyalerts", symbol="BTCUSDT", side=Side.BUY, asset_class=AssetClass.FOREX),
    ],
)
def test_fingerprint_key_differs_when_a_discrete_field_differs(other):
    base = Signal(source="buyalerts", symbol="BTCUSDT", side=Side.BUY)
    assert fingerprint_key(base) != fingerprint_key(other)


def test_fingerprint_key_includes_option_strike_expiry_right():
    base = Signal(
        source="opts", symbol="SPY", side=Side.BUY, asset_class=AssetClass.OPTION,
        option=OptionContractSpec(underlying="SPY", expiry="2026-09-18", strike=450.0, right="call"),
    )
    different_strike = Signal(
        source="opts", symbol="SPY", side=Side.BUY, asset_class=AssetClass.OPTION,
        option=OptionContractSpec(underlying="SPY", expiry="2026-09-18", strike=460.0, right="call"),
    )
    same_again = Signal(
        source="opts", symbol="SPY", side=Side.BUY, asset_class=AssetClass.OPTION,
        option=OptionContractSpec(underlying="SPY", expiry="2026-09-18", strike=450.0, right="call"),
    )
    assert fingerprint_key(base) != fingerprint_key(different_strike)
    assert fingerprint_key(base) == fingerprint_key(same_again)


def test_prices_within_tolerance_accepts_small_gap_rejects_large_one():
    assert prices_within_tolerance(100.0, 100.4, tolerance_pct=0.005) is True
    assert prices_within_tolerance(100.0, 110.0, tolerance_pct=0.005) is False


def test_prices_within_tolerance_rejects_non_positive_values():
    assert prices_within_tolerance(0.0, 100.0, tolerance_pct=0.5) is False
    assert prices_within_tolerance(100.0, -5.0, tolerance_pct=0.5) is False


def test_within_timestamp_window_handles_naive_and_aware_datetimes():
    a = datetime.now(timezone.utc)
    b_naive = (a + timedelta(minutes=5)).replace(tzinfo=None)
    assert within_timestamp_window(a, b_naive, window_seconds=600) is True
    assert within_timestamp_window(a, b_naive, window_seconds=60) is False


def test_classify_candidate_returns_none_when_either_price_missing():
    now = datetime.now(timezone.utc)
    assert (
        classify_candidate(
            new_price=None, new_side="buy", new_received_at=now,
            candidate_price=100.0, candidate_side="buy", candidate_received_at=now,
        )
        is None
    )


def test_classify_candidate_returns_none_outside_timestamp_window():
    now = datetime.now(timezone.utc)
    far = now + timedelta(hours=6)
    assert (
        classify_candidate(
            new_price=100.0, new_side="buy", new_received_at=now,
            candidate_price=100.0, candidate_side="buy", candidate_received_at=far,
            window_seconds=900,
        )
        is None
    )


def test_classify_candidate_corroborating_when_side_and_price_agree():
    now = datetime.now(timezone.utc)
    outcome = classify_candidate(
        new_price=65010.0, new_side="buy", new_received_at=now,
        candidate_price=65000.0, candidate_side="buy", candidate_received_at=now,
        price_tolerance_pct=0.005,
    )
    assert outcome is CorrelationOutcome.CORROBORATING


def test_classify_candidate_conflicting_when_side_disagrees():
    now = datetime.now(timezone.utc)
    outcome = classify_candidate(
        new_price=65000.0, new_side="sell", new_received_at=now,
        candidate_price=65000.0, candidate_side="buy", candidate_received_at=now,
    )
    assert outcome is CorrelationOutcome.CONFLICTING


def test_classify_candidate_conflicting_when_price_disagrees_beyond_tolerance():
    now = datetime.now(timezone.utc)
    outcome = classify_candidate(
        new_price=70000.0, new_side="buy", new_received_at=now,
        candidate_price=65000.0, candidate_side="buy", candidate_received_at=now,
        price_tolerance_pct=0.005,
    )
    assert outcome is CorrelationOutcome.CONFLICTING


# -- SignalStore read/write methods ----------------------------------------


def test_find_correlation_candidates_excludes_same_channel_and_out_of_window(store):
    now = datetime.now(timezone.utc)
    same_channel = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, price=65000.0,
        channel_id="telegram:chan1", message_id="1", received_at=now,
    )
    store.save_signal(same_channel)
    other_channel_in_window = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, price=65010.0,
        channel_id="whop-device:com.whop.whop", message_id="w1", received_at=now + timedelta(minutes=2),
    )
    store.save_signal(other_channel_in_window)
    other_channel_out_of_window = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, price=65010.0,
        channel_id="email:inbox1", message_id="e1", received_at=now + timedelta(hours=6),
    )
    store.save_signal(other_channel_out_of_window)

    key = fingerprint_key(same_channel)
    candidates = store.find_correlation_candidates(
        fingerprint_key=key,
        exclude_channel_id="telegram:chan1",
        since=now - timedelta(minutes=15),
        until=now + timedelta(minutes=15),
    )
    ids = {c["id"] for c in candidates}
    assert other_channel_in_window.id in ids
    assert same_channel.id not in ids  # same channel excluded
    assert other_channel_out_of_window.id not in ids  # outside the window


def test_record_and_list_signal_correlation_evidence(store):
    now = datetime.now(timezone.utc)
    evidence_id = store.record_signal_correlation_evidence(
        canonical_signal_id="sig-canonical",
        evidence_signal_id="sig-evidence",
        fingerprint_key="fp-1",
        source="whop",
        channel_id="whop-device:com.whop.whop",
        message_id="w1",
        price=65010.0,
        side="buy",
        received_at=now,
        match_type="corroborating",
    )
    assert evidence_id
    rows = store.list_signal_correlation_evidence("sig-canonical")
    assert len(rows) == 1
    assert rows[0]["match_type"] == "corroborating"
    assert rows[0]["evidence_signal_id"] == "sig-evidence"


def test_list_conflicting_signal_correlations_only_returns_conflicting(store):
    now = datetime.now(timezone.utc)
    store.record_signal_correlation_evidence(
        canonical_signal_id="sig-a", evidence_signal_id="sig-a2", fingerprint_key="fp-a",
        source="whop", channel_id="c1", message_id="m1", price=100.0, side="buy",
        received_at=now, match_type="corroborating",
    )
    store.record_signal_correlation_evidence(
        canonical_signal_id="sig-b", evidence_signal_id="sig-b2", fingerprint_key="fp-b",
        source="whop", channel_id="c2", message_id="m2", price=200.0, side="buy",
        received_at=now, match_type="conflicting",
    )
    conflicts = store.list_conflicting_signal_correlations()
    assert len(conflicts) == 1
    assert conflicts[0]["canonical_signal_id"] == "sig-b"


# -- Full engine integration -----------------------------------------------


@pytest.mark.asyncio
async def test_second_transport_corroborating_signal_does_not_submit_a_second_order(store):
    """The same real trade, first via Telegram then via Whop's
    notification bridge -- different channel_id/message_id, no shared
    provider identity at all, but the SAME parsed content within
    tolerance. Must produce exactly one broker order."""
    engine, broker = _engine(store)

    place_order_calls = []
    original_place_order = broker.place_order

    async def tracking_place_order(*args, **kwargs):
        place_order_calls.append(args)
        return await original_place_order(*args, **kwargs)

    broker.place_order = tracking_place_order

    now = datetime.now(timezone.utc)
    from_telegram = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65000.0,
        channel_id="telegram:chan1", message_id="msg-1", received_at=now,
    )
    from_whop = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65010.0,
        channel_id="phone-1:com.whop.whop", message_id="whop-key-1", received_at=now + timedelta(minutes=1),
    )

    original_whop_id = from_whop.id  # captured BEFORE handle_signal mutates it in place

    first = await engine.handle_signal(from_telegram)
    second = await engine.handle_signal(from_whop)

    assert len(place_order_calls) == 1  # only ONE real broker order
    assert first[0].status == OrderStatus.FILLED
    assert second[0].status == OrderStatus.FILLED
    assert second[0].broker_order_id == first[0].broker_order_id
    # The Whop signal's own id was canonicalized onto the Telegram one's
    # -- same mutate-in-place convention as the exact-identity dedup path
    # (see find_signal_id_by_provider_identity's own docstring).
    assert from_whop.id == from_telegram.id

    evidence = store.list_signal_correlation_evidence(from_telegram.id)
    assert len(evidence) == 1
    assert evidence[0]["match_type"] == "corroborating"
    assert evidence[0]["evidence_signal_id"] == original_whop_id


@pytest.mark.asyncio
async def test_second_transport_conflicting_signal_is_held_for_review_not_routed(store):
    """Same provider/symbol/side/window, but the Whop-sourced price is
    wildly different from the Telegram one -- CONFLICTING_SOURCE_DATA:
    never silently resolved, never routed live."""
    engine, broker = _engine(store)

    place_order_calls = []
    original_place_order = broker.place_order

    async def tracking_place_order(*args, **kwargs):
        place_order_calls.append(args)
        return await original_place_order(*args, **kwargs)

    broker.place_order = tracking_place_order

    now = datetime.now(timezone.utc)
    from_telegram = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65000.0,
        channel_id="telegram:chan1", message_id="msg-1", received_at=now,
    )
    from_whop = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=95000.0,  # wildly different
        channel_id="phone-1:com.whop.whop", message_id="whop-key-1", received_at=now + timedelta(minutes=1),
    )

    first = await engine.handle_signal(from_telegram)
    second = await engine.handle_signal(from_whop)

    assert len(place_order_calls) == 1  # the conflicting signal never reached the broker
    assert first[0].status == OrderStatus.FILLED
    assert len(second) == 1
    assert second[0].status == OrderStatus.REJECTED
    assert "CONFLICTING_SOURCE_DATA" in second[0].message

    conflicts = store.list_conflicting_signal_correlations()
    assert len(conflicts) == 1
    assert conflicts[0]["canonical_signal_id"] == from_telegram.id
    assert conflicts[0]["evidence_signal_id"] == from_whop.id

    # The conflicting signal is still recorded (audit trail), tagged, and
    # never becomes a second signal id that could confuse SIG-01 replay.
    with store._connect() as conn:
        row = conn.execute("SELECT import_batch FROM signals WHERE id = ?", (from_whop.id,)).fetchone()
    assert row is not None
    assert row[0] == f"cross_transport_conflict:{from_telegram.id}"


@pytest.mark.asyncio
async def test_correlation_never_fires_for_signals_with_no_provider_identity(store):
    """Guard invariant: a signal with no channel_id/message_id at all
    (most existing adapters/test fixtures) must never participate in
    fingerprint correlation -- see
    tests/test_telegram_cross_collector_dedup.py's own identical
    invariant for the within-transport layer this must not interfere
    with."""
    engine, broker = _engine(store)

    place_order_calls = []
    original_place_order = broker.place_order

    async def tracking_place_order(*args, **kwargs):
        place_order_calls.append(args)
        return await original_place_order(*args, **kwargs)

    broker.place_order = tracking_place_order

    a = Signal(source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65000.0)
    b = Signal(source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65000.0)
    assert a.channel_id is None and b.channel_id is None

    await engine.handle_signal(a)
    await engine.handle_signal(b)

    assert len(place_order_calls) == 2  # two independent orders, not correlated


@pytest.mark.asyncio
async def test_correlation_layer_can_be_disabled_via_config(store, monkeypatch):
    from app import config as app_config

    monkeypatch.setattr(app_config, "SIGNAL_CORRELATION_ENABLED", False)
    engine, broker = _engine(store)

    place_order_calls = []
    original_place_order = broker.place_order

    async def tracking_place_order(*args, **kwargs):
        place_order_calls.append(args)
        return await original_place_order(*args, **kwargs)

    broker.place_order = tracking_place_order

    now = datetime.now(timezone.utc)
    from_telegram = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65000.0,
        channel_id="telegram:chan1", message_id="msg-1", received_at=now,
    )
    from_whop = Signal(
        source="buyalerts", symbol="BTCUSDT", side=Side.BUY, quantity=0.01, price=65010.0,
        channel_id="phone-1:com.whop.whop", message_id="whop-key-1", received_at=now + timedelta(minutes=1),
    )

    await engine.handle_signal(from_telegram)
    await engine.handle_signal(from_whop)

    # With correlation disabled, these are two ordinary, independent
    # signals -- both get their own order.
    assert len(place_order_calls) == 2
