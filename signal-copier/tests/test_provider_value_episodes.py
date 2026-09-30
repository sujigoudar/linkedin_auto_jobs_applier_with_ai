"""app/provider_value.py's episode-based scoring
(`compute_provider_value_from_episodes`/`compute_provider_value_report_
from_episodes`) -- the corrected replacement for the closing-fill-based
`compute_provider_value`, and app/provider_scout.py's use of it."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db import SignalStore
from app.models import AssetClass, OrderResult, OrderStatus, Side, Signal
from app.provider_scout import ProviderScout
from app.provider_value import compute_provider_value_from_episodes


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _save(store, account_id, symbol, side, quantity, price, when, *, source="alice_calls", purpose="entry", family_id=None, raw=None):
    signal = Signal(source=source, symbol=symbol, side=side, asset_class=AssetClass.EQUITY, raw=raw or {})
    store.save_signal(signal)
    result = OrderResult(
        account_id=account_id, status=OrderStatus.FILLED, signal_id=signal.id,
        filled_quantity=quantity, filled_price=price, executed_at=when,
    )
    store.save_order_result(
        result, broker="paper", symbol=symbol, side=side, purpose=purpose, family_id=family_id, applied_quantity=quantity
    )


def test_stop_out_loss_counts_against_provider_score(store):
    """The review's central complaint: a provider whose losses exit via a
    managed-lifecycle stop must be scored on them exactly like a manual
    close -- not have them silently absent."""
    t0 = datetime.now(timezone.utc)
    # A winning MANUAL close for provider "good_looking_but_risky" ...
    _save(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0, source="good_looking_but_risky", family_id="fam-a")
    _save(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=1), source="good_looking_but_risky", purpose="close", family_id="fam-a")
    # ... and a much bigger STOP-OUT loss for the SAME provider.
    _save(store, "acct1", "MSFT", Side.BUY, 10.0, 200.0, t0 + timedelta(minutes=2), source="good_looking_but_risky", family_id="fam-b")
    _save(
        store, "acct1", "MSFT", Side.SELL, 10.0, 150.0, t0 + timedelta(minutes=3),
        source="good_looking_but_risky", purpose="stop_exit", family_id="fam-b", raw={"reason": "stop"},
    )

    values = compute_provider_value_from_episodes(store)
    pv = values[("good_looking_but_risky", None, "equity")]

    assert pv.closed_episodes == 2
    assert pv.winning_episodes == 1
    assert pv.losing_episodes == 1
    # 10*(110-100) - 10*(200-150) = 100 - 500 = -400: the stop-out loss
    # dominates -- exactly what the old closing-fill replay would have
    # missed if the stop exit never reached the orders table.
    assert pv.realized_pnl == pytest.approx(-400.0)
    assert pv.win_rate == pytest.approx(0.5)


def test_three_partial_exits_score_as_one_episode_not_three(store):
    t0 = datetime.now(timezone.utc)
    _save(store, "acct1", "AAPL", Side.BUY, 30.0, 100.0, t0, source="chatty_provider", family_id="fam-c")
    for i, (price, purpose) in enumerate([(105.0, "target_exit"), (110.0, "target_exit"), (95.0, "close")]):
        _save(
            store, "acct1", "AAPL", Side.SELL, 10.0, price, t0 + timedelta(minutes=i + 1),
            source="chatty_provider", purpose=purpose, family_id="fam-c",
        )

    values = compute_provider_value_from_episodes(store)
    pv = values[("chatty_provider", None, "equity")]
    assert pv.total_episodes == 1
    assert pv.closed_episodes == 1


def test_provider_scout_promotes_from_episode_scoring(store):
    """ProviderScout must read the episode-based report, not the
    deprecated closing-fill one -- a scout that still promoted on stale
    per-fill counts would recreate the exact bias the review flagged."""
    t0 = datetime.now(timezone.utc)
    for i in range(12):
        _save(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0 + timedelta(minutes=i * 2), source="promising_free_provider", family_id=f"fam-{i}")
        _save(
            store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=i * 2 + 1),
            source="promising_free_provider", purpose="target_exit", family_id=f"fam-{i}",
        )

    scout = ProviderScout(store, min_sample_size=10, win_rate_threshold=0.4, profit_factor_threshold=1.0)
    promoted = scout.scan_once()
    assert promoted == 1

    candidates = store.list_provider_candidates()
    [candidate] = [c for c in candidates if c["source"] == "promising_free_provider"]
    assert candidate["recommendation"] == "promote"
    assert candidate["win_rate"] == pytest.approx(1.0)
