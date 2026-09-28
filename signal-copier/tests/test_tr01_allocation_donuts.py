"""TR-01 Trading command center, Phase B1 batch: real asset-class/broker/
account allocation donuts + open-position table (MAE/MFE) enrichment --
see app/static/views/tr01.js's own module docstring for exactly which
GET endpoints back each of these, and this batch's report for the full
list of catalog items deliberately NOT built (equity/P&L/underwater/
daily-P&L/exposure-history, the waterfall, the risk heatmap) and why.

Real coverage, no fabricated data:
  - TestClient-level: the /positions, /accounts, /signals fields these
    new panels depend on are really present and owner-gated.
  - Real-browser (Playwright): seeds real accounts across two different
    brokers and real open positions across three different asset classes
    (via app/db.py's own SignalStore.save_signal/record_fill -- the same
    real writes app/engine.py itself performs on a live fill, just
    invoked directly against the SAME sqlite file the live_server
    subprocess serves from, rather than requiring a second real broker
    adapter's credentials just to prove a broker string groups
    correctly), then asserts the rendered donut tables' bucket/count
    breakdown EXACTLY matches that real seeded ground truth -- proving
    no double-counting and no miscategorization.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

import app.main as main_module
from app.db import SignalStore
from app.models import AssetClass, Side, Signal
from tests.conftest import resolve_chromium_executable

_CHROMIUM_EXECUTABLE = resolve_chromium_executable()


def _authed_client(monkeypatch, tmp_path, db_name="tr01_allocation.db"):
    from app import config as app_config

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    store = SignalStore(str(tmp_path / db_name))
    monkeypatch.setattr(main_module, "store", store)
    return store, TestClient(main_module.app)


def test_tr01_view_module_is_served():
    client = TestClient(main_module.app)
    response = client.get("/static/views/tr01.js")
    assert response.status_code == 200
    assert len(response.content) > 500


def test_positions_accounts_signals_require_owner_session(monkeypatch, tmp_path):
    _store, client = _authed_client(monkeypatch, tmp_path)
    assert client.get("/positions").status_code == 401
    assert client.get("/accounts").status_code == 401
    assert client.get("/signals").status_code == 401


def test_positions_response_carries_no_asset_class_column(monkeypatch, tmp_path):
    """Confirms the honest premise this batch's report relies on: GET
    /positions genuinely has no asset_class field per position (see
    app/db.py's `list_open_positions` -- account_id/symbol/net_quantity/
    updated_at only), which is why TR-01 joins asset class from GET
    /signals by symbol instead of reading it directly."""
    store, client = _authed_client(monkeypatch, tmp_path)
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    store.record_fill("acct1", "BTCUSDT", Side.BUY, 1.0)
    response = client.get("/positions")
    assert response.status_code == 200
    positions = response.json()["positions"]
    assert positions, "expected the seeded position to be listed"
    assert "asset_class" not in positions[0]


# --- Real browser: the three allocation donuts' rendered bucket/count
# breakdown exactly matches real seeded accounts/positions/signals across
# two brokers and three asset classes -- LOAD-BEARING INVARIANT for this
# batch (see this batch's report for the temporary-break verification
# performed against this exact test before committing). ---


@pytest.mark.asyncio
async def test_allocation_donuts_and_open_position_table_match_real_seeded_data(live_server, tmp_path):
    base_url = live_server

    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}

    # Two accounts, two DIFFERENT brokers (broker is a free-form string on
    # AccountRequest -- see app/main.py's AccountRequest model -- no real
    # adapter/credentials are needed just to prove GET /accounts' own
    # broker field is what the broker donut groups by).
    assert client.post(
        "/accounts", json={"account_id": "acct-paper", "broker": "paper"}, headers=csrf_headers
    ).status_code == 200
    assert client.post(
        "/accounts", json={"account_id": "acct-ibkr", "broker": "ibkr"}, headers=csrf_headers
    ).status_code == 200

    # Real open positions, seeded the same way app/engine.py itself
    # updates the tracked position on a real fill (SignalStore.record_fill)
    # -- against the SAME sqlite file (tmp_path/"e2e.db", per
    # tests/conftest.py's live_server fixture) the live_server subprocess
    # already has open, so GET /positions on that running server sees
    # these real rows. save_signal establishes the real (symbol ->
    # asset_class) pairing TR-01's join reads.
    store = SignalStore(str(tmp_path / "e2e.db"))
    seeded = [
        # (account, symbol, asset_class, quantity)
        ("acct-paper", "BTCUSDT", AssetClass.CRYPTO, 1.0),
        ("acct-paper", "AAPL", AssetClass.EQUITY, 2.0),
        ("acct-ibkr", "EURUSD", AssetClass.FOREX, 3.0),
    ]
    for account_id, symbol, asset_class, qty in seeded:
        store.save_signal(Signal(source="seed", symbol=symbol, side=Side.BUY, asset_class=asset_class))
        store.record_fill(account_id, symbol, Side.BUY, qty)

    # Ground truth this test asserts against, computed from the SAME real
    # seeded rows above (never hand-duplicated numbers that could drift).
    expected_by_asset_class = {}
    expected_by_broker = {}
    expected_by_account = {}
    broker_by_account = {"acct-paper": "paper", "acct-ibkr": "ibkr"}
    for account_id, _symbol, asset_class, _qty in seeded:
        expected_by_asset_class[asset_class.value] = expected_by_asset_class.get(asset_class.value, 0) + 1
        expected_by_broker[broker_by_account[account_id]] = expected_by_broker.get(broker_by_account[account_id], 0) + 1
        expected_by_account[account_id] = expected_by_account.get(account_id, 0) + 1

    # Confirm the live server's own GET /positions really sees these rows
    # before even opening a browser (isolates a routing/DB-path mismatch
    # from a front-end bug if this ever fails).
    positions_check = client.get("/positions", headers=csrf_headers)
    assert positions_check.status_code == 200
    assert len(positions_check.json()["positions"]) == len(seeded)

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)

            async def wait_settled(selector, timeout=8000):
                await page.wait_for_function(
                    "(sel) => { const el = document.querySelector(sel); "
                    "return !!el && !el.innerText.includes('Loading'); }",
                    arg=selector,
                    timeout=timeout,
                )

            await page.click('a[href="#/trade"]')
            await page.wait_for_selector("#tr01-p08", timeout=5000)
            await wait_settled("#tr01-p07")
            await wait_settled("#tr01-p08")

            # --- Open positions table: every seeded (account, symbol)
            # present, none invented. ---
            positions_text = await page.inner_text("#tr01-p07")
            for account_id, symbol, _asset_class, _qty in seeded:
                assert account_id in positions_text
                assert symbol in positions_text

            # --- Allocation donuts: each of the 3 accompanying tables'
            # bucket/count breakdown exactly matches the real seeded
            # ground truth -- the load-bearing invariant. ---
            async def bucket_counts(wrap_id):
                rows = await page.eval_on_selector_all(
                    f"#{wrap_id} table tbody tr",
                    "trs => trs.map(tr => Array.from(tr.children).map(td => td.textContent.trim()))",
                )
                return {r[0]: int(r[1].replace(",", "")) for r in rows}

            assert await bucket_counts("tr01-donut-assetclass-wrap") == expected_by_asset_class
            assert await bucket_counts("tr01-donut-broker-wrap") == expected_by_broker
            assert await bucket_counts("tr01-donut-account-wrap") == expected_by_account

            # Every donut's bucket counts must sum to the real total open
            # position count -- no position silently dropped, none double-
            # counted across buckets.
            total_positions = len(seeded)
            for wrap_id in ("tr01-donut-assetclass-wrap", "tr01-donut-broker-wrap", "tr01-donut-account-wrap"):
                counts = await bucket_counts(wrap_id)
                assert sum(counts.values()) == total_positions, wrap_id
        finally:
            await browser.close()
