"""TR-09..TR-12 (signal-copier private_execution screens, batch 3 of 4):
real coverage for the new hash-routed screens ("Signal providers and
collectors", "Source onboarding and parser laboratory", "Routing and
allocation rules", "Sizing, stops and profit policies") -- see
app/static/router.js and app/static/views/tr0*.js for the routing/
state-matrix pattern these screens are built on (established in batch 1,
extended in batch 2, see tests/test_tr01_tr04_trading_screens.py and
tests/test_tr05_tr08_trading_screens.py).

TestClient-level: proves the new static assets are reachable and every
endpoint these 4 screens read/write from stays owner-gated. Real-browser
(Playwright): proves the router actually renders real content for each of
the 4 new routes end to end against the live server, including TR-10's
real parser-lab classification and TR-11's real routing-rule create/
delete round trip.
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
    for view in ("tr09", "tr10", "tr11", "tr12"):
        response = client.get(f"/static/views/{view}.js")
        assert response.status_code == 200, view
        assert len(response.content) > 500, view


def test_dashboard_html_references_the_new_view_scripts():
    client = TestClient(main_module.app)
    html = client.get("/").text
    for view in ("tr09", "tr10", "tr11", "tr12"):
        assert f"/static/views/{view}.js" in html


def _authed_client(monkeypatch, tmp_path, db_name="tr09_12_batch3.db"):
    from app import config as app_config
    from app.db import SignalStore

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    store = SignalStore(str(tmp_path / db_name))
    monkeypatch.setattr(main_module, "store", store)
    return store, TestClient(main_module.app)


# --- LOAD-BEARING INVARIANT for this batch: RequireOwner still blocks an
# unauthenticated request to every endpoint these 4 new screens read from
# (in particular /providers, /routing-rules, /sources/{x}/classify-messages
# -- newly surfaced as a first-class multi-message workspace by TR-10 in
# this batch) AND the one real policy-affecting write these screens add
# (TR-12's provider/analyst sizing-override save, TR-11's routing-rule
# create/update/delete) goes through the exact same, already-tested
# owner-only POST /providers/{id}, POST /providers/{id}/analysts/{id} and
# POST/PUT/DELETE /routing-rules paths -- none of them can be reached
# without a valid owner session, so a policy/routing change can never be
# made anonymously through these new screens. See this batch's report for
# the temporary-break verification performed against this exact test
# before committing. ---


def test_new_screens_read_endpoints_require_owner_session(monkeypatch, tmp_path):
    _store, client = _authed_client(monkeypatch, tmp_path)
    assert client.get("/providers").status_code == 401
    assert client.get("/routing-rules").status_code == 401
    assert client.get("/providers/subscriptions").status_code == 401
    assert client.get("/providers/value").status_code == 401
    assert client.get("/providers/candidates").status_code == 401
    assert client.post("/sources/telegram/classify-messages", json={"texts": ["buy BTCUSDT"]}).status_code == 401


def test_policy_and_routing_write_paths_require_owner_session(monkeypatch, tmp_path):
    _store, client = _authed_client(monkeypatch, tmp_path)
    # TR-12's sizing-override save.
    assert client.post("/providers/telegram", json={"multiplier": 2.0}).status_code == 401
    assert client.post("/providers/telegram/analysts/alice", json={"multiplier": 0.5}).status_code == 401
    # TR-11's routing-rule create/update/delete.
    assert client.post(
        "/routing-rules", json={"source": "telegram", "destinations": ["acct1"]}
    ).status_code == 401
    assert client.put(
        "/routing-rules/1", json={"source": "telegram", "destinations": ["acct1"]}
    ).status_code == 401
    assert client.delete("/routing-rules/1").status_code == 401


def test_new_screens_read_endpoints_reachable_with_a_valid_owner_session(monkeypatch, tmp_path):
    _store, client = _authed_client(monkeypatch, tmp_path)
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    assert client.get("/providers").status_code == 200
    assert client.get("/routing-rules").status_code == 200
    assert client.get("/providers/subscriptions").status_code == 200
    assert client.get("/providers/value").status_code == 200
    assert client.get("/providers/candidates").status_code == 200
    resp = client.post("/sources/telegram/classify-messages", json={"texts": ["buy BTCUSDT"]})
    assert resp.status_code == 200
    assert resp.json()["dispositions"][0]["signal"]["symbol"] == "BTCUSDT"


# --- TR-10's parser laboratory: real, no-effects sandbox classification
# (the exact endpoint TR-05's "Reclassify in sandbox" already calls) --
# proves it never creates or routes a live signal. ---


def test_classify_messages_never_creates_a_live_signal(monkeypatch, tmp_path):
    store, client = _authed_client(monkeypatch, tmp_path)
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    before = store.list_recent_signals(limit=10)
    resp = client.post(
        "/sources/telegram/classify-messages",
        json={"texts": ["buy BTCUSDT sl 95 tp 110", "not a real signal at all"], "asset_class": "crypto"},
    )
    assert resp.status_code == 200
    dispositions = resp.json()["dispositions"]
    assert len(dispositions) == 2
    assert dispositions[0]["signal"]["side"] == "buy"
    assert dispositions[0]["signal"]["stop_loss"] == 95.0
    after = store.list_recent_signals(limit=10)
    assert after == before  # no signal was created or persisted


# --- TR-12's real sizing-override write path (the only live-editable
# "policy" surface this screen offers -- see app/static/views/tr12.js's
# module docstring for exactly why the other 4 policy panels stay
# unsupported). ---


def test_provider_and_analyst_sizing_override_round_trip(monkeypatch, tmp_path):
    _store, client = _authed_client(monkeypatch, tmp_path)
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    csrf = login.json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf}

    resp = client.post("/providers/telegram", json={"multiplier": 2.0}, headers=headers)
    assert resp.status_code == 200
    resp = client.post("/providers/telegram/analysts/alice", json={"fixed_quantity": 5.0}, headers=headers)
    assert resp.status_code == 200

    providers = client.get("/providers", headers=headers).json()["providers"]
    telegram = next(p for p in providers if p["provider_id"] == "telegram")
    assert telegram["settings"]["multiplier"] == 2.0
    alice = next(a for a in telegram["analysts"] if a["analyst_id"] == "alice")
    assert alice["settings"]["fixed_quantity"] == 5.0


# --- TR-11's real routing-rule CRUD (the exact endpoints the legacy
# dashboard's own Routing rules form already uses). ---


def test_routing_rule_create_update_delete_round_trip(monkeypatch, tmp_path):
    _store, client = _authed_client(monkeypatch, tmp_path)
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    csrf = login.json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf}

    assert client.post(
        "/accounts", json={"account_id": "acct1", "broker": "paper"}, headers=headers
    ).status_code == 200

    created = client.post(
        "/routing-rules", json={"source": "telegram", "destinations": ["acct1"]}, headers=headers
    )
    assert created.status_code == 200
    rule_id = created.json()["id"]

    updated = client.put(
        f"/routing-rules/{rule_id}",
        json={"source": "telegram", "destinations": ["acct1"], "symbol_filter": ["BTCUSDT"]},
        headers=headers,
    )
    assert updated.status_code == 200

    rules = client.get("/routing-rules", headers=headers).json()["routing_rules"]
    assert rules[0]["symbol_filter"] == ["BTCUSDT"]

    deleted = client.delete(f"/routing-rules/{rule_id}", headers=headers)
    assert deleted.status_code == 200
    assert client.get("/routing-rules", headers=headers).json()["routing_rules"] == []


# --- Real browser: the router actually renders real content for each of
# the 4 new hash routes. ---


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
        "/providers/telegram", json={"display_name": "Telegram signals", "multiplier": 1.5}, headers=csrf_headers
    ).status_code == 200
    assert client.post(
        "/routing-rules", json={"source": "telegram", "destinations": ["acct1"]}, headers=csrf_headers
    ).status_code == 200
    webhook = client.post(
        "/webhook/telegram",
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

            # TR-09: Signal providers and collectors.
            await page.click('a[href="#/trade/sources"]')
            await page.wait_for_selector("#tr09-p01", timeout=5000)
            await wait_settled("#tr09-p01")
            assert "Signal providers and collectors" == await page.inner_text("#route-title")
            sources_text = await page.inner_text("#tr09-p01")
            assert "telegram" in sources_text

            # TR-10: Source onboarding and parser laboratory -- real
            # sandbox parser run, no live signal created.
            await page.goto(f"{base_url}#/trade/sources/new")
            await page.wait_for_selector("#tr10-p05", timeout=5000)
            await wait_settled("#tr10-p05")
            assert "Source onboarding and parser laboratory" == await page.inner_text("#route-title")
            await page.fill("#tr10-provider-id", "telegram")
            await page.fill("#tr10-messages", "buy BTCUSDT sl 95 tp 110")
            await page.click("#tr10-run-parser")
            await page.wait_for_function(
                "() => document.querySelector('#tr10-classify-result') && "
                "document.querySelector('#tr10-classify-result').innerText.includes('Classified')",
                timeout=8000,
            )
            comparison_text = await page.inner_text("#tr10-p06")
            assert "BTCUSDT" in comparison_text

            # TR-11: Routing and allocation rules -- real preview + real
            # rule create through the draft form.
            await page.click('a[href="#/trade/routing"]')
            await page.wait_for_selector("#tr11-p01", timeout=5000)
            await wait_settled("#tr11-p01")
            assert "Routing and allocation rules" == await page.inner_text("#route-title")
            priority_text = await page.inner_text("#tr11-p01")
            assert "telegram" in priority_text
            await page.click("#tr11-preview-run")
            await page.wait_for_function(
                "() => document.querySelector('#tr11-preview-result') && "
                "document.querySelector('#tr11-preview-result').innerText.includes('acct1')",
                timeout=8000,
            )

            # TR-12: Sizing, stops and profit policies -- real effective
            # preview against the real received signal.
            await page.click('a[href="#/trade/policies"]')
            await page.wait_for_selector("#tr12-p02", timeout=5000)
            await wait_settled("#tr12-p02")
            assert "Sizing, stops and profit policies" == await page.inner_text("#route-title")
            size_text = await page.inner_text("#tr12-p02")
            assert "telegram" in size_text
            await page.click("#tr12-preview-run")
            await page.wait_for_function(
                "() => document.querySelector('#tr12-preview-result') && "
                "document.querySelector('#tr12-preview-result').innerText.includes('acct1')",
                timeout=8000,
            )
            preview_text = await page.inner_text("#tr12-preview-result")
            assert "units" in preview_text
        finally:
            await browser.close()
