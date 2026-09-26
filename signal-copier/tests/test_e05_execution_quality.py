"""E05: signal-to-fill latency reporting (app/execution_quality.py)."""
from datetime import datetime, timedelta, timezone

from app.db import SignalStore
from app.execution_quality import compute_execution_quality
from app.models import OrderResult, OrderStatus, Side, Signal

ACCOUNT_ID = "acct1"


def _signal(symbol: str, received_at: datetime) -> Signal:
    return Signal(source="test", symbol=symbol, side=Side.BUY, received_at=received_at)


def _fill(signal: Signal, executed_at: datetime, quantity: float = 10.0, price: float = 100.0) -> OrderResult:
    return OrderResult(
        account_id=ACCOUNT_ID,
        status=OrderStatus.FILLED,
        signal_id=signal.id,
        filled_quantity=quantity,
        filled_price=price,
        executed_at=executed_at,
    )


def test_computes_latency_from_known_timestamps(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    t0 = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    signal = _signal("AAPL", t0)
    store.save_signal(signal)
    store.save_order_result(
        _fill(signal, t0 + timedelta(seconds=5)), broker="paper", symbol="AAPL", side=Side.BUY
    )

    result = compute_execution_quality(store, ACCOUNT_ID)

    assert result.unmatched_order_count == 0
    aapl = result.per_symbol["AAPL"]
    assert aapl.sample_count == 1
    assert aapl.mean_seconds == 5.0
    assert aapl.median_seconds == 5.0
    assert aapl.max_seconds == 5.0


def test_multiple_symbols_aggregated_separately(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

    aapl_signal = _signal("AAPL", t0)
    store.save_signal(aapl_signal)
    store.save_order_result(
        _fill(aapl_signal, t0 + timedelta(seconds=2)), broker="paper", symbol="AAPL", side=Side.BUY
    )

    msft_signal = _signal("MSFT", t0)
    store.save_signal(msft_signal)
    store.save_order_result(
        _fill(msft_signal, t0 + timedelta(seconds=8)), broker="paper", symbol="MSFT", side=Side.BUY
    )

    result = compute_execution_quality(store, ACCOUNT_ID)

    assert result.per_symbol["AAPL"].mean_seconds == 2.0
    assert result.per_symbol["MSFT"].mean_seconds == 8.0


class _FakeStore:
    """SignalStore's schema enforces a foreign key from orders.signal_id to
    signals.id, so a real SignalStore can never actually hold a filled
    order whose signal row is missing -- there is no way to reproduce that
    state through the real store. This narrow double exercises
    compute_execution_quality's own defensive accounting for that gap
    directly, the same way the INNER JOIN in
    list_filled_orders_with_signal_timing would surface it if the join
    key were ever missing for some other reason (e.g. a future schema
    change that relaxes the FK)."""

    def __init__(self, all_rows, timed_rows):
        self._all_rows = all_rows
        self._timed_rows = timed_rows

    def list_filled_orders_chronological(self, account_id):
        return self._all_rows

    def list_filled_orders_with_signal_timing(self, account_id):
        return self._timed_rows


def test_order_with_no_matching_signal_counts_as_unmatched():
    store = _FakeStore(all_rows=[{"symbol": "AAPL"}], timed_rows=[])

    result = compute_execution_quality(store, ACCOUNT_ID)

    assert result.unmatched_order_count == 1
    assert result.per_symbol == {}


def test_no_orders_returns_empty_report(tmp_path):
    store = SignalStore(tmp_path / "test.db")

    result = compute_execution_quality(store, ACCOUNT_ID)

    assert result.unmatched_order_count == 0
    assert result.per_symbol == {}
    body = result.to_dict()
    assert body["account_id"] == ACCOUNT_ID
    assert body["per_symbol"] == {}
