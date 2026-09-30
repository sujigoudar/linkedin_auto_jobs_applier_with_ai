"""app/provider_scout.py: the scheduled scan that flags a free signal
source/analyst worth promoting, computed the same way
app/provider_value.py's core replay is."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db import SignalStore
from app.models import OrderResult, OrderStatus, Side, Signal
from app.provider_scout import ProviderScout


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _fill(store, account_id, symbol, side, quantity, price, when, *, source="test", purpose=None, family_id=None):
    signal = Signal(source=source, symbol=symbol, side=side)
    store.save_signal(signal)
    result = OrderResult(
        account_id=account_id, status=OrderStatus.FILLED, signal_id=signal.id,
        filled_quantity=quantity, filled_price=price, executed_at=when,
    )
    store.save_order_result(
        result, broker="paper", symbol=symbol, side=side, purpose=purpose, family_id=family_id, applied_quantity=quantity
    )


def _round_trips(store, source, n, *, win_price=110.0, buy_price=100.0):
    """TR-EPISODE-01: app/provider_scout.py now scores from
    app/trade_episode.py's episode replay, which (like the real
    app/engine.py contract -- see `orders.family_id`'s own schema
    comment) requires a real `family_id` linking an entry to its close;
    each round trip here gets its own distinct family so it's counted as
    one episode, matching production."""
    t0 = datetime.now(timezone.utc)
    for i in range(n):
        family_id = f"{source}-fam-{i}"
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, buy_price, t0 + timedelta(minutes=2 * i), source=source, purpose="entry", family_id=family_id)
        _fill(store, "acct1", "AAPL", Side.SELL, 10.0, win_price, t0 + timedelta(minutes=2 * i + 1), source=source, purpose="close", family_id=family_id)


@pytest.fixture
def scout(store):
    return ProviderScout(store, min_sample_size=10, win_rate_threshold=0.4, profit_factor_threshold=1.0)


def test_below_sample_size_is_insufficient_data(store, scout):
    _round_trips(store, "new_channel", 3)

    scout.scan_once()

    candidates = {(c["source"]): c for c in store.list_provider_candidates()}
    assert candidates["new_channel"]["recommendation"] == "insufficient_data"


def test_good_profitable_track_record_is_recommended_for_promotion(store, scout):
    _round_trips(store, "promising_channel", 12, buy_price=100.0, win_price=110.0)

    scout.scan_once()

    candidates = {c["source"]: c for c in store.list_provider_candidates()}
    assert candidates["promising_channel"]["recommendation"] == "promote"


def test_poor_track_record_is_not_promising(store, scout):
    _round_trips(store, "bad_channel", 12, buy_price=100.0, win_price=95.0)

    scout.scan_once()

    candidates = {c["source"]: c for c in store.list_provider_candidates()}
    assert candidates["bad_channel"]["recommendation"] == "not_promising"


def test_an_already_subscribed_provider_is_never_scouted_as_a_candidate(store, scout):
    _round_trips(store, "paid_channel", 12, buy_price=100.0, win_price=110.0)
    store.upsert_provider_subscription("paid_channel", cost_amount=10.0)

    scout.scan_once()

    sources = {c["source"] for c in store.list_provider_candidates()}
    assert "paid_channel" not in sources


def test_scan_once_returns_the_count_newly_recommended_for_promotion(store, scout):
    _round_trips(store, "good_a", 12, buy_price=100.0, win_price=110.0)
    _round_trips(store, "good_b", 12, buy_price=100.0, win_price=110.0)
    _round_trips(store, "bad_a", 12, buy_price=100.0, win_price=95.0)

    promoted = scout.scan_once()

    assert promoted == 2


def test_rescanning_updates_the_snapshot_rather_than_duplicating_it(store, scout):
    _round_trips(store, "channel", 3)
    scout.scan_once()
    assert len(store.list_provider_candidates()) == 1

    _round_trips(store, "channel", 20)  # now well past the sample-size threshold
    scout.scan_once()

    candidates = store.list_provider_candidates()
    assert len(candidates) == 1
    assert candidates[0]["recommendation"] != "insufficient_data"
