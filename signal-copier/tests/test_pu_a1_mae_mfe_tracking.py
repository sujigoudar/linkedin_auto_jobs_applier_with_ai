"""PU-A1: real MAE/MFE (maximum adverse/favorable excursion) tracking per
position -- driven ONLY by real price observations (a real entry fill
price, or a real feed tick via PriceMonitor/on_price_update), never a
fabricated value.

Covers: correct tracking of a real sequence of price observations for both
long and short positions, correctly reversing which extreme is "adverse"
vs "favorable" by side, persistence across a position's full lifecycle
including after close (via SignalStore.position_excursions), and an
honest "no real price feed available" outcome for an account/symbol with
no price feed at all.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan
from app.models import DestinationAccount, Side, Signal
from app.pricing import PriceMonitor


@pytest.fixture
def account():
    return DestinationAccount(account_id="acct1", broker="paper")


@pytest.fixture
def broker():
    return PaperBroker()


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


@pytest.fixture
def manager(broker, store):
    return PositionLifecycleManager(brokers={"paper": broker}, store=store)


async def _enter(manager, broker, account, plan, filled_quantity, entry_price=None):
    manager.start_plan(plan)
    entry_signal = Signal(source="test", symbol=plan.symbol, side=plan.side)
    await broker.place_order(entry_signal, account, filled_quantity, plan.symbol)
    return await manager.on_entry_fill(account, plan.symbol, filled_quantity, entry_price=entry_price)


def _plan(**overrides) -> PositionPlan:
    defaults = dict(
        account_id="acct1",
        symbol="AAPL",
        side=Side.BUY,
        planned_quantity=100.0,
        broker="paper",
        initial_stop=90.0,
    )
    defaults.update(overrides)
    return PositionPlan(**defaults)


@pytest.mark.asyncio
async def test_long_mae_mfe_tracks_a_real_sequence_of_price_observations(account, broker, manager):
    """A long entered at 100: price dips to 95 (adverse), then rallies to
    112 (favorable), then settles at 105. MAE must reflect the worst dip
    (100 -> 95 = 5), MFE the best rally (100 -> 112 = 12) -- not the final
    price."""
    plan = _plan(side=Side.BUY)
    await _enter(manager, broker, account, plan, 100.0, entry_price=100.0)

    for price in (98.0, 95.0, 103.0, 112.0, 105.0):
        await manager.on_price_update(account, "AAPL", price)

    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.entry_price == 100.0
    assert lifecycle.lowest_price_since_entry == 95.0
    assert lifecycle.highest_price_since_entry == 112.0
    assert lifecycle.mae == pytest.approx(5.0)
    assert lifecycle.mfe == pytest.approx(12.0)
    assert lifecycle.has_price_data is True


@pytest.mark.asyncio
async def test_short_mae_mfe_reverses_which_extreme_is_adverse_vs_favorable(account, broker, manager):
    """A short entered at 100: price RISING to 108 is the adverse move (a
    short loses when price goes up), price FALLING to 90 is the favorable
    move -- the exact opposite of the long case above. This is the single
    most correctness-critical invariant this slice adds (see this file's
    LOAD_BEARING_VERIFY note in the PR description) -- a short's MAE/MFE
    must never be computed with the long-side formula."""
    plan = _plan(side=Side.SELL, symbol="TSLA")
    await _enter(manager, broker, account, plan, 50.0, entry_price=100.0)

    for price in (103.0, 108.0, 95.0, 90.0, 96.0):
        await manager.on_price_update(account, "TSLA", price)

    lifecycle = manager.get_lifecycle("acct1", "TSLA")
    assert lifecycle.highest_price_since_entry == 108.0
    assert lifecycle.lowest_price_since_entry == 90.0
    # Adverse for a short = price INCREASE: 108 - 100 = 8, not 100 - 90.
    assert lifecycle.mae == pytest.approx(8.0)
    # Favorable for a short = price DECREASE: 100 - 90 = 10, not 108 - 100.
    assert lifecycle.mfe == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_mae_mfe_are_never_negative_when_price_only_moves_one_direction(account, broker, manager):
    """A long that only ever rallies from entry has an MAE of 0.0 (no
    adverse move observed), not a negative number."""
    plan = _plan(side=Side.BUY, symbol="MSFT")
    await _enter(manager, broker, account, plan, 10.0, entry_price=200.0)

    for price in (205.0, 210.0, 220.0):
        await manager.on_price_update(account, "MSFT", price)

    lifecycle = manager.get_lifecycle("acct1", "MSFT")
    assert lifecycle.mae == pytest.approx(0.0)
    assert lifecycle.mfe == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_no_price_feed_available_stays_honest_not_fabricated(account, manager, store):
    """A broker with NO get_last_price/has_last_price_capability (PaperBroker
    is exactly this -- see app/brokers/paper.py's docstring) means
    PriceMonitor never calls on_price_update for this position at all. MAE/
    MFE must then reflect exactly what a real entry-price observation
    proves (0.0 excursion, since nothing has moved from entry) -- not
    invent any further movement that was never actually observed."""
    broker = PaperBroker()
    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    plan = _plan(side=Side.BUY, symbol="NFLX")
    await _enter(manager, broker, account, plan, 5.0, entry_price=300.0)

    assert broker.has_last_price_capability is False
    monitor = PriceMonitor(lifecycle_manager=manager, brokers={"paper": broker})
    updated = await monitor.poll_once()
    assert updated == 0  # honestly skipped -- no feed to poll

    lifecycle = manager.get_lifecycle("acct1", "NFLX")
    # Only the real entry-fill observation ever happened -- both extremes
    # sit exactly at entry price, and MAE/MFE both report a real, observed
    # 0.0, not an unknown or a guessed movement.
    assert lifecycle.highest_price_since_entry == 300.0
    assert lifecycle.lowest_price_since_entry == 300.0
    assert lifecycle.mae == pytest.approx(0.0)
    assert lifecycle.mfe == pytest.approx(0.0)
    assert lifecycle.has_price_data is True


@pytest.mark.asyncio
async def test_no_entry_price_and_no_observation_reports_mae_mfe_as_unknown_not_zero(account, broker, manager):
    """Genuinely unknown (no entry price captured at all, and no real price
    observation has arrived) must be None, never a fabricated 0.0 --
    "unknown" and "zero excursion" are different facts."""
    plan = _plan(side=Side.BUY, symbol="GOOG")
    await _enter(manager, broker, account, plan, 3.0, entry_price=None)

    lifecycle = manager.get_lifecycle("acct1", "GOOG")
    assert lifecycle.has_price_data is False
    assert lifecycle.mae is None
    assert lifecycle.mfe is None


@pytest.mark.asyncio
async def test_mae_mfe_persists_through_full_lifecycle_including_after_close(account, broker, manager, store):
    """The final MAE/MFE for a position that has fully closed must remain
    queryable afterward -- via SignalStore.position_excursions, not just
    while the lifecycle is open in memory."""
    plan = _plan(side=Side.BUY, symbol="AMD")
    await _enter(manager, broker, account, plan, 20.0, entry_price=50.0)

    for price in (48.0, 45.0, 55.0):
        await manager.on_price_update(account, "AMD", price)

    # Fully close the position via the normal exit path.
    result = await manager.request_exit(account, "AMD", 20.0, source="test", reason="closing out")
    assert result.status.value == "filled"

    lifecycle = manager.get_lifecycle("acct1", "AMD")
    assert lifecycle.closed is True
    assert lifecycle.mae == pytest.approx(5.0)  # 50 -> 45
    assert lifecycle.mfe == pytest.approx(5.0)  # 50 -> 55

    rows = store.list_position_excursions(account_id="acct1", symbol="AMD")
    assert len(rows) == 1
    row = rows[0]
    assert row["side"] == "buy"
    assert row["entry_price"] == 50.0
    assert row["highest_price_since_entry"] == 55.0
    assert row["lowest_price_since_entry"] == 45.0
    assert row["mae"] == pytest.approx(5.0)
    assert row["mfe"] == pytest.approx(5.0)
    assert row["has_price_data"] is True
    assert row["closed_at"] is not None

    # The in-progress lifecycle_state row for this now-closed position is
    # gone (see PositionLifecycleManager._apply_exit_fill) -- the excursion
    # row above is the durable historical record from here on.
    assert store.load_lifecycle_states() == []


@pytest.mark.asyncio
async def test_mae_mfe_finalized_on_close_via_stop_fill(account, broker, manager, store):
    """A position closed by its protective stop actually filling (not a
    logical exit) must ALSO finalize its excursion record -- on_stop_filled
    is a separate closing path from request_exit/resolve_pending_exit."""
    plan = _plan(side=Side.BUY, symbol="INTC", planned_quantity=10.0, initial_stop=45.0)
    await _enter(manager, broker, account, plan, 10.0, entry_price=50.0)

    await manager.on_price_update(account, "INTC", 47.0)
    await manager.on_price_update(account, "INTC", 44.0)  # would trigger a real stop

    fills = broker.simulate_price("INTC", 44.0)
    assert len(fills) == 1
    await manager.on_stop_filled(account, "INTC", fills[0].filled_quantity, fills[0].filled_price)

    lifecycle = manager.get_lifecycle("acct1", "INTC")
    assert lifecycle.closed is True

    rows = store.list_position_excursions(account_id="acct1", symbol="INTC")
    assert len(rows) == 1
    assert rows[0]["lowest_price_since_entry"] == 44.0
    assert rows[0]["mae"] == pytest.approx(6.0)  # 50 -> 44


@pytest.mark.asyncio
async def test_open_positions_snapshot_exposes_mae_mfe_fields(account, broker, manager):
    """GET /positions' managed_lifecycles snapshot (app/main.py's
    _managed_lifecycle_snapshot) is built directly from these same
    PositionLifecycle fields -- this test pins the exact attribute names a
    later Phase B chart batch will read."""
    plan = _plan(side=Side.SELL, symbol="AMZN")
    await _enter(manager, broker, account, plan, 4.0, entry_price=150.0)
    await manager.on_price_update(account, "AMZN", 160.0)
    await manager.on_price_update(account, "AMZN", 140.0)

    lifecycle = manager.get_lifecycle("acct1", "AMZN")
    assert lifecycle.highest_price_at is not None
    assert lifecycle.lowest_price_at is not None
    assert isinstance(lifecycle.highest_price_at, datetime)
    assert lifecycle.highest_price_at.tzinfo is not None


@pytest.mark.asyncio
async def test_excursion_state_survives_a_restart_before_close(account, broker, tmp_path):
    """In-progress MAE/MFE (not yet closed) must resume after a restart via
    restore_from_store -- not just the final, closed value."""
    store = SignalStore(tmp_path / "restart.db")
    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    plan = _plan(side=Side.BUY, symbol="ORCL")
    await _enter(manager, broker, account, plan, 8.0, entry_price=80.0)
    await manager.on_price_update(account, "ORCL", 72.0)

    # Simulate a restart: a fresh manager over the same store.
    resumed = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    await resumed.restore_from_store()

    lifecycle = resumed.get_lifecycle("acct1", "ORCL")
    assert lifecycle is not None
    assert lifecycle.entry_price == 80.0
    assert lifecycle.lowest_price_since_entry == 72.0
    assert lifecycle.mae == pytest.approx(8.0)
