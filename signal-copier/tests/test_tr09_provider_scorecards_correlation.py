"""Phase B4/B5: TR-09's provider/analyst behavior charts, quality funnel,
account-performance-proxy panel, and account-correlation panel
(`#/trade/sources`) -- see app/static/views/tr09.js's module docstring for
the full honest-scoping rationale (no per-provider equity attribution
model exists anywhere in this codebase; an account's own real
equity/statistics may only stand in for "this provider's performance" when
GET /routing-rules proves a real, clean 1:1 routing mapping).

Real backing data used here, all via HTTP + this exact real running
server's own live `store`: real signals (POST /webhook/{source}, immediately
filled by PaperBroker -- see app/brokers/paper.py's `place_order`), real
routing rules (POST /routing-rules), and, for the two pieces of the real
schema this build doesn't yet have an HTTP-only path to drive (a real
STOP_PLACED event and a real closed-position excursion row -- see
app/lifecycle/manager.py / app/db.py's own schema for why: no bracket/stop
placement or lifecycle-close flow is reachable from a plain (non-managed)
webhook-only flow in this build), the exact same real,
already-tested `SignalStore.record_stop_target_event` /
`record_position_excursion` / `record_equity_snapshot` direct calls
tests/test_stop_target_events_endpoint.py and tests/test_statistics_endpoint.py
already use to seed this schema for a real endpoint's own tests -- run
directly against the SAME live `store` object the running server's HTTP
handlers read from (an in-process uvicorn, like tests/test_tr03_mae_mfe_chart.py's
own fixture, not a subprocess, precisely so this test can hold that
reference).
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import uvicorn
from playwright.async_api import async_playwright

from tests.conftest import resolve_chromium_executable

_CHROMIUM_EXECUTABLE = resolve_chromium_executable()
_SIGNAL_COPIER_DIR = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server_inprocess(monkeypatch, tmp_path):
    """A real uvicorn server serving the real `app.main:app` object, run
    in-process so this test can hold a direct reference to the exact same
    live `SignalStore` the running server's HTTP handlers use (see this
    module's own docstring for why that's necessary and still fully real)."""
    from app import config as app_config
    from app.db import SignalStore
    import app.main as main_module

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-pw-tr09b4b5")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret-tr09b4b5")
    monkeypatch.setattr(app_config, "WEBHOOK_SHARED_SECRET", "test-webhook-secret-tr09b4b5")

    fresh_store = SignalStore(str(tmp_path / "tr09_b4b5.db"))
    monkeypatch.setattr(main_module, "store", fresh_store)
    monkeypatch.setattr(main_module.engine, "store", fresh_store)

    port = _free_port()
    loop = asyncio.new_event_loop()
    server_config = uvicorn.Config(main_module.app, host="127.0.0.1", port=port, log_level="warning", loop="none")
    server = uvicorn.Server(server_config)

    def _run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve())

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    for _ in range(75):
        try:
            response = httpx.get(f"{base_url}/health", timeout=1.0)
            if response.status_code in (200, 503):
                break
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    else:
        server.should_exit = True
        raise RuntimeError("in-process live server did not become reachable in time")

    try:
        yield base_url, fresh_store
    finally:
        server.should_exit = True
        thread.join(timeout=5)


async def _wait_settled(page, selector, timeout=10000):
    await page.wait_for_function(
        "(sel) => { const el = document.querySelector(sel); "
        "return !!el && !el.innerText.includes('Loading'); }",
        arg=selector,
        timeout=timeout,
    )


@pytest.mark.asyncio
async def test_tr09_provider_scorecards_and_correlation_match_real_seeded_data(live_server_inprocess):
    base_url, store = live_server_inprocess
    client = httpx.Client(base_url=base_url, timeout=10.0)

    login = client.post("/auth/login", json={"password": "test-owner-pw-tr09b4b5"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    webhook_headers = {"X-Webhook-Secret": "test-webhook-secret-tr09b4b5"}

    # --- Real accounts + routing: provA is a CLEAN 1:1 mapping to
    # acct-clean; provB fans out to two accounts (acct-fan1, acct-fan2),
    # a real but AMBIGUOUS mapping -- must render unsupported, never an
    # arbitrary stand-in account. ---
    for account_id in ("acct-clean", "acct-fan1", "acct-fan2"):
        assert client.post(
            "/accounts", json={"account_id": account_id, "broker": "paper"}, headers=csrf_headers
        ).status_code == 200
    assert client.post(
        "/routing-rules", json={"source": "provA", "destinations": ["acct-clean"]}, headers=csrf_headers
    ).status_code == 200
    assert client.post(
        "/routing-rules",
        json={"source": "provB", "destinations": ["acct-fan1", "acct-fan2"], "delivery_mode": "replicate"},
        headers=csrf_headers,
    ).status_code == 200
    # GET /providers (TR-09's "Sources" table) reads app/providers.py's
    # ProviderRegistry, a SEPARATE config surface from routing rules --
    # a source with no explicit provider entry never shows up here at all
    # (see TR-09's own module docstring), so both providers need one.
    assert client.post(
        "/providers/provA", json={"display_name": "Provider A"}, headers=csrf_headers
    ).status_code == 200
    assert client.post(
        "/providers/provB", json={"display_name": "Provider B"}, headers=csrf_headers
    ).status_code == 200

    # --- Real signals (provA: 2x BTCUSDT buy, 1x ETHUSDT sell; provB: 1x
    # XAUUSD buy) -- each immediately filled by PaperBroker (see this
    # module's docstring), so "order created"/"filled" are real for every
    # one of these. ---
    for side, symbol, price in (("buy", "BTCUSDT", 100.0), ("buy", "BTCUSDT", 101.0), ("sell", "ETHUSDT", 50.0)):
        resp = client.post(
            "/webhook/provA",
            json={"symbol": symbol, "side": side, "quantity": 1.0, "price": price},
            headers=webhook_headers,
        )
        assert resp.status_code == 200
    resp = client.post(
        "/webhook/provB",
        json={"symbol": "XAUUSD", "side": "buy", "quantity": 1.0, "price": 10.0},
        headers=webhook_headers,
    )
    assert resp.status_code == 200

    orders = client.get("/orders?limit=50", headers=csrf_headers).json()["orders"]
    btc_orders_clean = [o for o in orders if o["account_id"] == "acct-clean" and o["symbol"] == "BTCUSDT"]
    assert len(btc_orders_clean) == 2
    assert all(o["status"] == "filled" for o in btc_orders_clean)
    fan_orders = [o for o in orders if o["symbol"] == "XAUUSD"]
    # Real fan-out: one signal, routed to BOTH destination accounts -> 2 real orders.
    assert {o["account_id"] for o in fan_orders} == {"acct-fan1", "acct-fan2"}

    # --- Real stop-event + real closed-position excursion, seeded via the
    # exact same direct SignalStore calls this schema's own endpoint tests
    # use (see module docstring) -- for BTCUSDT/acct-clean only, so the
    # quality funnel's "protected"/"closed" stages are exercised for
    # exactly 2 of provA's 3 signals (both BTCUSDT ones), never all 3. ---
    stop_at = datetime.fromisoformat(btc_orders_clean[0]["executed_at"]) + timedelta(seconds=1)
    store.record_stop_target_event(
        "acct-clean", "BTCUSDT", event_type="stop_placed", at=stop_at, price=95.0, previous_price=None, source="signal"
    )
    store.record_position_excursion(
        "acct-clean",
        "BTCUSDT",
        side="buy",
        entry_price=100.0,
        highest_price_since_entry=105.0,
        highest_price_at=stop_at,
        lowest_price_since_entry=95.0,
        lowest_price_at=stop_at,
        mae=5.0,
        mfe=5.0,
        has_price_data=True,
        closed_at=stop_at + timedelta(minutes=5),
    )

    # --- Real equity snapshots: 10 overlapping timestamps for acct-clean
    # and acct-fan1 (a perfectly-correlated pair, real Pearson r == 1.0),
    # so both the account-performance-proxy panel (acct-clean, provA's
    # clean mapping) and the correlation panel have real data to read. ---
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i in range(10):
        captured_at = t0 + timedelta(hours=i)
        value_clean = 100.0 + i * 5.0
        store.record_equity_snapshot(
            "acct-clean", captured_at=captured_at, realized_pnl=value_clean, unrealized_pnl=0.0, cumulative_pnl=value_clean
        )
        store.record_equity_snapshot(
            "acct-fan1",
            captured_at=captured_at,
            realized_pnl=2 * value_clean,
            unrealized_pnl=0.0,
            cumulative_pnl=2 * value_clean,
        )

    # Cross-check the real endpoints directly before ever touching the
    # browser -- same real data the page itself will read.
    stats = client.get("/accounts/acct-clean/statistics", headers=csrf_headers).json()
    assert stats["sample_count"] == 10
    corr = client.get(
        "/accounts/correlation",
        params={"account_a": "acct-clean", "account_b": "acct-fan1"},
        headers=csrf_headers,
    ).json()
    assert corr["sample_count"] == 10
    assert corr["correlation"] == pytest.approx(1.0)

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw-tr09b4b5")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)

            await page.goto(f"{base_url}/#/trade/sources")
            await page.wait_for_selector("#tr09-p06", timeout=5000)
            await _wait_settled(page, "#tr09-p06")
            await _wait_settled(page, "#tr09-p07")
            await _wait_settled(page, "#tr09-p08")
            await _wait_settled(page, "#tr09-p09")

            # --- B4-1: provider behavior charts -- real counts, by provider. ---
            longshort = await page.evaluate(
                "() => { const c = Chart.getChart(document.getElementById('tr09-longshort-chart')); "
                "if (!c) return null; const idx = c.data.labels.indexOf('provA'); "
                "const out = {}; for (const ds of c.data.datasets) out[ds.label] = ds.data[idx]; return out; }"
            )
            assert longshort == {"buy": 2, "sell": 1}

            perday_provA_total = await page.evaluate(
                "() => { const c = Chart.getChart(document.getElementById('tr09-perday-chart')); "
                "const ds = c.data.datasets.find((d) => d.label === 'provA'); "
                "return ds.data.reduce((a, b) => a + b, 0); }"
            )
            assert perday_provA_total == 3
            perday_provB_total = await page.evaluate(
                "() => { const c = Chart.getChart(document.getElementById('tr09-perday-chart')); "
                "const ds = c.data.datasets.find((d) => d.label === 'provB'); "
                "return ds.data.reduce((a, b) => a + b, 0); }"
            )
            assert perday_provB_total == 1

            # Instrument concentration: switch the selector to provA and
            # confirm the real per-symbol counts (2x BTCUSDT, 1x ETHUSDT).
            await page.select_option("#tr09-instrument-provider", "provA")
            await page.wait_for_function(
                "() => { const c = Chart.getChart(document.getElementById('tr09-instrument-chart')); "
                "return !!c && c.data.labels.includes('BTCUSDT'); }",
                timeout=5000,
            )
            instrument_counts = await page.evaluate(
                "() => { const c = Chart.getChart(document.getElementById('tr09-instrument-chart')); "
                "const out = {}; c.data.labels.forEach((l, i) => out[l] = c.data.datasets[0].data[i]); return out; }"
            )
            assert instrument_counts == {"BTCUSDT": 2, "ETHUSDT": 1}

            # --- B4-2: quality funnel -- real join, per provider. provA:
            # received=3, order_created=3, filled=3, protected=2 (only the
            # 2 BTCUSDT signals), closed=2 (same). provB: received=1,
            # order_created=1, filled=1, protected=0, closed=0. ---
            funnel_provA = await page.evaluate(
                "() => { const c = Chart.getChart(document.getElementById('tr09-funnel-chart')); "
                "const ds = c.data.datasets.find((d) => d.label === 'provA'); return ds.data; }"
            )
            assert funnel_provA == [3, 3, 3, 2, 2]
            funnel_provB = await page.evaluate(
                "() => { const c = Chart.getChart(document.getElementById('tr09-funnel-chart')); "
                "const ds = c.data.datasets.find((d) => d.label === 'provB'); return ds.data; }"
            )
            assert funnel_provB == [1, 1, 1, 0, 0]

            # --- B4-3: account-performance-proxy -- provA is clean (real
            # equity chart for acct-clean); provB is real but AMBIGUOUS
            # (fans to 2 accounts) and must say so, never pick one account
            # as a stand-in. ---
            proxy_text = await page.inner_text("#tr09-p08")
            assert 'acct-clean' in proxy_text
            assert "provB" in proxy_text
            assert "2 destination accounts" in proxy_text
            equity_data = await page.evaluate(
                "() => { const c = Chart.getChart(document.getElementById('tr09-equity-chart-acct-clean')); "
                "return c ? c.data.datasets[0].data : null; }"
            )
            assert equity_data == [100.0 + i * 5.0 for i in range(10)]

            # --- B5: correlation -- real Pearson r == 1.0 between
            # acct-clean and acct-fan1 (their real, perfectly-correlated
            # seeded snapshots). ---
            await page.select_option("#tr09-corr-a", "acct-clean")
            await page.select_option("#tr09-corr-b", "acct-fan1")
            await page.click("#tr09-corr-run")
            await page.wait_for_function(
                "() => document.querySelector('#tr09-corr-result') && "
                "document.querySelector('#tr09-corr-result').innerText.includes('10 real overlapping')",
                timeout=8000,
            )
            corr_text = await page.inner_text("#tr09-corr-result")
            assert "1" in corr_text
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_tr09_shared_account_is_unsupported_for_both_providers_never_a_stand_in(live_server_inprocess):
    """LOAD-BEARING INVARIANT for this batch: when ONE destination account
    is fed by TWO real providers (a real but ambiguous many-to-one
    mapping), the account-performance-proxy panel must render both
    providers as unsupported -- an account's real equity/statistics may
    NEVER stand in for either provider's own performance just because it
    happens to be the only account either one routes to. See this file's
    `routingMapping()` in app/static/views/tr09.js: this is exactly the
    "providersOnThatAccount.size > 1" half of that check (the other half,
    "one provider fanning out to 2+ accounts", is already covered by
    provB in this module's first test)."""
    base_url, store = live_server_inprocess
    client = httpx.Client(base_url=base_url, timeout=10.0)

    login = client.post("/auth/login", json={"password": "test-owner-pw-tr09b4b5"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}

    assert client.post(
        "/accounts", json={"account_id": "acct-shared", "broker": "paper"}, headers=csrf_headers
    ).status_code == 200
    # TWO real providers both routed to the SAME single account.
    assert client.post(
        "/routing-rules", json={"source": "provShared1", "destinations": ["acct-shared"]}, headers=csrf_headers
    ).status_code == 200
    assert client.post(
        "/routing-rules", json={"source": "provShared2", "destinations": ["acct-shared"]}, headers=csrf_headers
    ).status_code == 200
    assert client.post(
        "/providers/provShared1", json={"display_name": "Shared 1"}, headers=csrf_headers
    ).status_code == 200
    assert client.post(
        "/providers/provShared2", json={"display_name": "Shared 2"}, headers=csrf_headers
    ).status_code == 200
    webhook_headers = {"X-Webhook-Secret": "test-webhook-secret-tr09b4b5"}
    assert client.post(
        "/webhook/provShared1",
        json={"symbol": "AAA", "side": "buy", "quantity": 1.0, "price": 1.0},
        headers=webhook_headers,
    ).status_code == 200

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw-tr09b4b5")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)

            await page.goto(f"{base_url}/#/trade/sources")
            await page.wait_for_selector("#tr09-p08", timeout=5000)
            await _wait_settled(page, "#tr09-p08")

            proxy_text = await page.inner_text("#tr09-p08")
            assert "provShared1" in proxy_text
            assert "provShared2" in proxy_text
            assert "also receives routed signals from" in proxy_text
            # Never a real equity chart for the shared account under either
            # provider's name -- this is the exact fabrication this
            # invariant exists to prevent.
            no_chart = await page.evaluate(
                "() => Chart.getChart(document.getElementById('tr09-equity-chart-acct-shared'))"
            )
            assert no_chart is None
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_tr09_correlation_panel_shows_honest_insufficient_data_state(live_server_inprocess):
    """Two real accounts with real but too-few overlapping snapshots must
    show an honest "insufficient overlapping data" state -- never a
    fabricated 0 (see app/statistics.py's own MIN_CORRELATION_SAMPLES
    threshold, which GET /accounts/correlation already enforces)."""
    base_url, store = live_server_inprocess
    client = httpx.Client(base_url=base_url, timeout=10.0)

    login = client.post("/auth/login", json={"password": "test-owner-pw-tr09b4b5"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}

    for account_id in ("acct-x", "acct-y"):
        assert client.post(
            "/accounts", json={"account_id": account_id, "broker": "paper"}, headers=csrf_headers
        ).status_code == 200
    assert client.post(
        "/routing-rules", json={"source": "provX", "destinations": ["acct-x"]}, headers=csrf_headers
    ).status_code == 200
    assert client.post(
        "/providers/provX", json={"display_name": "Provider X"}, headers=csrf_headers
    ).status_code == 200

    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i in range(3):  # below MIN_CORRELATION_SAMPLES (10)
        captured_at = t0 + timedelta(hours=i)
        store.record_equity_snapshot(
            "acct-x", captured_at=captured_at, realized_pnl=float(i), unrealized_pnl=0.0, cumulative_pnl=float(i)
        )
        store.record_equity_snapshot(
            "acct-y", captured_at=captured_at, realized_pnl=float(2 * i), unrealized_pnl=0.0, cumulative_pnl=float(2 * i)
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw-tr09b4b5")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)

            await page.goto(f"{base_url}/#/trade/sources")
            await page.wait_for_selector("#tr09-p09", timeout=5000)
            await _wait_settled(page, "#tr09-p09")

            await page.select_option("#tr09-corr-a", "acct-x")
            await page.select_option("#tr09-corr-b", "acct-y")
            await page.click("#tr09-corr-run")
            await page.wait_for_function(
                "() => document.querySelector('#tr09-corr-result') && "
                "document.querySelector('#tr09-corr-result').innerText.includes('Insufficient overlapping data')",
                timeout=8000,
            )
            corr_text = await page.inner_text("#tr09-corr-result")
            assert "3 real overlapping" in corr_text
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_tr09_signal_vs_execution_gap_matches_real_seeded_slippage_and_quantity_gap(live_server_inprocess):
    """Phase C1's new "provider signal vs. our copied execution" panel
    (#tr09-p10) -- LOAD-BEARING for this batch's riskiest new computation,
    the signed/side-aware slippage formula in app/static/views/tr09.js's
    `computeSignalExecutionGap`.

    PaperBroker (app/brokers/paper.py) always fills at exactly the
    signal's own price with zero slippage by design (see its own
    docstring) -- so, precisely like this module's own earlier tests seed
    a real STOP_PLACED event / closed-position excursion directly via
    `SignalStore` for schema pieces this webhook-only flow can't produce,
    this test drives one real signal through the real webhook -> engine ->
    PaperBroker path (so the order row's own real signal_id linkage,
    requested_quantity sizing, and account attribution are all genuine),
    then corrects that one order's terminal fill via the exact same real,
    already-tested `SignalStore.update_order_status` app/reconciliation.py
    itself calls -- simulating what a REAL broker's confirmed fill report
    (with real slippage and a real partial fill) would have written to
    this same row. A second signal's order is flipped to REJECTED the same
    way, with a real message, to exercise the rejection-reason breakdown.
    """
    base_url, store = live_server_inprocess
    client = httpx.Client(base_url=base_url, timeout=10.0)

    login = client.post("/auth/login", json={"password": "test-owner-pw-tr09b4b5"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    webhook_headers = {"X-Webhook-Secret": "test-webhook-secret-tr09b4b5"}

    assert client.post(
        "/accounts", json={"account_id": "acct-gap", "broker": "paper"}, headers=csrf_headers
    ).status_code == 200
    assert client.post(
        "/routing-rules", json={"source": "provGap", "destinations": ["acct-gap"]}, headers=csrf_headers
    ).status_code == 200
    assert client.post(
        "/providers/provGap", json={"display_name": "Provider Gap"}, headers=csrf_headers
    ).status_code == 200

    # Signal 1: real BUY signal specifying price=100.0, quantity=10.0 --
    # PaperBroker fills it instantly at that same price/quantity (zero
    # slippage, by its own design).
    resp = client.post(
        "/webhook/provGap",
        json={"symbol": "GAPUSD", "side": "buy", "quantity": 10.0, "price": 100.0},
        headers=webhook_headers,
    )
    assert resp.status_code == 200

    # Signal 2: a second real signal whose resulting order is corrected to
    # REJECTED below (to exercise the rejection-reason breakdown).
    resp = client.post(
        "/webhook/provGap",
        json={"symbol": "GAPUSD2", "side": "buy", "quantity": 5.0, "price": 50.0},
        headers=webhook_headers,
    )
    assert resp.status_code == 200

    orders = client.get("/orders?limit=50", headers=csrf_headers).json()["orders"]
    order_gapusd = next(o for o in orders if o["symbol"] == "GAPUSD")
    order_gapusd2 = next(o for o in orders if o["symbol"] == "GAPUSD2")
    assert order_gapusd["status"] == "filled"
    assert order_gapusd["requested_quantity"] == pytest.approx(10.0)
    assert order_gapusd2["status"] == "filled"

    from app.models import OrderResult, OrderStatus

    # Real, deliberately adverse fill for a BUY: paid 101.5 instead of the
    # signal's own 100.0 (slippage = +1.5, adverse), and only 8.0 of the
    # requested 10.0 filled (execution gap = +2.0).
    store.update_order_status(
        order_gapusd["id"],
        OrderResult(
            account_id="acct-gap",
            status=OrderStatus.FILLED,
            signal_id=order_gapusd["signal_id"],
            filled_quantity=8.0,
            filled_price=101.5,
            message="",
        ),
    )
    # Real rejection, with a real message, for the second signal's order.
    store.update_order_status(
        order_gapusd2["id"],
        OrderResult(
            account_id="acct-gap",
            status=OrderStatus.REJECTED,
            signal_id=order_gapusd2["signal_id"],
            filled_quantity=None,
            filled_price=None,
            message="simulated risk-check rejection for this test",
        ),
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw-tr09b4b5")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)

            await page.goto(f"{base_url}/#/trade/sources")
            await page.wait_for_selector("#tr09-p10", timeout=5000)
            await _wait_settled(page, "#tr09-p10")

            gap_text = await page.inner_text("#tr09-p10")
            assert "provGap" in gap_text
            # Real, signed slippage: +1.5 (adverse, paid more than the
            # signal specified on a BUY) -- must be positive/"adverse",
            # never negative/"favorable" (a flipped sign would silently
            # report a real bad fill as if it were a good one).
            assert "1.5" in gap_text
            assert "-1.5" not in gap_text
            assert "adverse" in gap_text
            assert "favorable" not in gap_text
            # Real execution gap: requested 10.0, filled 8.0 -> +2.
            assert "2" in gap_text
            assert "simulated risk-check rejection for this test" in gap_text
        finally:
            await browser.close()
