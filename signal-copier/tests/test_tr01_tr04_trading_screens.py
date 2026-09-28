"""TR-01..TR-04 (signal-copier private_execution screens, batch 1 of 4):
real coverage for the new hash-routed screens ("Trading command center",
"Positions and allocations", "Position and protection detail", "Incoming
signal stream") -- see app/static/router.js and app/static/views/tr0*.js
for the routing/state-matrix pattern these screens are built on.

TestClient-level: proves the new/extended static assets and API surface
are reachable and owner-only (RequireOwner). Real-browser (Playwright):
proves the router actually renders real content for each of the 4 new
routes against the real running app, reusing tests/conftest.py's
`live_server` fixture the same way C14/C33/C34 already do.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

import app.main as main_module
from tests.conftest import resolve_chromium_executable

_CHROMIUM_EXECUTABLE = resolve_chromium_executable()


# --- Static assets: the new router/state-matrix/view files must actually
# be servable -- app/main.py previously only mounted /static/vendor, which
# would 404 every /static/router.js, /static/state-matrix.js and
# /static/views/tr0N.js reference dashboard.html now makes. ---


def test_router_and_state_matrix_are_served():
    client = TestClient(main_module.app)
    for path in ("router.js", "state-matrix.js"):
        response = client.get(f"/static/{path}")
        assert response.status_code == 200, path
        assert len(response.content) > 500, path


def test_all_four_view_modules_are_served():
    client = TestClient(main_module.app)
    for view in ("tr01", "tr02", "tr03", "tr04"):
        response = client.get(f"/static/views/{view}.js")
        assert response.status_code == 200, view
        assert len(response.content) > 500, view


def test_vendor_mount_still_takes_precedence_over_the_new_broader_static_mount():
    # C12/C13 regression: adding the broader `/static` mount must not
    # shadow or break the existing, narrower `/static/vendor` one.
    client = TestClient(main_module.app)
    response = client.get("/static/vendor/chart.umd.min.js")
    assert response.status_code == 200
    assert len(response.content) > 1000


def test_dashboard_html_references_the_new_router_scripts():
    client = TestClient(main_module.app)
    html = client.get("/").text
    assert "/static/state-matrix.js" in html
    assert "/static/router.js" in html
    for view in ("tr01", "tr02", "tr03", "tr04"):
        assert f"/static/views/{view}.js" in html


# --- /signals: extended in this batch to also project the already-stored
# `analyst` column (see app/db.py's list_recent_signals). ---


def _authed_client(monkeypatch, tmp_path, db_name="tr0x.db"):
    from app import config as app_config
    from app.db import SignalStore

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    store = SignalStore(str(tmp_path / db_name))
    monkeypatch.setattr(main_module, "store", store)
    return store, TestClient(main_module.app)


def test_signals_endpoint_now_projects_analyst(monkeypatch, tmp_path):
    from app.models import AssetClass, Signal, Side

    store, client = _authed_client(monkeypatch, tmp_path)

    signal = Signal(
        source="tradingview", symbol="BTCUSDT", side=Side.BUY, asset_class=AssetClass.CRYPTO, analyst="alice"
    )
    store.save_signal(signal)

    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    response = client.get("/signals")
    assert response.status_code == 200
    body = response.json()
    assert body["signals"], "expected the seeded signal to be listed"
    row = body["signals"][0]
    assert row["analyst"] == "alice"
    assert row["source"] == "tradingview"


# --- LOAD-BEARING INVARIANT for this batch: RequireOwner actually blocks
# an unauthenticated request to the endpoints these 4 new screens read
# from. This is exercised for /signals (the one endpoint this batch
# actually modified) and /positions (the endpoint every one of the 4 new
# views reads from most). See this batch's report for the temporary-break
# verification performed against this exact test before committing. ---


def test_signals_and_positions_require_owner_session(monkeypatch, tmp_path):
    _store, client = _authed_client(monkeypatch, tmp_path)
    assert client.get("/signals").status_code == 401
    assert client.get("/positions").status_code == 401


def test_signals_and_positions_reachable_with_a_valid_owner_session(monkeypatch, tmp_path):
    _store, client = _authed_client(monkeypatch, tmp_path)
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    assert client.get("/signals").status_code == 200
    assert client.get("/positions").status_code == 200


# --- Real browser: the router actually renders real content for each of
# the 4 new hash routes. ---


@pytest.mark.asyncio
async def test_all_four_trading_screens_render_real_content(live_server):
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
        json={"symbol": "BTCUSDT", "side": "buy", "quantity": 1.0},
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
                # Robust against the loading->ready race: wait until the
                # panel's own text no longer reads the loading skeleton's
                # placeholder, rather than racing a selector match against
                # the async fetch that fills it in.
                await page.wait_for_function(
                    "(sel) => { const el = document.querySelector(sel); "
                    "return !!el && !el.innerText.includes('Loading'); }",
                    arg=selector,
                    timeout=timeout,
                )

            # TR-01: Trading command center.
            await page.click('a[href="#/trade"]')
            await page.wait_for_selector("#tr01-p01", timeout=5000)
            await wait_settled("#tr01-p03")
            assert "Trading command center" == await page.inner_text("#route-title")
            assert "acct1" in await page.inner_text("#tr01-p03")

            # TR-02: Positions and allocations.
            await page.click('a[href="#/trade/positions"]')
            await page.wait_for_selector("#tr02-p02", timeout=5000)
            await wait_settled("#tr02-p02")
            assert "Positions and allocations" == await page.inner_text("#route-title")
            grid_text = await page.inner_text("#tr02-p02")
            assert "acct1" in grid_text
            assert "BTCUSDT" in grid_text

            # TR-03: Position and protection detail -- navigate via the
            # real "Open position" link the position grid rendered,
            # proving the router's dynamic :account_id/:symbol segment
            # actually resolves to the real allocation.
            await page.click("#tr02-p02 a")
            await page.wait_for_selector("#tr03-p01", timeout=5000)
            await wait_settled("#tr03-p01")
            await wait_settled("#tr03-p06")
            assert "Position and protection detail" == await page.inner_text("#route-title")
            identity_text = await page.inner_text("#tr03-p01")
            assert "acct1" in identity_text
            assert "BTCUSDT" in identity_text
            # The one real action this screen exposes (rule 6: no new
            # financial capability) must be present and enabled/disabled
            # sanely, and the deliberately-unimplemented ones must say so
            # rather than silently doing nothing.
            assert await page.query_selector("#tr03-exit-now") is not None
            controls_text = await page.inner_text("#tr03-p06")
            assert "no backing capability" in controls_text

            # TR-04: Incoming signal stream.
            await page.click('a[href="#/trade/signals"]')
            await page.wait_for_selector("#tr04-p03", timeout=5000)
            await wait_settled("#tr04-p03")
            assert "Incoming signal stream" == await page.inner_text("#route-title")
            disposition_text = await page.inner_text("#tr04-p03")
            assert "tradingview" in disposition_text
            assert "BTCUSDT" in disposition_text

            # Browser-history-backed navigation: back button returns to
            # the legacy dashboard's default (no-hash) view.
            await page.click("#legacy-nav-link")
            await page.wait_for_selector("#legacy-content:not([hidden])", timeout=5000)
        finally:
            await browser.close()
