"""PU-B2: TR-03's real MAE/MFE excursion chart (`#/trade/positions/:account_id/:symbol`).

Real backing data: PU-A1's own `PositionLifecycle.entry_price`/
`highest_price_since_entry`/`lowest_price_since_entry`/`mae`/`mfe`/
`has_price_data`, already projected onto every `managed_lifecycles` row
by GET /positions (app/main.py's `_managed_lifecycle_snapshot`) -- this
batch adds NO new backend endpoint, only app/static/views/tr03.js's
rendering of data that already exists.

Seeding a real, multi-tick price sequence for a managed-lifecycle
position end-to-end over HTTP is not possible in this build: PaperBroker
(app/brokers/paper.py) never implements `get_last_price`
(`has_last_price_capability` is False), so app/pricing.py's PriceMonitor
never polls it, and there is no HTTP route anywhere that accepts an
external price tick for a managed lifecycle (that surface simply doesn't
exist -- see app/pricing.py's own module docstring). The one real price
observation an HTTP-only flow CAN produce is the entry fill price itself
(PaperBroker fills at `signal.price`, threaded through engine.py's
`_handle_managed_entry` into `on_entry_fill(..., entry_price=...)`),
which only ever gives a degenerate (single-point, mae=mfe=0) series.

So this test drives the exact same real, production entry point real
price ticks arrive through -- `PositionLifecycleManager.on_price_update`
(app/lifecycle/manager.py), the very method app/pricing.py's PriceMonitor
itself calls for every real broker tick -- directly, against the
in-process app's own live `lifecycle_manager` object, scheduled onto the
real server's own event loop via `asyncio.run_coroutine_threadsafe` so it
races the real HTTP-driven admission/webhook flow exactly the way a real
concurrent price feed would. Nothing about the computed MAE/MFE/extremes
is fabricated: every value asserted below is `PositionLifecycle`'s own
side-aware `mae`/`mfe` properties (app/lifecycle/models.py), computed
from this exact real price sequence, read back through the real GET
/positions endpoint AND the real rendered page.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time
from contextlib import closing
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
    in-process (a background thread, its own event loop) rather than as a
    subprocess -- unlike `tests/conftest.py`'s `live_server`, this gives
    the test a handle on the exact same live `lifecycle_manager` object
    the running server's HTTP handlers read from, so a real price tick
    can be injected onto it (see this module's docstring for why that's
    necessary and still fully real).

    Rewires every module-global this app wires together at import time
    (`store`, `lifecycle_manager`, and `engine`'s own `store`/
    `lifecycle_manager` attributes) onto a fresh, this-test-only
    `SignalStore`/`PositionLifecycleManager` pair -- so this test's data
    never leaks into or out of any other test sharing the same imported
    `app.main` module, the same way `tests/test_tr01_tr04_trading_screens.py`'s
    `_authed_client` helper does for its own TestClient-level tests.
    """
    from app import config as app_config
    from app.db import SignalStore
    from app.lifecycle.manager import PositionLifecycleManager
    import app.main as main_module

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-pw-tr03")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret-tr03")
    monkeypatch.setattr(app_config, "WEBHOOK_SHARED_SECRET", "test-webhook-secret-tr03")

    fresh_store = SignalStore(str(tmp_path / "tr03_maemfe.db"))
    fresh_lifecycle_manager = PositionLifecycleManager(brokers=main_module.brokers, store=fresh_store)
    fresh_lifecycle_manager.capital_allocator = main_module.engine.capital_allocator

    monkeypatch.setattr(main_module, "store", fresh_store)
    monkeypatch.setattr(main_module, "lifecycle_manager", fresh_lifecycle_manager)
    monkeypatch.setattr(main_module.engine, "store", fresh_store)
    monkeypatch.setattr(main_module.engine, "lifecycle_manager", fresh_lifecycle_manager)

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
        yield base_url, main_module, loop
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _run_coro(loop: asyncio.AbstractEventLoop, coro):
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=5)


