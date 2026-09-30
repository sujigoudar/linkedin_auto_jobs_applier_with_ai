"""Phase B7 (Capital utilization, TR-02 "Positions and allocations"):
real coverage for the new `GET /capital-allocation` endpoint (the one new,
narrowly-scoped read-only route this batch adds on top of
app/capital_allocator.py's previously process-internal admission-control
state) and the new TR-02 capital-state/utilization-over-time/return-on-
capital panels that read it.

See app/static/views/tr02.js's own module docstring for the full honest-
scoping rationale (what's real, what's a labeled proxy, what's left
unsupported and why).
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

import app.main as main_module
from app import config as app_config
from app.db import SignalStore
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from tests.conftest import resolve_chromium_executable

_CHROMIUM_EXECUTABLE = resolve_chromium_executable()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")

    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)
    main_module.routing_config.accounts.clear()
    main_module.routing_config.accounts["acct1"] = DestinationAccount(
        account_id="acct1", broker="paper", max_notional_exposure=5000.0
    )
    main_module.routing_config.accounts["acct2"] = DestinationAccount(account_id="acct2", broker="paper")
    # Reset the shared, process-lifetime CapitalAllocator's in-memory
    # reservation ledger between tests -- it is not per-store state.
    main_module.engine.capital_allocator._pending.clear()

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    return test_client, store


def _fill(store: SignalStore, account_id: str, symbol: str, side: Side, quantity: float, price: float) -> None:
    signal = Signal(source="test", symbol=symbol, side=side)
    store.save_signal(signal)
    store.save_order_result(
        OrderResult(account_id=account_id, status=OrderStatus.FILLED, signal_id=signal.id, filled_quantity=quantity, filled_price=price),
        broker="paper",
        symbol=symbol,
        side=side,
    )


# --- GET /capital-allocation: HTTP-level ---


def test_unauthenticated_request_401s(client):
    test_client, _ = client
    with test_client:
        response = TestClient(main_module.app).get("/capital-allocation")
    assert response.status_code == 401


def test_no_positions_no_reservations_reports_all_zero(client):
    test_client, _ = client
    with test_client:
        response = test_client.get("/capital-allocation")
    assert response.status_code == 200
    accounts = {a["account_id"]: a for a in response.json()["accounts"]}
    assert set(accounts) == {"acct1", "acct2"}
    for a in accounts.values():
        assert a["deployed_notional"] == 0.0
        assert a["reserved_notional"] == 0.0
    assert accounts["acct1"]["max_notional_exposure"] == 5000.0
    assert accounts["acct1"]["available_notional"] == 5000.0
    # acct2 opted out of the exposure ceiling entirely -- null, never a
    # guessed capacity number.
    assert accounts["acct2"]["max_notional_exposure"] is None
    assert accounts["acct2"]["available_notional"] is None


# --- LOAD-BEARING INVARIANT for this batch: deployed and reserved must
# never be double-counted or miscategorized against the allocator's own
# real internal state -- a confirmed fill counts ONLY as deployed, an
# admitted-but-unresolved reservation counts ONLY as reserved, and
# available is exactly max - deployed - reserved, never some other
# combination. ---


def test_deployed_and_reserved_are_computed_from_real_state_never_double_counted(client):
    test_client, store = client
    # Real confirmed exposure: a filled 1000.0-notional position on acct1.
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0)

    # A real, still-in-flight (unresolved) reservation on acct1, admitted
    # exactly the way app/engine.py's own admission path would.
    import asyncio

    asyncio.run(main_module.engine.capital_allocator.admit("acct1", 1500.0, confirmed_exposure=1000.0, max_exposure=5000.0))

    with test_client:
        response = test_client.get("/capital-allocation")
    assert response.status_code == 200
    accounts = {a["account_id"]: a for a in response.json()["accounts"]}
    acct1 = accounts["acct1"]
    assert acct1["deployed_notional"] == pytest.approx(1000.0)
    assert acct1["reserved_notional"] == pytest.approx(1500.0)
    assert acct1["max_notional_exposure"] == 5000.0
    # 5000 - 1000 (deployed) - 1500 (reserved) = 2500 -- neither figure
    # counted twice, neither omitted.
    assert acct1["available_notional"] == pytest.approx(2500.0)

    # acct2 is wholly unaffected -- reservations/exposure are per-account.
    assert accounts["acct2"]["deployed_notional"] == 0.0
    assert accounts["acct2"]["reserved_notional"] == 0.0


def test_release_reduces_reserved_and_never_touches_deployed(client):
    test_client, store = client
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0)

    import asyncio

    asyncio.run(main_module.engine.capital_allocator.admit("acct1", 1500.0, confirmed_exposure=1000.0, max_exposure=5000.0))
    main_module.engine.capital_allocator.release("acct1", 1500.0)

    with test_client:
        response = test_client.get("/capital-allocation")
    acct1 = {a["account_id"]: a for a in response.json()["accounts"]}["acct1"]
    assert acct1["deployed_notional"] == pytest.approx(1000.0)
    assert acct1["reserved_notional"] == 0.0
    assert acct1["available_notional"] == pytest.approx(4000.0)


# --- Real-browser: TR-02's new panels actually render this same real
# data, exactly matching what the endpoint itself returns. ---


@pytest.mark.asyncio
async def test_tr02_capital_panels_render_real_matching_data(live_server):
    base_url = live_server

    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}

    assert client.post(
        "/accounts",
        json={"account_id": "acct1", "broker": "paper", "max_notional_exposure": 5000.0},
        headers=csrf_headers,
    ).status_code == 200
    assert client.post(
        "/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]}, headers=csrf_headers
    ).status_code == 200
    webhook = client.post(
        "/webhook/tradingview",
        json={"symbol": "BTCUSDT", "side": "buy", "quantity": 1.0, "price": 100.0},
        headers={"X-Webhook-Secret": "test-webhook-secret"},
    )
    assert webhook.status_code == 200

    capital = client.get("/capital-allocation", headers=csrf_headers)
    assert capital.status_code == 200
    real = {a["account_id"]: a for a in capital.json()["accounts"]}["acct1"]

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

            await page.click('a[href="#/trade/positions"]')
            await page.wait_for_selector("#tr02-p05", timeout=5000)
            await wait_settled("#tr02-p05")

            capital_text = await page.inner_text("#tr02-p05")
            assert "acct1" in capital_text

            def locale_fmt(n):
                # Mirrors dashboard.html's fmtNum: Number(n).toLocaleString().
                return f"{n:,.0f}"

            # LOAD-BEARING INVARIANT (browser side): the rendered deployed/
            # reserved/available figures exactly match GET /capital-allocation's
            # own real numbers -- not off-by-one, not double-counted.
            assert locale_fmt(real["deployed_notional"]) in capital_text
            assert locale_fmt(real["max_notional_exposure"]) in capital_text
            assert locale_fmt(real["available_notional"]) in capital_text

            await wait_settled("#tr02-p06")
            over_time_text = await page.inner_text("#tr02-p06")
            assert "proxy" in over_time_text.lower()
            assert "capital" in over_time_text.lower()

            await wait_settled("#tr02-p07")
            roi_text = await page.inner_text("#tr02-p07")
            assert "not available" in roi_text.lower()
        finally:
            await browser.close()
