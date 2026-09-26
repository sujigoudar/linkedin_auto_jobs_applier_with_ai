"""C14 (bounded): regression coverage for a real, confirmed stored-XSS
vulnerability in app/static/dashboard.html's `escapeAttr` helper.

Every dashboard row that embeds a value inside `onclick="fn('...')"` (an
attacker-reachable `symbol`, among others -- app/sources/webhook.py's
JSON path applies no character-class validation to it at all) used
`escapeAttr`, which only escaped a single quote. A value containing a
double quote could close the `onclick` attribute early and inject a new
HTML attribute (e.g. `onmouseover="..."`) that the browser then executes.

Confirmed exploitable in a real headless Chromium browser before the fix
(hovering the resulting "Exit now" button fired injected JS), and
confirmed neutralized after -- this test automates exactly that
reproduction against the real running app, not a mock of the page.

This is the one dashboard security check worth running against a real
browser; C33/C34 (Playwright + axe-core accessibility in CI) reuses the
same live-server fixture (see tests/conftest.py) for a broader, less
security-critical check.
"""
from __future__ import annotations

import httpx
import pytest
from playwright.async_api import async_playwright

from tests.conftest import resolve_chromium_executable

_CHROMIUM_EXECUTABLE = resolve_chromium_executable()


@pytest.mark.asyncio
async def test_attacker_controlled_symbol_cannot_break_out_of_the_onclick_attribute(live_server):
    base_url = live_server

    # Set up an account/routing rule the same way an operator would, then
    # ingest a signal whose `symbol` is exactly the kind of value
    # app/sources/webhook.py's JSON path never validates the character
    # set of -- this is real attacker-reachable input, not a synthetic
    # DOM string.
    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}

    assert client.post("/accounts", json={"account_id": "acct1", "broker": "paper"}, headers=csrf_headers).status_code == 200
    assert (
        client.post(
            "/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]}, headers=csrf_headers
        ).status_code
        == 200
    )

    malicious_symbol = 'XSS" onmouseover="window.__xss_fired=true" data-x="'
    webhook = client.post(
        "/webhook/tradingview",
        json={"symbol": malicious_symbol, "side": "buy", "quantity": 1.0},
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
            await page.wait_for_selector("#positions-table button.danger", timeout=5000)

            button = await page.query_selector("#positions-table button.danger")
            assert button is not None
            box = await button.bounding_box()
            await page.mouse.move(box["x"] + 5, box["y"] + 5)
            await page.wait_for_timeout(200)

            xss_fired = await page.evaluate("window.__xss_fired === true")
            assert xss_fired is False, "attacker-controlled symbol broke out of the onclick attribute"

            # And the legitimate functionality must still round-trip the
            # exact original value as one JS string argument, proving
            # this isn't just "stop rendering the value at all".
            onclick_attr = await button.get_attribute("onclick")
            assert malicious_symbol in onclick_attr
        finally:
            await browser.close()
