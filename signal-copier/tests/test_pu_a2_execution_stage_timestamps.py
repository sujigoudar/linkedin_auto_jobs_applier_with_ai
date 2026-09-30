"""PU-A2: real multi-stage execution-latency timestamp capture.

Covers both layers:
  - app/execution_quality.py's stage-latency computation from known,
    directly-stored timestamps (unit level, mirroring test_e05_*.py's own
    style).
  - app/engine.py actually populating those timestamps on a real
    handle_signal() call, for both a managed_lifecycle account (which can
    reach every stage, including protection acknowledgement) and a plain
    account (which never reaches protection_confirmed_at -- verifying that
    absence is honest, not a bug).
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.execution_quality import compute_execution_quality
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule

ACCOUNT_ID = "acct1"


def _signal(symbol: str, received_at: datetime) -> Signal:
    return Signal(source="test", symbol=symbol, side=Side.BUY, received_at=received_at)


def _engine(store, paper, account):
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=[account.account_id])],
        accounts={account.account_id: account},
    )
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": paper}, store=store)
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": paper}, store=store, lifecycle_manager=lifecycle_manager
    )
    return engine, lifecycle_manager


# --- Unit level: compute_execution_quality's stage arithmetic from known timestamps ---


def test_stage_latencies_computed_from_known_timestamps(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    signal = _signal("AAPL", t0)
    store.save_signal(signal)
    store.save_order_result(
        OrderResult(
            account_id=ACCOUNT_ID,
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            filled_quantity=10.0,
            filled_price=100.0,
            executed_at=t0 + timedelta(seconds=5),
        ),
        broker="paper",
        symbol="AAPL",
        side=Side.BUY,
        submitted_at=t0 + timedelta(seconds=1),
        protection_confirmed_at=t0 + timedelta(seconds=7),
    )

    result = compute_execution_quality(store, ACCOUNT_ID)

    stages = {s.stage: s for s in result.stage_latencies["AAPL"]}
    assert stages["receipt_to_submission"].mean_seconds == 1.0
    assert stages["submission_to_fill"].mean_seconds == 4.0  # 5 - 1
    assert stages["fill_to_protection"].mean_seconds == 2.0  # 7 - 5
    for stage in stages.values():
        assert stage.sample_count == 1


def test_stage_missing_one_endpoint_is_honestly_omitted_not_bridged(tmp_path):
    """An order with submitted_at but no protection_confirmed_at (e.g. a
    plain, non-managed_lifecycle account) must never contribute a
    fill_to_protection sample -- that stage doesn't exist for this order at
    all, and must not silently default to 0 or be skipped by falling back
    to some other timestamp."""
    store = SignalStore(tmp_path / "test.db")
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    signal = _signal("AAPL", t0)
    store.save_signal(signal)
    store.save_order_result(
        OrderResult(
            account_id=ACCOUNT_ID,
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            filled_quantity=10.0,
            filled_price=100.0,
            executed_at=t0 + timedelta(seconds=5),
        ),
        broker="paper",
        symbol="AAPL",
        side=Side.BUY,
        submitted_at=t0 + timedelta(seconds=1),
        protection_confirmed_at=None,
    )

    result = compute_execution_quality(store, ACCOUNT_ID)

    stage_names = {s.stage for s in result.stage_latencies["AAPL"]}
    assert stage_names == {"receipt_to_submission", "submission_to_fill"}
    assert "fill_to_protection" not in stage_names


def test_order_with_no_stage_timestamps_at_all_reports_no_stages(tmp_path):
    """A pre-PU-A2 order (submitted_at/protection_confirmed_at both never
    set) must still compute the original E05 per_symbol latency, with an
    entirely empty stage breakdown for that symbol -- additive, never
    breaking the pre-existing report."""
    store = SignalStore(tmp_path / "test.db")
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    signal = _signal("AAPL", t0)
    store.save_signal(signal)
    store.save_order_result(
        OrderResult(
            account_id=ACCOUNT_ID,
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            filled_quantity=10.0,
            filled_price=100.0,
            executed_at=t0 + timedelta(seconds=5),
        ),
        broker="paper",
        symbol="AAPL",
        side=Side.BUY,
    )

    result = compute_execution_quality(store, ACCOUNT_ID)

    assert result.per_symbol["AAPL"].mean_seconds == 5.0
    assert "AAPL" not in result.stage_latencies


# --- Integration level: app/engine.py actually populating these timestamps ---


@pytest.mark.asyncio
async def test_managed_entry_populates_submission_and_protection_timestamps(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    paper = PaperBroker()
    account = DestinationAccount(account_id=ACCOUNT_ID, broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)

    before = datetime.now(timezone.utc)
    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=48.50)
    results = await engine.handle_signal(signal)
    after = datetime.now(timezone.utc)

    assert results[0].status == OrderStatus.FILLED
    # PaperBroker.place_protective_stop returns PENDING with a real
    # broker_order_id -- STOP_CONFIRMED, synchronously, within the same
    # handle_signal call.
    lifecycle = lifecycle_manager.get_lifecycle(ACCOUNT_ID, "AAPL")
    assert lifecycle.stop.confirmed_at is not None

    rows = store.list_filled_orders_with_signal_timing(ACCOUNT_ID)
    assert len(rows) == 1
    row = rows[0]
    submitted_at = datetime.fromisoformat(row["submitted_at"])
    protection_confirmed_at = datetime.fromisoformat(row["protection_confirmed_at"])
    assert before <= submitted_at <= after
    assert before <= protection_confirmed_at <= after
    assert submitted_at <= protection_confirmed_at  # submission happens before protection is confirmed

    quality = compute_execution_quality(store, ACCOUNT_ID)
    stages = {s.stage for s in quality.stage_latencies["AAPL"]}
    assert "receipt_to_submission" in stages
    assert "submission_to_fill" in stages
    assert "fill_to_protection" in stages


@pytest.mark.asyncio
async def test_plain_account_entry_never_gets_a_protection_timestamp(tmp_path):
    """A non-managed_lifecycle account's entry has a real submission
    instant, but this schema has no separately-confirmed protection leg for
    it at all (see app/execution_quality.py's module docstring) -- must stay
    honestly None, never copied from executed_at or any other field."""
    store = SignalStore(tmp_path / "test.db")
    paper = PaperBroker()
    account = DestinationAccount(account_id=ACCOUNT_ID, broker="paper", managed_lifecycle=False)
    engine, _ = _engine(store, paper, account)

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    results = await engine.handle_signal(signal)
    assert results[0].status == OrderStatus.FILLED

    rows = store.list_filled_orders_with_signal_timing(ACCOUNT_ID)
    assert len(rows) == 1
    assert rows[0]["submitted_at"] is not None
    assert rows[0]["protection_confirmed_at"] is None

    quality = compute_execution_quality(store, ACCOUNT_ID)
    stage_names = {s.stage for s in quality.stage_latencies.get("AAPL", [])}
    assert "fill_to_protection" not in stage_names


@pytest.mark.asyncio
async def test_managed_entry_rejected_before_broker_call_has_no_submitted_at(tmp_path):
    """An entry refused for having no resolvable stop never reaches
    broker.place_order at all -- submitted_at must stay honestly None, not
    fabricated from whatever `executed_at` the rejection's OrderResult
    happens to carry (its own construction-time default)."""
    store = SignalStore(tmp_path / "test.db")
    paper = PaperBroker()
    account = DestinationAccount(account_id=ACCOUNT_ID, broker="paper", managed_lifecycle=True)
    engine, _ = _engine(store, paper, account)

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)  # no stop_loss
    results = await engine.handle_signal(signal)
    assert results[0].status == OrderStatus.REJECTED

    rows = store.list_recent_orders(account_id=ACCOUNT_ID)
    assert len(rows) == 1
    assert rows[0]["status"] == "rejected"
    # never reached the broker at all
    assert paper.positions.get(ACCOUNT_ID, {}).get("AAPL", 0.0) == 0.0

    conn = sqlite3.connect(tmp_path / "test.db")
    submitted_at, protection_confirmed_at = conn.execute(
        "SELECT submitted_at, protection_confirmed_at FROM orders WHERE account_id = ?", (ACCOUNT_ID,)
    ).fetchone()
    conn.close()
    assert submitted_at is None
    assert protection_confirmed_at is None
