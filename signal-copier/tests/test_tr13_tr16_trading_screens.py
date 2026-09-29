"""TR-13..TR-16 (signal-copier private_execution screens, batch 4 of 4 --
the FINAL batch of the TR-0X console): real coverage for "Reconciliation
and trading incidents", "Trading performance and execution quality",
"Historical signal backtests" and "Private settings, site role and
recovery" -- see app/static/router.js and app/static/views/tr0*.js for the
routing/state-matrix pattern established in batch 1 and extended in
batches 2 and 3 (tests/test_tr01_tr04_trading_screens.py,
tests/test_tr05_tr08_trading_screens.py,
tests/test_tr09_tr12_trading_screens.py).

TestClient-level: proves the new static assets are reachable, every
endpoint these 4 screens read from stays owner-gated, and (the LOAD-
BEARING invariant for this batch) the new GET /system/info endpoint
(TR-16) never leaks a secret/credential value. Real-browser (Playwright):
proves the router renders real content for all 4 new routes end to end,
including a real, small, synchronous backtest run through TR-15's form.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


# --- Static assets ---


def test_all_four_new_view_modules_are_served():
    client = TestClient(main_module.app)
    for view in ("tr13", "tr14", "tr15", "tr16"):
        response = client.get(f"/static/views/{view}.js")
        assert response.status_code == 200, view
        assert len(response.content) > 500, view


def test_dashboard_html_references_the_new_view_scripts():
    client = TestClient(main_module.app)
    html = client.get("/").text
    for view in ("tr13", "tr14", "tr15", "tr16"):
        assert f"/static/views/{view}.js" in html


def test_dashboard_nav_links_to_all_four_new_routes():
    client = TestClient(main_module.app)
    html = client.get("/").text
    for route in ("#/trade/incidents", "#/trade/performance", "#/trade/backtests", "#/trade/system"):
        assert f'href="{route}"' in html


def _authed_client(monkeypatch, tmp_path, db_name="tr13_16_batch4.db"):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    store = SignalStore(str(tmp_path / db_name))
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)
    return store, TestClient(main_module.app)


def test_new_screens_read_endpoints_require_owner_session(monkeypatch, tmp_path):
    _store, client = _authed_client(monkeypatch, tmp_path)
    main_module.routing_config.accounts["acct1"] = DestinationAccount(account_id="acct1", broker="paper")
    assert client.get("/system/info").status_code == 401
    assert client.get("/accounts/acct1/economics").status_code == 401
    assert client.get("/accounts/acct1/execution-quality").status_code == 401
    assert client.get("/positions").status_code == 401
    assert client.get("/orders").status_code == 401
    assert client.post("/backtest", json={"source": "x", "start": "2024-01-01T00:00:00Z", "end": "2024-01-02T00:00:00Z", "csv_paths": {}}).status_code == 401


def test_new_screens_read_endpoints_reachable_with_a_valid_owner_session(monkeypatch, tmp_path):
    _store, client = _authed_client(monkeypatch, tmp_path)
    main_module.routing_config.accounts["acct1"] = DestinationAccount(account_id="acct1", broker="paper")
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    assert client.get("/system/info").status_code == 200
    assert client.get("/accounts/acct1/economics").status_code == 200
    assert client.get("/accounts/acct1/execution-quality").status_code == 200
    assert client.get("/positions").status_code == 200
    assert client.get("/orders").status_code == 200


# --- LOAD-BEARING INVARIANT for this batch: the new GET /system/info
# endpoint (TR-16's "Private settings, site role and recovery") never
# leaks a secret/credential value into the response, no matter how many
# of app/config.py's secret fields are actually configured. TR-16 is the
# one screen in this batch whose entire purpose is showing operational
# facts an operator would otherwise have to read from raw environment
# variables -- the one real way this build could regress into leaking a
# credential to the browser is this exact endpoint growing a field that
# echoes one back. See this batch's report for the temporary-break
# verification performed against this exact test before committing. ---


def test_system_info_never_leaks_a_configured_secret(monkeypatch, tmp_path):
    _store, client = _authed_client(monkeypatch, tmp_path)
    # Every secret-shaped field in app/config.py's _Settings, set to a
    # distinctive marker value that would be unmistakable if it leaked.
    secret_fields = {
        "WEBHOOK_SHARED_SECRET": "MARKER-webhook-shared-secret",
        "OWNER_PASSWORD_HASH": "",  # kept blank: OWNER_PASSWORD is the active credential below
        "SESSION_SECRET": "test-session-secret",
        "TELEGRAM_BOT_TOKEN": "MARKER-telegram-bot-token",
        "DISCORD_BOT_TOKEN": "MARKER-discord-bot-token",
        "SLACK_BOT_TOKEN": "MARKER-slack-bot-token",
        "SLACK_APP_TOKEN": "MARKER-slack-app-token",
        "TWITTER_BEARER_TOKEN": "MARKER-twitter-bearer-token",
        "TWILIO_AUTH_TOKEN": "MARKER-twilio-auth-token",
        "WHATSAPP_APP_SECRET": "MARKER-whatsapp-app-secret",
        "WHATSAPP_VERIFY_TOKEN": "MARKER-whatsapp-verify-token",
        "NINJATRADER_WEBHOOK_SECRET": "MARKER-ninjatrader-webhook-secret",
        "MT4_MT5_METAAPI_TOKEN": "MARKER-mt4-mt5-metaapi-token",
        "RITHMIC_PASSWORD": "MARKER-rithmic-password",
        "RELAY_SIGNING_SECRET": "MARKER-relay-signing-secret",
        "FRED_API_KEY": "MARKER-fred-api-key",
    }
    for field, value in secret_fields.items():
        monkeypatch.setattr(app_config, field, value)
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "MARKER-owner-password")

    login = client.post("/auth/login", json={"password": "MARKER-owner-password"})
    assert login.status_code == 200

    response = client.get("/system/info")
    assert response.status_code == 200
    raw = response.text

    for field, value in secret_fields.items():
        if not value:
            continue
        assert value not in raw, f"{field}'s configured value leaked into GET /system/info"
    assert "MARKER-owner-password" not in raw

    # Sanity check the endpoint isn't just returning an empty/trivial body
    # (which would make the assertions above vacuous) -- real fields are present.
    body = response.json()
    assert body["auth_configured"] is True
    assert body["owner_credential_kind"] == "plain (OWNER_PASSWORD)"
    assert "schema_version" in body
    assert "schema_head" in body
    assert body["standby_mode"] is False


def test_system_info_reports_real_schema_version_matching_code_head(monkeypatch, tmp_path):
    from app.db import alembic_code_head

    store, client = _authed_client(monkeypatch, tmp_path)
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    response = client.get("/system/info")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == store.schema_version()
    assert body["schema_head"] == alembic_code_head()
    assert body["schema_version"] == body["schema_head"]  # a freshly stamped database is always at head


# --- P0-8 (external release audit): GET /system/readiness splits
# "reachable" from "ready" into six independently-computed dimensions
# (liveness/data_readiness/market_data_readiness/trading_authority/
# protection_readiness/release_status) plus a rollup computed ONLY from
# them (app/main.py's `_compute_readiness_rollup`). These tests are the
# load-bearing coverage for that split -- see this batch's own report for
# the temporary-collapse verification performed against
# test_readiness_liveness_up_while_data_readiness_unknown_render_both
# before committing. ---


class _UnverifiableBalanceBroker:
    """A broker that IS reachable (liveness/health say nothing about it at
    all) but genuinely cannot report a usable account balance this cycle
    -- the exact "reachable alongside unknown balance" shape the audit
    calls out. `has_balance_capability` is True (the method is overridden),
    so this is honestly `unknown`, never `not_tracked` (which would imply
    no capability exists at all)."""

    name = "unverifiable_balance_broker"

    async def get_account_balance(self, account):  # noqa: ARG002 - interface
        return None

    async def close(self) -> None:
        # BrokerAdapter's own safe no-op (app/brokers/base.py) -- every
        # registered broker's close() is awaited on app shutdown
        # (app/main.py's lifespan), so a test double registered into the
        # real, shared `brokers` dict must implement this too.
        return None

    @property
    def has_balance_capability(self) -> bool:
        return True


def test_readiness_endpoint_requires_owner_session(monkeypatch, tmp_path):
    _store, client = _authed_client(monkeypatch, tmp_path)
    assert client.get("/system/readiness").status_code == 401


def test_readiness_endpoint_returns_all_six_dimensions_and_a_rollup(monkeypatch, tmp_path):
    _store, client = _authed_client(monkeypatch, tmp_path)
    main_module.routing_config.accounts.clear()
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    response = client.get("/system/readiness")
    assert response.status_code == 200
    body = response.json()
    for key in (
        "liveness",
        "data_readiness",
        "market_data_readiness",
        "trading_authority",
        "protection_readiness",
        "release_status",
        "rollup",
    ):
        assert key in body, key
    assert body["liveness"]["status"] == "up"
    assert body["rollup"]["label"] in ("ACTIVE", "STANDBY", "DEGRADED", "NOT READY")
    # No accounts configured -- data_readiness is honestly not_tracked,
    # never fabricated as "fresh".
    assert body["data_readiness"]["status"] == "not_tracked"
    # No managed-lifecycle position open -- protection_readiness is
    # honestly not_tracked, never fabricated as "current".
    assert body["protection_readiness"]["status"] == "not_tracked"
    # release_status/trading_authority are documented placeholders pending
    # the P0-7/P0-6 sibling batches -- never fabricated as approved/held.
    assert body["release_status"]["status"] == "not_tracked"
    assert "P0-7" in body["release_status"]["reason"]


# --- LOAD-BEARING: "reachable" (liveness=up) must never render as if
# everything else is fine -- data_readiness must independently show
# `unknown` for an account whose broker cannot report a usable balance,
# and the overall rollup must reflect that (not stay ACTIVE). ---
def test_readiness_liveness_up_while_data_readiness_unknown_render_both(monkeypatch, tmp_path):
    _store, client = _authed_client(monkeypatch, tmp_path)
    monkeypatch.setitem(main_module.brokers, "unverifiable_balance_broker", _UnverifiableBalanceBroker())
    main_module.routing_config.accounts.clear()
    main_module.routing_config.accounts["acct1"] = DestinationAccount(account_id="acct1", broker="unverifiable_balance_broker")
    # Every OTHER dimension healthy (a fresh worker pass each) -- isolates
    # this test to data_readiness alone, the exact "reachable alongside
    # unknown balance" shape the audit calls out, not a fresh-startup
    # NOT READY from price_monitor/reconciler never having run.
    now = datetime.now(timezone.utc)
    main_module.price_monitor.last_success_at = now
    main_module.reconciler.last_success_at = now
    main_module.provider_scout.last_success_at = now
    main_module.equity_snapshotter.last_success_at = now
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    response = client.get("/system/readiness")
    assert response.status_code == 200
    body = response.json()

    # Both facts must be present and correct, independently:
    assert body["liveness"]["status"] == "up"
    assert body["data_readiness"]["status"] == "unknown"
    accounts = body["data_readiness"]["accounts"]
    assert len(accounts) == 1
    assert accounts[0]["account_id"] == "acct1"
    assert accounts[0]["status"] == "unknown"
    # An unknown data-readiness account must not silently keep the rollup
    # at ACTIVE -- it must read DEGRADED (not a critical position-safety
    # gate, but real and visible, never silent).
    assert body["rollup"]["label"] == "DEGRADED"
    assert "account balance" in body["rollup"]["reason"]


# --- LOAD-BEARING: trading_authority absent (standby -- not holding
# writer role) must block an ACTIVE rollup regardless of every other
# dimension looking fine. ---
def test_readiness_rollup_blocked_from_active_when_trading_authority_not_held(monkeypatch, tmp_path):
    monkeypatch.setattr(app_config, "STANDBY_MODE", True)
    _store, client = _authed_client(monkeypatch, tmp_path)
    main_module.routing_config.accounts.clear()
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    response = client.get("/system/readiness")
    assert response.status_code == 200
    body = response.json()
    assert body["trading_authority"]["status"] == "not_held"
    assert body["rollup"]["label"] != "ACTIVE"
    assert body["rollup"]["label"] == "STANDBY"


# --- LOAD-BEARING: an unconfirmed stop on an open managed-lifecycle
# position must force NOT READY, never let the rollup read ACTIVE just
# because /health's own three flags are all green. ---
def test_readiness_rollup_not_ready_when_a_managed_lifecycle_stop_is_unconfirmed(monkeypatch, tmp_path):
    from app.brokers.paper import PaperBroker
    from app.lifecycle.manager import PositionLifecycleManager
    from app.lifecycle.models import PositionPlan
    from app.models import Signal

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    store = SignalStore(str(tmp_path / "tr16_protection_gap.db"))
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)

    class NoOpStopBroker(PaperBroker):
        async def place_protective_stop(self, *a, **k):  # noqa: ANN002, ANN003 - test double
            return None

    broker = NoOpStopBroker()
    fresh_lifecycle_manager = PositionLifecycleManager(brokers={**main_module.brokers, "paper": broker}, store=store)
    fresh_lifecycle_manager.capital_allocator = main_module.engine.capital_allocator
    monkeypatch.setattr(main_module, "lifecycle_manager", fresh_lifecycle_manager)
    monkeypatch.setattr(main_module.engine, "lifecycle_manager", fresh_lifecycle_manager)

    main_module.routing_config.accounts.clear()
    main_module.routing_config.accounts["acct1"] = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)

    plan = PositionPlan(account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=10, broker="paper", initial_stop=90)
    fresh_lifecycle_manager.start_plan(plan)

    import asyncio

    async def _fill():
        await broker.place_order(
            Signal(source="t", symbol="AAPL", side=Side.BUY),
            main_module.routing_config.accounts["acct1"],
            10,
            "AAPL",
        )
        await fresh_lifecycle_manager.on_entry_fill(main_module.routing_config.accounts["acct1"], "AAPL", 10)

    asyncio.run(_fill())

    client = TestClient(main_module.app)
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    response = client.get("/system/readiness")
    assert response.status_code == 200
    body = response.json()
    assert body["protection_readiness"]["status"] == "gap"
    assert body["protection_readiness"]["stop_gap_count"] == 1
    assert body["rollup"]["label"] == "NOT READY"


# --- TR-14's real economics/execution-quality reuse (E06/E05, already
# tested endpoints -- this only proves the screen's own aggregation logic
# has real, non-empty data to read when a real fill exists). ---


def test_account_economics_and_execution_quality_reflect_a_real_fill(monkeypatch, tmp_path):
    store, client = _authed_client(monkeypatch, tmp_path)
    main_module.routing_config.accounts["acct1"] = DestinationAccount(account_id="acct1", broker="paper")
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    buy_signal = Signal(source="test", symbol="AAPL", side=Side.BUY, received_at=t0)
    sell_signal = Signal(source="test", symbol="AAPL", side=Side.SELL, received_at=t0)
    store.save_signal(buy_signal)
    store.save_signal(sell_signal)
    store.save_order_result(
        OrderResult(account_id="acct1", status=OrderStatus.FILLED, signal_id=buy_signal.id, filled_quantity=10.0, filled_price=100.0, executed_at=t0 + timedelta(seconds=2)),
        broker="paper", symbol="AAPL", side=Side.BUY,
    )
    store.save_order_result(
        OrderResult(account_id="acct1", status=OrderStatus.FILLED, signal_id=sell_signal.id, filled_quantity=10.0, filled_price=110.0, executed_at=t0 + timedelta(seconds=3)),
        broker="paper", symbol="AAPL", side=Side.SELL,
    )

    econ = client.get("/accounts/acct1/economics")
    assert econ.status_code == 200
    assert econ.json()["realized_pnl"] == pytest.approx(100.0)
    assert econ.json()["per_symbol"]["AAPL"]["completed_episodes"] == 1

    quality = client.get("/accounts/acct1/execution-quality")
    assert quality.status_code == 200
    assert quality.json()["per_symbol"]["AAPL"]["sample_count"] == 2


# --- TR-13's real reconciliation-derived incident data: GET /positions'
# managed_lifecycles carries real halted/uncovered state that this screen
# reads verbatim -- no separate incidents store exists (see tr13.js's own
# module docstring), so this proves the exact fields the screen depends on. ---


def test_positions_endpoint_exposes_the_real_fields_tr13_depends_on(monkeypatch, tmp_path):
    _store, client = _authed_client(monkeypatch, tmp_path)
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    response = client.get("/positions")
    assert response.status_code == 200
    body = response.json()
    assert "positions" in body
    assert "managed_lifecycles" in body  # halted/uncovered_quantity/pending_exit/pending_entry live here


# --- TR-15's real, small, synchronous backtest run (already-tested
# POST /backtest -- this only proves it's reachable and produces a real
# result the screen's own Reports panel renders unmodified). ---


def test_backtest_endpoint_runs_a_real_small_replay(monkeypatch, tmp_path):
    store, client = _authed_client(monkeypatch, tmp_path)
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]

    store.save_signal(
        Signal(
            source="tradingview",
            symbol="AAPL",
            side=Side.BUY,
            quantity=10.0,
            price=100.0,
            stop_loss=95.0,
            take_profit=110.0,
            received_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
    )
    csv_path = tmp_path / "AAPL.csv"
    csv_path.write_text("timestamp,open,high,low,close\n2024-01-02T00:00:00+00:00,101,111,100,110\n")

    response = client.post(
        "/backtest",
        json={
            "source": "tradingview",
            "symbol": "AAPL",
            "start": "2024-01-01T00:00:00+00:00",
            "end": "2024-01-31T00:00:00+00:00",
            "csv_paths": {"AAPL": str(csv_path)},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["resolved_trades"] == 1
    assert body["trades"][0]["symbol"] == "AAPL"


# --- Real browser: the router actually renders real content for each of
# the 4 new hash routes, including a real backtest run through the UI. ---


@pytest.mark.asyncio
async def test_all_four_new_trading_screens_render_real_content(live_server, tmp_path):
    base_url = live_server

    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}

    assert client.post(
        "/accounts", json={"account_id": "acct1", "broker": "paper"}, headers=csrf_headers
    ).status_code == 200
    assert client.post(
        "/providers/tradingview", json={"display_name": "TradingView"}, headers=csrf_headers
    ).status_code == 200
    assert client.post(
        "/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]}, headers=csrf_headers
    ).status_code == 200
    # Real filled buy+sell so TR-14's economics/execution-quality panels have real data.
    webhook_headers = {"X-Webhook-Secret": "test-webhook-secret"}
    buy = client.post(
        "/webhook/tradingview",
        json={"symbol": "AAPL", "side": "buy", "quantity": 5.0},
        headers=webhook_headers,
    )
    assert buy.status_code == 200
    sell = client.post(
        "/webhook/tradingview",
        json={"symbol": "AAPL", "side": "sell", "quantity": 5.0},
        headers=webhook_headers,
    )
    assert sell.status_code == 200

    # A real, small OHLC CSV on the SAME machine the live_server subprocess
    # runs on -- POST /backtest's csv_paths is a server-filesystem path
    # (see app/main.py's BacktestRequest docstring), and this test process
    # and that subprocess share a filesystem.
    # The webhook-created signal below is timestamped "now" (real wall-clock
    # time, not a fixed historical date this test controls) -- the CSV bar
    # and the replay's own period must both cover that, so both use a wide
    # future-proof window instead of a hardcoded past date.
    csv_path = tmp_path / "AAPL_backtest.csv"
    csv_path.write_text("timestamp,open,high,low,close\n2030-06-02T00:00:00+00:00,101,111,100,110\n")
    tv_signal = client.post(
        "/webhook/tradingview",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 1.0,
            "price": 100.0,
            "stop_loss": 95.0,
            "take_profit": 110.0,
        },
        headers=webhook_headers,
    )
    assert tv_signal.status_code == 200

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

            # TR-13: Reconciliation and trading incidents.
            await page.click('a[href="#/trade/incidents"]')
            await page.wait_for_selector("#tr13-p03", timeout=5000)
            await wait_settled("#tr13-p03")
            assert "Reconciliation and trading incidents" == await page.inner_text("#route-title")
            timeline_text = await page.inner_text("#tr13-p03")
            assert "AAPL" in timeline_text

            # TR-14: Trading performance and execution quality -- real
            # realized P&L from the buy+sell fills above.
            await page.click('a[href="#/trade/performance"]')
            await page.wait_for_selector("#tr14-p01", timeout=5000)
            await wait_settled("#tr14-p01")
            assert "Trading performance and execution quality" == await page.inner_text("#route-title")
            book_text = await page.inner_text("#tr14-p01")
            assert "acct1" in book_text
            assert "AAPL" in book_text

            # TR-15: Historical signal backtests -- a real, small,
            # synchronous replay run through the actual form.
            await page.click('a[href="#/trade/backtests"]')
            await page.wait_for_selector("#tr15-p03", timeout=5000)
            await wait_settled("#tr15-p03")
            assert "Historical signal backtests" == await page.inner_text("#route-title")
            await page.fill("#tr15-source", "tradingview")
            await page.fill("#tr15-symbol", "AAPL")
            await page.fill("#tr15-start", "2020-01-01T00:00")
            await page.fill("#tr15-end", "2031-01-01T00:00")
            # The CSV bar above is deliberately far in the future (so it
            # always postdates whenever this test actually runs); max_hold
            # must be widened past the default 30 days to actually reach it,
            # or the replay would report NO_PRICE_DATA instead of a real
            # resolved trade -- there is no honest chart to draw from that.
            await page.fill("#tr15-max-hold", "4000")
            await page.fill('[data-csv-row="1"] .tr15-csv-symbol', "AAPL")
            await page.fill('[data-csv-row="1"] .tr15-csv-path', str(csv_path))
            await page.click("#tr15-confirm")
            await page.wait_for_function(
                "() => document.querySelector('#tr15-preview-result') && "
                "document.querySelector('#tr15-preview-result').innerText.includes('Replay complete')",
                timeout=10000,
            )
            reports_text = await page.inner_text("#tr15-p05")
            assert "AAPL" in reports_text
            coverage_text = await page.inner_text("#tr15-p02")
            assert "Resolved trades" in coverage_text

            # LOAD-BEARING: the additive equity-curve chart's own Chart.js
            # dataset must exactly match the real, already-rendered trades
            # table's own pnl figure -- a chart showing the wrong number is
            # worse than no chart at all (see this batch's task brief).
            await page.wait_for_selector("#tr15-equity-chart", timeout=5000)
            table_pnl_text = await page.inner_text("#tr15-p05 table")
            chart_points = await page.evaluate(
                "() => { const c = Chart.getChart(document.getElementById('tr15-equity-chart')); "
                "return c.data.datasets[0].data; }"
            )
            assert len(chart_points) == 1
            # The real TradingView webhook signal above bought at price 100
            # with stop 95 / target 110; the CSV bar (101/111/100/110) fills
            # the take_profit target, so the resolved trade's real pnl is
            # (110 - 100) * 1.0 quantity = 10.0 -- the SAME number the trades
            # table (already asserted against the real POST /backtest
            # response above) must also show.
            assert chart_points[0] == 10.0
            assert "10" in table_pnl_text

            # TR-16: Private settings, site role and recovery.
            await page.click('a[href="#/trade/system"]')
            await page.wait_for_selector("#tr16-p01", timeout=5000)
            await wait_settled("#tr16-p01")
            assert "Private settings, site role and recovery" == await page.inner_text("#route-title")
            role_text = await page.inner_text("#tr16-p01")
            assert "active writer" in role_text
            checklist_text = await page.inner_text("#tr16-p06")
            assert "Database schema" in checklist_text
            # LOAD-BEARING (browser-level restatement): the rendered page
            # text never contains the real WEBHOOK_SHARED_SECRET this very
            # test authenticated webhook calls with above.
            full_page_text = await page.inner_text("body")
            assert "test-webhook-secret" not in full_page_text
        finally:
            await browser.close()
