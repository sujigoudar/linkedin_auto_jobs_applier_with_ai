"""B7: real cross-signal capital-sharing in the backtest replay.

Load-bearing scenario used throughout this file: two signals whose real
time windows genuinely overlap (signal B's entry falls before signal A's
resolving exit) and whose combined notional (1000 + 1000 = 2000) genuinely
exceeds a real configured account ceiling (max_notional_exposure=1500).
The naive, independent replay (`BacktestEngine.run`) resolves both as
WINs; the contention-aware overlay
(`BacktestEngine.run_with_capital_contention`) must reuse the exact same
`CapitalAllocator.admit()` gate `app/capital_allocator.py` / the live
engine use, and reject the later-arriving signal (B) because A's notional
was still committed when B arrived.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.backtest.models import HistoricalBar, PriceHistoryProvider
from app.backtest.replay import BacktestEngine, TradeOutcome
from app.db import SignalStore
from app.models import Signal, Side


class _FakeProvider(PriceHistoryProvider):
    def __init__(self, bars_by_symbol: dict[str, list[HistoricalBar]]):
        self.bars_by_symbol = bars_by_symbol

    def get_bars(self, symbol, start, end):
        return [b for b in self.bars_by_symbol.get(symbol, []) if start <= b.timestamp <= end]


def _bar(dt, o, h, l, c):
    return HistoricalBar(timestamp=dt, open=o, high=h, low=l, close=c)


def _row(**overrides):
    defaults = dict(
        id="sig-a",
        source="tradingview",
        symbol="AAPL",
        side="buy",
        analyst=None,
        quantity=10.0,
        price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        received_at=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc).isoformat(),
        raw={},
    )
    defaults.update(overrides)
    return defaults


def _overlapping_rows():
    """Signal A (AAPL) opens 2024-01-01 00:00, resolves WIN on 2024-01-03
    (still holding its notional through then). Signal B (MSFT) opens
    2024-01-01 12:00 -- while A's notional is still committed -- and
    resolves WIN on 2024-01-02. Their real time windows genuinely overlap
    from 2024-01-01 12:00 to 2024-01-02."""
    row_a = _row(id="sig-a", symbol="AAPL", received_at=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc).isoformat())
    row_b = _row(id="sig-b", symbol="MSFT", received_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc).isoformat())
    provider = _FakeProvider(
        {
            "AAPL": [_bar(datetime(2024, 1, 3, tzinfo=timezone.utc), 101, 111, 100, 110)],  # target hit -> WIN
            "MSFT": [_bar(datetime(2024, 1, 2, tzinfo=timezone.utc), 101, 111, 100, 110)],  # target hit -> WIN
        }
    )
    return [row_a, row_b], provider


@pytest.mark.asyncio
async def test_naive_replay_resolves_both_overlapping_signals_independently():
    """Control: the existing, unmodified naive replay has no capital
    modeling at all -- both signals resolve as WINs regardless of any
    shared ceiling."""
    rows, provider = _overlapping_rows()
    engine = BacktestEngine(provider)

    report = engine.run(rows)

    outcomes = {t.signal_id: t.outcome for t in report.trades}
    assert outcomes == {"sig-a": TradeOutcome.WIN, "sig-b": TradeOutcome.WIN}


@pytest.mark.asyncio
async def test_contention_aware_replay_rejects_the_later_overlapping_signal_when_ceiling_exceeded():
    """The real load-bearing case: combined notional (1000 + 1000 = 2000)
    exceeds the account's real configured ceiling (1500), so the
    later-arriving, still-overlapping signal (B) is genuinely rejected --
    a DIFFERENT result than the naive replay above produces for the same
    two signals."""
    rows, provider = _overlapping_rows()
    engine = BacktestEngine(provider)

    report = await engine.run_with_capital_contention(
        rows, account_id="acct-1", max_notional_exposure=1500.0
    )

    assert report.status == "implemented"
    assert report.reduced_or_rejected_count == 1
    assert report.rejected_signal_ids == ["sig-b"]
    assert "sig-b" in report.rejected_notes
    assert "acct-1" in report.rejected_notes["sig-b"]
    assert "1500" in report.rejected_notes["sig-b"]

    # contention_aware_summary is a dict (BacktestReport.summary()) -- assert
    # on the real counts it reports.
    assert report.contention_aware_summary["wins"] == 1
    assert report.contention_aware_summary["capital_rejected"] == 1
    # And the naive summary (used for comparison) is untouched/unaffected:
    assert report.naive_summary["wins"] == 2
    assert report.naive_summary["capital_rejected"] == 0


@pytest.mark.asyncio
async def test_contention_aware_replay_admits_both_when_ceiling_is_high_enough():
    """Same overlapping scenario, but a high-enough ceiling (2500) fits
    both signals' combined notional (2000) -- no rejection."""
    rows, provider = _overlapping_rows()
    engine = BacktestEngine(provider)

    report = await engine.run_with_capital_contention(
        rows, account_id="acct-1", max_notional_exposure=2500.0
    )

    assert report.status == "implemented"
    assert report.reduced_or_rejected_count == 0
    assert report.contention_aware_summary["wins"] == 2


