"""app/trade_episode.py: one authoritative episode per position lifecycle,
including a managed-lifecycle stop/target/time_exit fill -- the exact gap
a release review found in the closing-fill-based app/provider_value.py
replay. See app/lifecycle/manager.py's TR-EPISODE-01 fix (the companion
change that makes those exits real `orders` rows in the first place)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db import SignalStore
from app.models import AssetClass, OrderResult, OrderStatus, Side, Signal
from app.trade_episode import compute_trade_episodes


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _save(
    store,
    account_id,
    symbol,
    side,
    quantity,
    price,
    when,
    *,
    source="alice_calls",
    analyst=None,
    asset_class=AssetClass.EQUITY,
    purpose="entry",
    family_id=None,
    raw=None,
):
    signal = Signal(source=source, symbol=symbol, side=side, analyst=analyst, asset_class=asset_class, raw=raw or {})
    store.save_signal(signal)
    result = OrderResult(
        account_id=account_id,
        status=OrderStatus.FILLED,
        signal_id=signal.id,
        filled_quantity=quantity,
        filled_price=price,
        executed_at=when,
    )
    store.save_order_result(
        result, broker="paper", symbol=symbol, side=side, purpose=purpose, family_id=family_id, applied_quantity=quantity
    )
    return signal.id


def test_manual_round_trip_is_one_closed_winning_episode(store):
    """Mirrors app/engine.py's real `family_id` contract: an entry's family
    is its own signal id, and its eventual close carries that same id
    (see `orders.family_id`'s schema comment)."""
    t0 = datetime.now(timezone.utc)
    _save(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0, purpose="entry", family_id="fam-round-trip")
    _save(
        store,
        "acct1",
        "AAPL",
        Side.SELL,
        10.0,
        110.0,
        t0 + timedelta(minutes=5),
        purpose="close",
        family_id="fam-round-trip",
    )
    episodes, skipped = compute_trade_episodes(store)
    assert skipped == 0
    [episode] = list(episodes.values())
    assert episode.outcome == "win"
    assert episode.realized_pnl == pytest.approx(100.0)
    assert episode.is_closed
    assert episode.reducing_execution_count == 1
    assert len(episode.reduction_executions) == 1


def test_stop_exit_loss_is_counted_against_the_provider(store):
    """The exact gap the review flagged: a stop-out loss must count
    against the provider's scorecard, not vanish."""
    t0 = datetime.now(timezone.utc)
    entry_id = _save(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0, source="bob_signals", family_id="fam-1")
    _save(
        store,
        "acct1",
        "AAPL",
        Side.SELL,
        10.0,
        90.0,
        t0 + timedelta(minutes=5),
        source="bob_signals",
        purpose="stop_exit",
        family_id="fam-1",
        raw={"reason": "stop"},
    )
    episodes, skipped = compute_trade_episodes(store)
    assert skipped == 0
    [episode] = list(episodes.values())
    assert episode.outcome == "loss"
    assert episode.realized_pnl == pytest.approx(-100.0)
    assert len(episode.stop_executions) == 1
    assert len(episode.trailing_stop_executions) == 0


def test_trailing_stop_exit_is_reported_separately_from_a_plain_stop(store):
    t0 = datetime.now(timezone.utc)
    _save(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0, family_id="fam-2")
    _save(
        store,
        "acct1",
        "AAPL",
        Side.SELL,
        10.0,
        120.0,
        t0 + timedelta(minutes=5),
        purpose="stop_exit",
        family_id="fam-2",
        raw={"reason": "trailing_stop"},
    )
    episodes, _ = compute_trade_episodes(store)
    [episode] = list(episodes.values())
    assert len(episode.trailing_stop_executions) == 1
    assert len(episode.stop_executions) == 0
    assert episode.outcome == "win"


def test_three_partial_exits_are_one_episode_not_three(store):
    """The review's other explicit complaint: several reductions of the
    same trade must behave like ONE trade, not several independent ones."""
    t0 = datetime.now(timezone.utc)
    _save(store, "acct1", "AAPL", Side.BUY, 30.0, 100.0, t0, family_id="fam-3")
    _save(store, "acct1", "AAPL", Side.SELL, 10.0, 105.0, t0 + timedelta(minutes=1), purpose="target_exit", family_id="fam-3")
    _save(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=2), purpose="target_exit", family_id="fam-3")
    _save(store, "acct1", "AAPL", Side.SELL, 10.0, 90.0, t0 + timedelta(minutes=3), purpose="stop_exit", family_id="fam-3", raw={"reason": "stop"})

    episodes, _ = compute_trade_episodes(store)
    assert len(episodes) == 1
    [episode] = list(episodes.values())
    assert episode.is_closed
    assert episode.reducing_execution_count == 3
    # (105-100)*10 + (110-100)*10 + (90-100)*10 = 50 + 100 - 100 = 50
    assert episode.realized_pnl == pytest.approx(50.0)
    assert episode.outcome == "win"


def test_unknown_exit_price_makes_realized_pnl_unknown_not_zero(store):
    t0 = datetime.now(timezone.utc)
    _save(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0, family_id="fam-4")
    # A stop exit whose fill price genuinely isn't known yet (see
    # resolve_pending_exit's own disclosed gap) -- filled_price=None.
    signal = Signal(source="alice_calls", symbol="AAPL", side=Side.SELL, asset_class=AssetClass.EQUITY)
    store.save_signal(signal)
    result = OrderResult(
        account_id="acct1", status=OrderStatus.FILLED, signal_id=signal.id,
        filled_quantity=10.0, filled_price=None, executed_at=t0 + timedelta(minutes=5),
    )
    store.save_order_result(
        result, broker="paper", symbol="AAPL", side=Side.SELL, purpose="stop_exit", family_id="fam-4",
        applied_quantity=10.0,
    )

    episodes, _ = compute_trade_episodes(store)
    [episode] = list(episodes.values())
    assert episode.has_unknown_price_execution is True
    assert episode.realized_pnl is None
    assert episode.outcome == "unknown"


def test_orders_with_no_family_id_are_excluded_not_guessed(store):
    t0 = datetime.now(timezone.utc)
    _save(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0, family_id=None)
    _save(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=1), family_id=None)

    episodes, skipped = compute_trade_episodes(store)
    assert episodes == {}
    assert skipped == 2


def test_open_episode_reports_outcome_open(store):
    t0 = datetime.now(timezone.utc)
    _save(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0, family_id="fam-5")

    episodes, _ = compute_trade_episodes(store)
    [episode] = list(episodes.values())
    assert episode.outcome == "open"
    assert not episode.is_closed