@pytest.mark.asyncio
async def test_tr03_mae_mfe_chart_matches_real_seeded_position_data(live_server_inprocess):
    from app.models import DestinationAccount

    base_url, main_module, loop = live_server_inprocess
    account_id = "acct-maemfe"
    symbol = "MAEX"

    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw-tr03"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}

    assert client.post(
        "/accounts",
        json={"account_id": account_id, "broker": "paper", "managed_lifecycle": True},
        headers=csrf_headers,
    ).status_code == 200
    assert client.post(
        "/routing-rules",
        json={"source": "e2e-maemfe", "destinations": [account_id]},
        headers=csrf_headers,
    ).status_code == 200

    # Real entry fill at 100.0, protected by a real stop at 90.0 -- this is
    # the one real price observation an HTTP-only flow can produce (see
    # this module's docstring); PaperBroker fills at exactly `price`.
    webhook = client.post(
        "/webhook/e2e-maemfe",
        json={"symbol": symbol, "side": "buy", "quantity": 10.0, "price": 100.0, "stop_loss": 90.0},
        headers={"X-Webhook-Secret": "test-webhook-secret-tr03"},
    )
    assert webhook.status_code == 200

    # Real price sequence (mirrors tests/test_pu_a1_mae_mfe_tracking.py's own
    # long-position scenario): dips to 95 (adverse), rallies to 112
    # (favorable), settles at 105 -- MAE must reflect the worst dip
    # (100 -> 95 = 5), MFE the best rally (100 -> 112 = 12), never the
    # final settle price. Injected via the exact same real
    # `PositionLifecycleManager.on_price_update` entry point
    # app/pricing.py's PriceMonitor itself calls for a real broker tick.
    account = DestinationAccount(account_id=account_id, broker="paper")
    for price in (98.0, 95.0, 103.0, 112.0, 105.0):
        _run_coro(loop, main_module.lifecycle_manager.on_price_update(account, symbol, price))

    # Cross-check against the real GET /positions response before ever
    # touching the browser -- this is the same real snapshot the page
    # itself fetches.
    positions = client.get("/positions").json()
    lifecycle = next(
        lc for lc in positions["managed_lifecycles"] if lc["account_id"] == account_id and lc["symbol"] == symbol
    )
    assert lifecycle["entry_price"] == 100.0
    assert lifecycle["highest_price_since_entry"] == 112.0
    assert lifecycle["lowest_price_since_entry"] == 95.0
    assert lifecycle["has_price_data"] is True
    assert lifecycle["mae"] == 5.0
    assert lifecycle["mfe"] == 12.0

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw-tr03")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)

            await page.goto(f"{base_url}/#/trade/positions/{account_id}/{symbol}")
            await page.wait_for_selector("#tr03-p07", timeout=5000)

            async def wait_settled(selector, timeout=8000):
                await page.wait_for_function(
                    "(sel) => { const el = document.querySelector(sel); "
                    "return !!el && !el.innerText.includes('Loading'); }",
                    arg=selector,
                    timeout=timeout,
                )

            await wait_settled("#tr03-p07")

            panel_text = await page.inner_text("#tr03-p07")
            assert "100" in panel_text  # entry price
            assert "112" in panel_text  # highest reached
            assert "95" in panel_text  # lowest reached

            # LOAD-BEARING INVARIANT for this batch: the panel's displayed
            # MAE/MFE are PositionLifecycle's own real computed values,
            # never independently recomputed client-side. Assert the exact
            # rendered numbers, not just substring presence (112/95/100/5
            # could otherwise coincidentally overlap).
            mae_text = await page.inner_text("#tr03-mae-value")
            mfe_text = await page.inner_text("#tr03-mfe-value")
            assert mae_text.strip() == "5"
            assert mfe_text.strip() == "12"

            # And the real Chart.js instance's own data -- not just text --
            # carries the exact same real entry/highest/lowest values.
            chart_data = await page.evaluate(
                "() => { const c = Chart.getChart(document.getElementById('tr03-maemfe-chart')); "
                "return c ? c.data.datasets[0].data : null; }"
            )
            assert chart_data == [[100.0, 112.0], [95.0, 100.0]]

            # Real price/stop timeline enrichment: the same real
            # highest/lowest markers, with their real timestamps, appear
            # as additive annotations alongside the order journal.
            timeline_text = await page.inner_text("#tr03-p03")
            assert "Highest price reached" in timeline_text
            assert "Lowest price reached" in timeline_text
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_tr03_mae_mfe_panel_shows_honest_gap_for_a_plain_unmanaged_position(live_server_inprocess):
    """A plain (unmanaged) account position has no `PositionLifecycle` at
    all -- the MAE/MFE panel must say so plainly, never render a chart
    (fabricated or otherwise) for data that structurally does not exist
    for this position."""
    base_url, _main_module, _loop = live_server_inprocess
    account_id = "acct-plain-maemfe"
    symbol = "PLAINX"

    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw-tr03"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}

    assert client.post(
        "/accounts",
        json={"account_id": account_id, "broker": "paper", "managed_lifecycle": False},
        headers=csrf_headers,
    ).status_code == 200
    assert client.post(
        "/routing-rules",
        json={"source": "e2e-plain-maemfe", "destinations": [account_id]},
        headers=csrf_headers,
    ).status_code == 200
    webhook = client.post(
        "/webhook/e2e-plain-maemfe",
        json={"symbol": symbol, "side": "buy", "quantity": 5.0, "price": 50.0},
        headers={"X-Webhook-Secret": "test-webhook-secret-tr03"},
    )
    assert webhook.status_code == 200

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw-tr03")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)

            await page.goto(f"{base_url}/#/trade/positions/{account_id}/{symbol}")
            await page.wait_for_selector("#tr03-p07", timeout=5000)

            async def wait_settled(selector, timeout=8000):
                await page.wait_for_function(
                    "(sel) => { const el = document.querySelector(sel); "
                    "return !!el && !el.innerText.includes('Loading'); }",
                    arg=selector,
                    timeout=timeout,
                )

            await wait_settled("#tr03-p07")
            panel_text = await page.inner_text("#tr03-p07")
            assert "only exists for managed-lifecycle positions" in panel_text
            assert await page.query_selector("#tr03-maemfe-chart") is None
        finally:
            await browser.close()
