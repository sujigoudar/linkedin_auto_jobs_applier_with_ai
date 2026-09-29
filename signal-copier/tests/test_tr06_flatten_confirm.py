"""TR-06 (Orders, fills and commands) "Emergency: Flatten account" --
real, browser-driven coverage for Components.confirmAction (app/static/
components/action-confirm.js), the shared preview -> impact ->
confirmation -> durable-operation-status gate the 2026-09 design review
asked for ("Financial controls look too similar to ordinary forms...
Emergency actions must remain accessible but difficult to trigger
accidentally").

Static-asset/TestClient checks first, then two real-browser
(Playwright) tests against the live app:

  1. The full happy path: seed a real open position via the real
     webhook -> engine -> paper-broker pipeline, open TR-06, click
     "Flatten account...", and drive preview -> impact -> confirm gate
     -> durable result end to end, asserting the real
     POST /accounts/{id}/flatten fired exactly once and the durable
     result panel shows the real response (never auto-dismissed).

  2. LOAD-BEARING regression for the confirmation gate itself: the
     mutating request must not fire before the operator types the exact
     confirm word. This is intercepted at the network layer (Playwright
     route interception), not just asserted against DOM state, so a
     regression that fires the request through some other path (not
     just one that forgets to disable a button) is still caught. See
     this test module's own report for the verification performed
     against a deliberately broken copy of action-confirm.js before
     committing.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

import app.main as main_module
from tests.conftest import resolve_chromium_executable

_CHROMIUM_EXECUTABLE = resolve_chromium_executable()


def test_action_confirm_component_is_served():
    client = TestClient(main_module.app)
    response = client.get("/static/components/action-confirm.js")
    assert response.status_code == 200
    assert len(response.content) > 500
    assert "confirmAction" in response.text


def test_dashboard_html_references_action_confirm_script():
    client = TestClient(main_module.app)
    html = client.get("/").text
    assert "/static/components/action-confirm.js" in html
    # Loaded alongside the other shared components, before the view scripts
    # that use it (tr06.js) -- same ordering convention as router.js's own
    # module docstring documents for every trNN.js.
    assert html.index("/static/components/action-confirm.js") < html.index("/static/views/tr06.js")


async def _seed_account_and_open_position(client: httpx.Client, csrf_headers: dict) -> None:
    assert client.post(
        "/accounts", json={"account_id": "flat1", "broker": "paper"}, headers=csrf_headers
    ).status_code == 200
    assert client.post(
        "/routing-rules", json={"source": "tradingview", "destinations": ["flat1"]}, headers=csrf_headers
    ).status_code == 200
    webhook = client.post(
        "/webhook/tradingview",
        json={"symbol": "AAPL", "side": "buy", "quantity": 10.0},
        headers={"X-Webhook-Secret": "test-webhook-secret"},
    )
    assert webhook.status_code == 200


@pytest.mark.asyncio
async def test_flatten_account_full_preview_impact_confirm_result_flow(live_server, tmp_path):
    base_url = live_server
    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    await _seed_account_and_open_position(client, csrf_headers)

    flatten_calls = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:

            async def on_request(request):
                if request.method == "POST" and request.url.endswith("/accounts/flat1/flatten"):
                    flatten_calls.append(request.url)

            page.on("request", on_request)

            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)

            await page.goto(f"{base_url}#/trade/orders")
            await page.wait_for_selector("#tr06-flatten-btn", timeout=8000)

            screenshot_dir = tmp_path / "screenshots"
            screenshot_dir.mkdir()

            await page.select_option("#tr06-flatten-account", "flat1")
            await page.click("#tr06-flatten-btn")

            # --- Stage 1/2: preview + impact, built from the real GET
            # /positions read (never a client-side guess). ---
            await page.wait_for_selector(".action-confirm-modal", timeout=5000)
            await page.wait_for_function(
                "() => { const m = document.querySelector('.action-confirm-modal'); "
                "return !!m && !m.innerText.includes('Loading preview'); }",
                timeout=8000,
            )
            preview_text = await page.inner_text(".action-confirm-modal")
            assert "AAPL" in preview_text
            assert "Fill price and slippage cannot be previewed" in preview_text
            await page.screenshot(path=str(screenshot_dir / "1-preview-impact.png"))

            # The mutating request must NOT have fired just from opening the
            # preview.
            assert flatten_calls == []

            # --- Stage 3: explicit confirmation gate. The confirm button
            # starts disabled; clicking it while disabled must not submit. ---
            confirm_btn = page.locator("[data-ac-confirm]")
            assert await confirm_btn.is_disabled()
            await confirm_btn.click(force=True)  # even a forced click on a disabled button does nothing
            await page.wait_for_timeout(200)
            assert flatten_calls == []

            await page.fill("#ac-confirm-input", "wrong-account-id")
            assert await confirm_btn.is_disabled()
            await page.screenshot(path=str(screenshot_dir / "2-confirm-gate-wrong-text.png"))

            await page.fill("#ac-confirm-input", "flat1")
            assert not await confirm_btn.is_disabled()
            await page.screenshot(path=str(screenshot_dir / "3-confirm-gate-armed.png"))

            await confirm_btn.click()

            # --- Stage 4: durable result panel, real response. ---
            await page.wait_for_function(
                "() => { const m = document.querySelector('.action-confirm-modal'); "
                "return !!m && m.innerText.includes('Completed'); }",
                timeout=8000,
            )
            result_text = await page.inner_text(".action-confirm-modal")
            assert "AAPL" in result_text
            assert "filled" in result_text
            await page.screenshot(path=str(screenshot_dir / "4-durable-result.png"))

            assert flatten_calls == ["".join([base_url, "/accounts/flat1/flatten"])]

            # Durable: waiting does not auto-dismiss it.
            await page.wait_for_timeout(500)
            assert await page.locator(".action-confirm-modal").count() == 1

            # Only the explicit Dismiss button closes it.
            await page.click("[data-ac-dismiss]")
            await page.wait_for_selector(".action-confirm-backdrop", state="detached", timeout=3000)

            for shot in sorted(screenshot_dir.iterdir()):
                assert shot.stat().st_size > 0, shot
        finally:
            await browser.close()

    # The real backend actually flattened the position -- not just a UI
    # illusion.
    positions = client.get("/positions", headers=csrf_headers).json()["positions"]
    assert not any(p["account_id"] == "flat1" and p["net_quantity"] != 0 for p in positions)


@pytest.mark.asyncio
async def test_confirm_gate_blocks_the_mutating_request_until_exact_text_is_typed(live_server):
    """LOAD-BEARING: this is the regression test the confirmation gate
    itself must be verified against. Before committing, this exact test
    was run against a deliberately broken copy of action-confirm.js (the
    `input.value !== opts.confirmWord` checks removed, so the confirm
    button enabled/fired immediately) and confirmed to FAIL; the real
    file was then restored and this test re-confirmed to PASS. See this
    task's report for that verification."""
    base_url = live_server
    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    await _seed_account_and_open_position(client, csrf_headers)

    flatten_calls = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            async def on_request(request):
                if request.method == "POST" and request.url.endswith("/accounts/flat1/flatten"):
                    flatten_calls.append(request.url)

            page.on("request", on_request)

            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)
            await page.goto(f"{base_url}#/trade/orders")
            await page.wait_for_selector("#tr06-flatten-btn", timeout=8000)

            await page.select_option("#tr06-flatten-account", "flat1")
            await page.click("#tr06-flatten-btn")
            await page.wait_for_selector("[data-ac-confirm]", timeout=5000)
            await page.wait_for_function(
                "() => { const b = document.querySelector('[data-ac-confirm]'); return !!b && b.disabled; }",
                timeout=5000,
            )

            # Never typed the confirm word at all -- click the (disabled)
            # button several times, then wait, then close the modal.
            confirm_btn = page.locator("[data-ac-confirm]")
            for _ in range(3):
                await confirm_btn.click(force=True)
            await page.wait_for_timeout(300)

            assert flatten_calls == [], (
                "the mutating POST /accounts/flat1/flatten fired without the "
                "operator ever typing the exact confirm word -- the "
                "confirmation gate did not hold"
            )
        finally:
            await browser.close()
