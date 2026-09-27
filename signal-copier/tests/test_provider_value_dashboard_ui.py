"""Real-browser smoke test for the dashboard's new "Signal provider value
& subscriptions" and "Free-provider candidates" panels (app/static/
dashboard.html) -- same real-server/real-Chromium infrastructure as
C14's XSS regression and C33/C34's accessibility check
(tests/conftest.py's `live_server`), because a DOM mock can't catch a
real JS syntax/runtime error the way an actual page load does.
"""
from __future__ import annotations

import httpx
import pytest
from playwright.async_api import async_playwright

from tests.conftest import resolve_chromium_executable

_CHROMIUM_EXECUTABLE = resolve_chromium_executable()


@pytest.mark.asyncio
async def test_subscription_form_round_trip_and_no_console_errors(live_server):
    base_url = live_server
    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200

    # `pageerror` is a genuine uncaught JS exception in this page's own
    # inline <script> -- a real signal a change here would introduce.
    # Response status is checked separately (below) rather than via
    # `console` message text: Chromium logs every failed subresource load
    # (including the browser's own automatic /favicon.ico request, which
    # this app doesn't serve, unrelated to any JS this page runs) as a
    # console error-level entry, so filtering by message text is fragile.
    page_errors: list[str] = []
    failed_responses: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on(
            "response",
            lambda resp: failed_responses.append(f"{resp.status} {resp.url}")
            if resp.status >= 400 and not resp.url.endswith("/favicon.ico")
            else None,
        )
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)
            await page.wait_for_timeout(500)

            # Both new panels rendered.
            await page.wait_for_selector("text=Signal provider value & subscriptions")
            await page.wait_for_selector("text=Free-provider candidates")
            await page.wait_for_selector("#provider-subscriptions-table")
            await page.wait_for_selector("#provider-value-table")
            await page.wait_for_selector("#provider-candidates-table")

            # Add a subscription through the real form.
            form = page.locator("#provider-subscription-form")
            await form.locator("[name=provider_id]").fill("dashboard_test_provider")
            await form.locator("[name=display_name]").fill("Dashboard Test Provider")
            await form.locator("[name=cost_amount]").fill("25")
            await form.locator("button[type=submit]").click()
            await page.wait_for_timeout(300)

            table_text = await page.locator("#provider-subscriptions-table").inner_text()
            assert "dashboard_test_provider" in table_text
            assert "25" in table_text

            # Delete it via the real button, confirming the browser's
            # confirm() dialog (dashboard.html gates every delete on one).
            page.once("dialog", lambda dialog: dialog.accept())
            await page.locator("#provider-subscriptions-table button.danger", has_text="Delete").first.click()
            await page.wait_for_timeout(300)

            table_text_after = await page.locator("#provider-subscriptions-table").inner_text()
            assert "dashboard_test_provider" not in table_text_after

            # Provider-value search filter form doesn't error even with no data.
            await page.fill("#provider-value-filter-form [name=source]", "nonexistent")
            await page.click("#provider-value-filter-form button[type=submit]")
            await page.wait_for_timeout(300)
            value_text = await page.locator("#provider-value-table").inner_text()
            assert "No confirmed-fill data" in value_text
        finally:
            await browser.close()

    assert not page_errors, f"unexpected uncaught JS exception(s): {page_errors}"
    assert not failed_responses, f"unexpected failed HTTP response(s): {failed_responses}"
