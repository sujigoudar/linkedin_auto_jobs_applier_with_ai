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
same live-server fixture for a broader, less security-critical check.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from playwright.async_api import async_playwright

_SIGNAL_COPIER_DIR = Path(__file__).resolve().parent.parent


def _resolve_chromium_executable() -> str | None:
    """An explicit override always wins. Otherwise, auto-detect this
    sandbox's own pre-installed, pinned Chromium build outside
    Playwright's managed browser cache (its own `playwright install`
    can't reach the network here) -- a normal CI runner has no such
    directory and gets None, letting Playwright resolve the browser it
    fetched via its own `playwright install chromium` CI step instead
    (see .github/workflows/signal-copier-ci.yml)."""
    override = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if override:
        return override
    pinned_dir = Path("/opt/pw-browsers")
    if pinned_dir.is_dir():
        for candidate in sorted(pinned_dir.glob("chromium-*/chrome-linux/chrome")):
            return str(candidate)
    return None


_CHROMIUM_EXECUTABLE = _resolve_chromium_executable()


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(tmp_path):
    """A real `uvicorn` subprocess serving the real app.main:app, for the
    one kind of check that needs an actual browser against an actual HTTP
    server rather than TestClient/ASGI transport."""
    port = _free_port()
    env = os.environ.copy()
    env.update(
        {
            "OWNER_PASSWORD": "test-owner-pw",
            "SESSION_SECRET": "test-session-secret",
            "WEBHOOK_SHARED_SECRET": "test-webhook-secret",
            "DATABASE_PATH": str(tmp_path / "e2e.db"),
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(_SIGNAL_COPIER_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(75):
            try:
                response = httpx.get(f"{base_url}/health", timeout=1.0)
                if response.status_code in (200, 503):
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.2)
        else:
            proc.terminate()
            raise RuntimeError("live_server did not become reachable in time")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


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
