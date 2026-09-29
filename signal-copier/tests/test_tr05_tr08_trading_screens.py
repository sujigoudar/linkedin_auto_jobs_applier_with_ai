"""TR-05..TR-08 (signal-copier private_execution screens, batch 2 of 4):
real coverage for the new hash-routed screens ("Signal evidence and plan
preview", "Orders, fills and commands", "Broker accounts and
capabilities", "Broker account configuration") -- see
app/static/router.js and app/static/views/tr0*.js for the routing/
state-matrix pattern these screens are built on (established in batch 1,
see tests/test_tr01_tr04_trading_screens.py).

TestClient-level: proves the new static assets are reachable and the
extended /signals projection is real. Real-browser (Playwright): proves
the router actually renders real content for each of the 4 new routes,
including navigating INTO TR-05 from a TR-04 row link (the spec's own
"TR-04 rows link into TR-05" requirement) and exercising TR-07's real
"Pause new entries" action end to end against the live server.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

import app.main as main_module
from tests.conftest import resolve_chromium_executable

_CHROMIUM_EXECUTABLE = resolve_chromium_executable()


# --- Static assets ---


def test_all_four_new_view_modules_are_served():
    client = TestClient(main_module.app)
    for view in ("tr05", "tr06", "tr07", "tr08"):
        response = client.get(f"/static/views/{view}.js")
        assert response.status_code == 200, view
        assert len(response.content) > 500, view


def test_dashboard_html_references_the_new_view_scripts():
    client = TestClient(main_module.app)
    html = client.get("/").text
    for view in ("tr05", "tr06", "tr07", "tr08"):
        assert f"/static/views/{view}.js" in html


# --- /signals: extended in this batch to also project stop_loss,
# take_profit and raw (already-persisted columns, never previously
# selected -- see app/db.py's list_recent_signals). ---


def _authed_client(monkeypatch, tmp_path, db_name="tr0x_batch2.db"):
    from app import config as app_config
    from app.db import SignalStore

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    store = SignalStore(str(tmp_path / db_name))
    monkeypatch.setattr(main_module, "store", store)
    return store, TestClient(main_module.app)


def test_signals_endpoint_now_projects_stop_loss_take_profit_and_raw(monkeypatch, tmp_path):
    from app.models import AssetClass, Signal, Side

    store, client = _authed_client(monkeypatch, tmp_path)

    signal = Signal(
        source="tradingview",
        symbol="BTCUSDT",
        side=Side.BUY,
        asset_class=AssetClass.CRYPTO,
        stop_loss=95.0,
        take_profit=110.0,
        raw={"text": "buy BTCUSDT sl 95 tp 110"},
    )
    store.save_signal(signal)

    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    response = client.get("/signals")
    assert response.status_code == 200
    row = response.json()["signals"][0]
    assert row["stop_loss"] == 95.0
    assert row["take_profit"] == 110.0
    assert row["raw"] == {"text": "buy BTCUSDT sl 95 tp 110"}


def test_signals_list_still_orders_newest_first_with_new_columns(monkeypatch, tmp_path):
    # Regression: the extended SELECT must not disturb the existing
    # newest-first ordering or row count batch 1's own test relies on.
    from app.models import Signal, Side

    store, _client = _authed_client(monkeypatch, tmp_path)
    store.save_signal(Signal(source="a", symbol="BTCUSDT", side=Side.BUY))
    store.save_signal(Signal(source="b", symbol="ETHUSDT", side=Side.SELL))

    signals = store.list_recent_signals(limit=10)
    assert len(signals) == 2
    assert signals[0]["source"] == "b"
    assert signals[1]["source"] == "a"
    assert signals[0]["stop_loss"] is None
    assert signals[0]["raw"] == {}


# --- LOAD-BEARING INVARIANT for this batch: RequireOwner still blocks an
# unauthenticated request to every endpoint these 4 new screens read from
# (in particular /signals, whose projection this batch changed, and
# /orders/​/brokers/​/accounts, which TR-06/TR-07/TR-08 newly read into
# owner-facing screens) AND the one real command these screens add
# (TR-07's "Pause new entries") goes through the exact same, already-
# tested POST /accounts owner-only path -- it cannot be reached without a
# valid owner session either. See this batch's report for the temporary-
# break verification performed against this exact test before
# committing. ---


def test_new_screens_read_endpoints_require_owner_session(monkeypatch, tmp_path):
    _store, client = _authed_client(monkeypatch, tmp_path)
    assert client.get("/signals").status_code == 401
    assert client.get("/orders").status_code == 401
    assert client.get("/brokers").status_code == 401
    assert client.get("/accounts").status_code == 401


def test_pause_new_entries_command_path_requires_owner_session(monkeypatch, tmp_path):
    # TR-07-A03 "Pause new entries" is real POST /accounts with
    # enabled=false -- unauthenticated, it must be refused exactly like
    # every other account mutation already is.
    _store, client = _authed_client(monkeypatch, tmp_path)
    response = client.post("/accounts", json={"account_id": "acct1", "broker": "paper", "enabled": False})
    assert response.status_code == 401


def test_new_screens_read_endpoints_reachable_with_a_valid_owner_session(monkeypatch, tmp_path):
    _store, client = _authed_client(monkeypatch, tmp_path)
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    assert client.get("/signals").status_code == 200
    assert client.get("/orders").status_code == 200
    assert client.get("/brokers").status_code == 200
    assert client.get("/accounts").status_code == 200


# --- Real browser: the router actually renders real content for each of
# the 4 new hash routes, including TR-04 -> TR-05 navigation and TR-07's
# real pause-new-entries command. ---


@pytest.mark.asyncio
async def test_all_four_new_trading_screens_render_real_content(live_server):
    base_url = live_server

    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}

    assert client.post(
        "/accounts", json={"account_id": "acct1", "broker": "paper"}, headers=csrf_headers
    ).status_code == 200
    assert client.post(
        "/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]}, headers=csrf_headers
    ).status_code == 200
    webhook = client.post(
        "/webhook/tradingview",
        json={"symbol": "BTCUSDT", "side": "buy", "quantity": 1.0, "stop_loss": 95.0},
        headers={"X-Webhook-Secret": "test-webhook-secret"},
    )
    assert webhook.status_code == 200

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

            # TR-04 -> TR-05: navigate via the real row link the disposition
            # table renders, proving the router's dynamic :event_id segment
            # resolves to this exact signal.
            await page.click('a[href="#/trade/signals"]')
            await page.wait_for_selector("#tr04-p03", timeout=5000)
            await wait_settled("#tr04-p03")
            await page.click("#tr04-p03 a.mono")
            await page.wait_for_selector("#tr05-p01", timeout=5000)
            await wait_settled("#tr05-p01")
            await wait_settled("#tr05-p04")
            assert "Signal evidence and plan preview" == await page.inner_text("#route-title")
            original_text = await page.inner_text("#tr05-p01")
            assert "BTCUSDT" in original_text
            plan_text = await page.inner_text("#tr05-p04")
            assert "95" in plan_text  # stop_loss round-trips into the plan panel

            # TR-06: Orders, fills and commands.
            await page.click('a[href="#/trade/orders"]')
            await page.wait_for_selector("#tr06-p01", timeout=5000)
            await wait_settled("#tr06-p01")
            assert "Orders, fills and commands" == await page.inner_text("#route-title")
            queue_text = await page.inner_text("#tr06-p01")
            assert "acct1" in queue_text
            assert "BTCUSDT" in queue_text

            # TR-07: Broker accounts and capabilities -- real content plus
            # the real "Pause new entries" command (TR-07-A03).
            await page.click('a[href="#/trade/accounts"]')
            await page.wait_for_selector("#tr07-p01", timeout=5000)
            await wait_settled("#tr07-p01")
            await wait_settled("#tr07-p02")
            assert "Broker accounts and capabilities" == await page.inner_text("#route-title")
            accounts_text = await page.inner_text("#tr07-p01")
            assert "acct1" in accounts_text
            assert "paper" in accounts_text

            page.on("dialog", lambda dialog: dialog.accept())
            await wait_settled("#tr07-p04")
            await page.click('[data-pause-account="acct1"]')
            await page.wait_for_function(
                "() => { const b = document.querySelector('[data-pause-account=\"acct1\"]'); "
                "return !!b && b.disabled; }",
                timeout=8000,
            )
            accounts_response = client.get("/accounts")
            paused = next(a for a in accounts_response.json()["accounts"] if a["account_id"] == "acct1")
            assert paused["enabled"] is False

            # TR-08: Broker account configuration -- a real, second account
            # saved through the same POST /accounts path, always inactive.
            await page.goto(f"{base_url}#/trade/accounts/new")
            await page.wait_for_selector("#tr08-p06", timeout=5000)
            await wait_settled("#tr08-p06")
            assert "Broker account configuration" == await page.inner_text("#route-title")
            await page.fill("#tr08-account-label", "acct2")
            await page.click("#tr08-save")
            await page.wait_for_function(
                "() => document.querySelector('#tr08-action-result') && "
                "document.querySelector('#tr08-action-result').innerText.includes('Saved')",
                timeout=8000,
            )
            accounts_response2 = client.get("/accounts")
            saved = next(a for a in accounts_response2.json()["accounts"] if a["account_id"] == "acct2")
            assert saved["enabled"] is False
            assert saved["broker"] == "paper"
        finally:
            await browser.close()
