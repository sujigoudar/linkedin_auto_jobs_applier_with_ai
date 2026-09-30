"""Phase B10 (PU-B10): TR-12's new "Stop & target lifecycle analytics"
panel -- the FIRST consumer of Phase A4's real, append-only
`stop_target_events` log (`GET /positions/{account_id}/{symbol}/stop-events`).

This is a real-browser (Playwright) test against a live `uvicorn`
subprocess (see tests/conftest.py's `live_server`): the two real managed-
lifecycle entries below go through the ACTUAL webhook -> engine ->
PositionLifecycleManager path (real STOP_PLACED events, real entry_price,
real signal/order rows) -- the same techniques Phase A4's own
tests/test_stop_target_events.py and tests/test_stop_target_events_endpoint.py
use, just driven over HTTP since `live_server` is a separate process. A
handful of additional STOP_TIGHTENED/PROTECTION_FAILED/TARGET_HIT events
and one closed-position excursion are then appended with a second
`SignalStore` handle on that SAME database file, calling the exact same
real persistence methods (`record_stop_target_event`,
`record_position_excursion`) app/lifecycle/manager.py and
app/reconciliation.py themselves call -- not a fabricated shape, the real
schema and the real append-only API, just invoked directly rather than
waiting on a live price feed this build has no HTTP endpoint for (see
app/static/views/tr12.js's own module docstring on why the analytics
panel is a client-side aggregate rather than a new backend endpoint).

Every number this test asserts against the rendered page is worked out by
hand from that exact seeded history -- nothing here is charted "because
the code says so"; the arithmetic is redone independently below.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from playwright.async_api import async_playwright

from app.db import SignalStore
from tests.conftest import resolve_chromium_executable

_CHROMIUM_EXECUTABLE = resolve_chromium_executable()


@pytest.mark.asyncio
async def test_stop_target_analytics_panel_matches_real_seeded_event_history(live_server, tmp_path):
    base_url = live_server
    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}

    # --- Real setup: 2 managed_lifecycle accounts, routed by symbol. ---
    assert client.post(
        "/accounts", json={"account_id": "acct1", "broker": "paper", "managed_lifecycle": True}, headers=headers
    ).status_code == 200
    assert client.post(
        "/accounts", json={"account_id": "acct2", "broker": "paper", "managed_lifecycle": True}, headers=headers
    ).status_code == 200
    assert client.post(
        "/providers/telegram", json={"display_name": "Telegram signals"}, headers=headers
    ).status_code == 200
    assert client.post(
        "/routing-rules",
        json={"source": "telegram", "destinations": ["acct1"], "symbol_filter": ["AAPL"]},
        headers=headers,
    ).status_code == 200
    assert client.post(
        "/routing-rules",
        json={"source": "telegram", "destinations": ["acct2"], "symbol_filter": ["MSFT"]},
        headers=headers,
    ).status_code == 200

    # --- Real entry #1: acct1/AAPL, real entry_price=100.0, real
    # stop_loss=95.0 (real distance = 5.0), real take_profit=110.0 (this
    # position IS target-bearing). PaperBroker fills synchronously at
    # signal.price -- see app/brokers/paper.py's place_order. This is the
    # real STOP_PLACED (previous_price=None) event for this pair.
    entry1 = client.post(
        "/webhook/telegram",
        json={"symbol": "AAPL", "side": "buy", "quantity": 10.0, "price": 100.0, "stop_loss": 95.0, "take_profit": 110.0},
        headers={"X-Webhook-Secret": "test-webhook-secret"},
    )
    assert entry1.status_code == 200

    # --- Real entry #2: acct2/MSFT, real entry_price=300.0, real
    # stop_loss=280.0 (real distance = 20.0), NO take_profit -- this
    # position is NOT target-bearing.
    entry2 = client.post(
        "/webhook/telegram",
        json={"symbol": "MSFT", "side": "buy", "quantity": 5.0, "price": 300.0, "stop_loss": 280.0},
        headers={"X-Webhook-Secret": "test-webhook-secret"},
    )
    assert entry2.status_code == 200

    positions = client.get("/positions", headers=headers).json()["managed_lifecycles"]
    aapl_lifecycle = next(p for p in positions if p["account_id"] == "acct1" and p["symbol"] == "AAPL")
    msft_lifecycle = next(p for p in positions if p["account_id"] == "acct2" and p["symbol"] == "MSFT")
    assert aapl_lifecycle["entry_price"] == 100.0
    assert msft_lifecycle["entry_price"] == 300.0
    aapl_events = client.get("/positions/acct1/AAPL/stop-events", headers=headers).json()["events"]
    assert len(aapl_events) == 1 and aapl_events[0]["event_type"] == "stop_placed" and aapl_events[0]["price"] == 95.0
    msft_events = client.get("/positions/acct2/MSFT/stop-events", headers=headers).json()["events"]
    assert len(msft_events) == 1 and msft_events[0]["event_type"] == "stop_placed" and msft_events[0]["price"] == 280.0

    # --- Append the rest of the real history directly with the exact
    # same real, already-tested persistence methods
    # PositionLifecycleManager/app/reconciliation.py themselves call --
    # same database file the live server is serving from. ---
    store = SignalStore(tmp_path / "e2e.db")
    t0 = datetime.now(timezone.utc)
    store.record_stop_target_event(
        "acct1", "AAPL", event_type="stop_tightened", at=t0 + timedelta(seconds=1), price=97.0, previous_price=95.0, source="signal"
    )
    store.record_stop_target_event(
        "acct1", "AAPL", event_type="stop_tightened", at=t0 + timedelta(seconds=2), price=99.0, previous_price=97.0, source="signal"
    )
    store.record_stop_target_event(
        "acct1", "AAPL", event_type="target_hit", at=t0 + timedelta(seconds=3), price=110.0, previous_price=None, source="signal"
    )
    store.record_stop_target_event(
        "acct2", "MSFT", event_type="protection_failed", at=t0 + timedelta(seconds=1), price=280.0, previous_price=None, source="signal"
    )
    # A third, CLOSED position (acct1/GOOG) with a real excursion row (PU-A1's
    # own real persistence method) and a real genuine-first STOP_PLACED --
    # entry_price=1500.0, stop=1450.0 (real distance = 50.0). This pair has
    # no real `orders` row at all (never went through the engine), so its
    # target status must come back genuinely UNKNOWN, not assumed either way.
    store.record_position_excursion(
        "acct1",
        "GOOG",
        side="buy",
        entry_price=1500.0,
        highest_price_since_entry=1500.0,
        highest_price_at=t0,
        lowest_price_since_entry=1500.0,
        lowest_price_at=t0,
        mae=0.0,
        mfe=0.0,
        has_price_data=True,
        closed_at=t0,
    )
    store.record_stop_target_event(
        "acct1", "GOOG", event_type="stop_placed", at=t0, price=1450.0, previous_price=None, source="signal"
    )

    # --- Hand-computed expected aggregate, independent of the JS itself:
    #   stop-tightening: 2 total, both acct1/AAPL -- acct1: 2, AAPL: 2.
    #   protection-failure rate: 1 PROTECTION_FAILED / (1 + 3 STOP_PLACED) = 1/4 = 25%.
    #   initial-stop distances: |95-100|=5, |280-300|=20, |1450-1500|=50 -- 3 charted, 0 unknown-entry.
    #   target-hit rate: 1 TARGET_HIT / 1 target-bearing position (AAPL only) = 100%,
    #     with acct2/MSFT resolved-but-no-target (excluded from both sides) and
    #     acct1/GOOG's target status genuinely unknown (1 excluded/disclosed).
    # ---

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)

            await page.click('a[href="#/trade/policies"]')
            await page.wait_for_selector("#tr12-p07", timeout=5000)
            await page.wait_for_function(
                "() => { const el = document.querySelector('#tr12-p07'); "
                "return !!el && el.innerText.includes('Stop-tightening trajectory'); }",
                timeout=10000,
            )

            panel_text = await page.inner_text("#tr12-p07")
            assert "3 real (account, symbol) positions" in panel_text or "3 real (account, symbol) position" in panel_text

            # 1. Stop-tightening frequency: overall real total of 2, both on acct1/AAPL.
            assert "Overall: 2 real tightening events" in panel_text
            assert "acct1" in panel_text and "AAPL" in panel_text

            # 2. Protection-failure rate: 1 of 4 = 25%.
            assert "25%" in panel_text
            assert "1 PROTECTION_FAILED of 4 real stop attempts" in panel_text

            # 3. Initial-stop-distance distribution: 3 charted, 0 excluded.
            assert "3 genuine first placements charted" in panel_text
            assert await page.locator("#tr12-initial-stop-distance-chart").count() == 1

            # 4. Target-hit rate: 1 of 1 = 100%, 1 unknown disclosed.
            assert "100%" in panel_text
            assert "1 real TARGET_HIT events of 1 real positions" in panel_text
            assert "1 position(s) excluded from this rate" in panel_text

            # 5. Stop-tightening trajectory drill-down for acct1/AAPL: the
            # real (previous_price -> price) sequence, in order.
            await page.select_option("#tr12-trajectory-position", "acct1::AAPL")
            await page.click("#tr12-trajectory-run")
            await page.wait_for_function(
                "() => document.querySelector('#tr12-trajectory-result') && "
                "document.querySelector('#tr12-trajectory-result').innerText.includes('99')",
                timeout=5000,
            )
            trajectory_text = await page.inner_text("#tr12-trajectory-result")
            assert "95" in trajectory_text and "97" in trajectory_text and "99" in trajectory_text

            # Deferred items are disclosed, not silently dropped.
            assert "always 1 take_profit target per entry" in panel_text or "Target-count distribution" in panel_text
            assert "Stop-efficiency-vs-MFE scatter" in panel_text
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_stop_target_analytics_panel_shows_honest_zero_with_no_positions(live_server):
    """No managed-lifecycle position has ever existed for this scope --
    the panel must say so honestly rather than render a table of
    fabricated zeroes for a universe that doesn't exist."""
    base_url = live_server
    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    assert client.post(
        "/accounts", json={"account_id": "acct1", "broker": "paper"}, headers=headers
    ).status_code == 200
    assert client.post(
        "/providers/telegram", json={"display_name": "Telegram signals"}, headers=headers
    ).status_code == 200

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)

            await page.click('a[href="#/trade/policies"]')
            await page.wait_for_selector("#tr12-p07", timeout=5000)
            await page.wait_for_function(
                "() => { const el = document.querySelector('#tr12-p07'); "
                "return !!el && !el.innerText.includes('Loading'); }",
                timeout=10000,
            )
            panel_text = await page.inner_text("#tr12-p07")
            assert "no real (account, symbol) universe" in panel_text
            assert await page.locator("#tr12-initial-stop-distance-chart").count() == 0
        finally:
            await browser.close()