@pytest.mark.asyncio
async def test_contention_aware_replay_admits_both_when_first_position_already_closed():
    """Non-overlapping control: if A's real exit_time is BEFORE B's real
    entry_time, A's capital has genuinely been released by the time B
    arrives, so B is admitted even under the tight 1500 ceiling that
    rejected it in the overlapping case above."""
    row_a = _row(id="sig-a", symbol="AAPL", received_at=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc).isoformat())
    row_b = _row(id="sig-b", symbol="MSFT", received_at=datetime(2024, 1, 5, 0, 0, tzinfo=timezone.utc).isoformat())
    provider = _FakeProvider(
        {
            "AAPL": [_bar(datetime(2024, 1, 2, tzinfo=timezone.utc), 101, 111, 100, 110)],  # WIN, exits day 2
            "MSFT": [_bar(datetime(2024, 1, 6, tzinfo=timezone.utc), 101, 111, 100, 110)],  # WIN, enters day 5 (after A exited)
        }
    )
    engine = BacktestEngine(provider)

    report = await engine.run_with_capital_contention(
        [row_a, row_b], account_id="acct-1", max_notional_exposure=1500.0
    )

    assert report.reduced_or_rejected_count == 0
    assert report.contention_aware_summary["wins"] == 2


def test_capital_contention_report_is_not_tracked_when_disabled():
    """Fault-injection proxy for 'disable the contention logic': calling
    the naive `run()` alone (what every pre-existing caller still does)
    never produces a CAPITAL_REJECTED outcome or a capital_contention
    report at all -- the overlay is genuinely opt-in, not baked into the
    default path."""
    rows, provider = _overlapping_rows()
    engine = BacktestEngine(provider)

    report = engine.run(rows)

    assert report.summary()["capital_rejected"] == 0
    assert all(t.outcome != TradeOutcome.CAPITAL_REJECTED for t in report.trades)


# --- API-level end-to-end: POST /backtest with a real configured account ---


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _signal(**overrides):
    defaults = dict(
        source="tradingview",
        symbol="AAPL",
        side=Side.BUY,
        quantity=10.0,
        price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        received_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return Signal(**defaults)


def test_backtest_endpoint_persists_real_capital_contention_result(store, tmp_path, monkeypatch):
    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(main_module, "store", store)

    store.save_signal(
        _signal(symbol="AAPL", received_at=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc))
    )
    store.save_signal(
        _signal(symbol="MSFT", received_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc))
    )

    aapl_csv = tmp_path / "AAPL.csv"
    aapl_csv.write_text("timestamp,open,high,low,close\n2024-01-03T00:00:00+00:00,101,111,100,110\n")
    msft_csv = tmp_path / "MSFT.csv"
    msft_csv.write_text("timestamp,open,high,low,close\n2024-01-02T00:00:00+00:00,101,111,100,110\n")

    client = TestClient(main_module.app)
    with client:
        login = client.post("/auth/login", json={"password": "test-owner-password"})
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]

        # A real configured account with a real, tight max_notional_exposure.
        acct_res = client.post(
            "/accounts",
            json={"account_id": "acct-1", "broker": "alpaca", "max_notional_exposure": 1500.0},
        )
        assert acct_res.status_code == 200

        # 1) No account_id -> honestly not_tracked, never a fabricated ceiling.
        no_account_response = client.post(
            "/backtest",
            json={
                "source": "tradingview",
                "start": "2024-01-01T00:00:00+00:00",
                "end": "2024-01-31T00:00:00+00:00",
                "csv_paths": {"AAPL": str(aapl_csv), "MSFT": str(msft_csv)},
            },
        )
        assert no_account_response.status_code == 200
        no_account_body = no_account_response.json()
        assert no_account_body["capital_contention"]["status"] == "not_tracked"

        # 2) Real account_id with a real ceiling -> real contention result,
        #    reusing the same overlapping-signal scenario as the unit tests
        #    above, persisted to app/db.py's backtest_runs table.
        response = client.post(
            "/backtest",
            json={
                "source": "tradingview",
                "start": "2024-01-01T00:00:00+00:00",
                "end": "2024-01-31T00:00:00+00:00",
                "csv_paths": {"AAPL": str(aapl_csv), "MSFT": str(msft_csv)},
                "account_id": "acct-1",
            },
        )

    assert response.status_code == 200
    body = response.json()
    cc = body["capital_contention"]
    assert cc["status"] == "implemented"
    assert cc["account_id"] == "acct-1"
    assert cc["max_notional_exposure"] == 1500.0
    assert cc["reduced_or_rejected_count"] == 1
    assert len(cc["rejected_signal_ids"]) == 1
    rejected_id = cc["rejected_signal_ids"][0]
    assert rejected_id in cc["rejected_notes"]
    assert "acct-1" in cc["rejected_notes"][rejected_id]

    # Persisted: fetching the run back gives the same real result, not a
    # placeholder -- app/db.py's backtest_runs.capital_contention_json.
    run_id = body["run_id"]
    persisted = store.get_backtest_run(run_id)
    assert persisted["capital_contention"]["status"] == "implemented"
    assert persisted["capital_contention"]["reduced_or_rejected_count"] == 1
