"""ALLOC-06: the TR-11 routing console exposes delivery mode and preserves it
on edit, driven in a real browser against the real app (not a static page).

Regression this guards: editing an existing `replicate` rule from the UI must
not silently flip it to the API default (`single`), and vice versa.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from playwright.async_api import async_playwright

from tests.conftest import resolve_chromium_executable

_CHROMIUM_EXECUTABLE = resolve_chromium_executable()


async def _saved_rule(client: httpx.Client, rule_id: int, predicate) -> dict:
    """The panel reloads itself after Save (wiping its own "Saved" note), so
    assert on the persisted rule, the real outcome, not on transient text."""
    for _ in range(50):
        rules = {r["id"]: r for r in client.get("/routing-rules").json()["routing_rules"]}
        if predicate(rules[rule_id]):
            return rules[rule_id]
        await asyncio.sleep(0.2)
    return rules[rule_id]


@pytest.mark.asyncio
async def test_tr11_shows_delivery_mode_and_edit_round_trips(live_server):
    base_url = live_server
    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    csrf = {"X-CSRF-Token": login.json()["csrf_token"]}
    for account_id in ("a1", "a2", "a3"):
        assert client.post("/accounts", json={"account_id": account_id, "broker": "paper"}, headers=csrf).status_code == 200
    r1 = client.post(
        "/routing-rules", json={"source": "alpha", "destinations": ["a1", "a2"], "delivery_mode": "replicate"}, headers=csrf
    )
    r2 = client.post("/routing-rules", json={"source": "beta", "destinations": ["a2", "a3"]}, headers=csrf)
    assert r1.status_code == 200 and r2.status_code == 200
    rule_alpha = r1.json()["id"]

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)
            await page.goto(f"{base_url}/#/trade/routing")
            await page.wait_for_selector("#tr11-draft-mode", timeout=10000)

            text = await page.inner_text("#tr11-p01")
            assert "replicate: every destination gets a copy" in text
            assert "single: ONE account selected" in text

            # selecting the replicate rule loads its mode (not the default)
            await page.select_option("#tr11-draft-rule", str(rule_alpha))
            assert await page.input_value("#tr11-draft-mode") == "replicate"

            # switch to single; first Save click only runs the preview gate
            await page.select_option("#tr11-draft-mode", "single")
            await page.click("#tr11-draft-save")
            await page.wait_for_selector("#tr11-draft-result :text('click \"Save draft rule\" again')", timeout=10000)
            still = {r["id"]: r["delivery_mode"] for r in client.get("/routing-rules").json()["routing_rules"]}
            assert still[rule_alpha] == "replicate"  # preview must not have applied anything

            await page.click("#tr11-draft-save")
            saved = await _saved_rule(client, rule_alpha, lambda r: r["delivery_mode"] == "single")
            assert saved["delivery_mode"] == "single"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_tr11_editing_other_fields_does_not_change_replicate_mode(live_server):
    base_url = live_server
    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    csrf = {"X-CSRF-Token": login.json()["csrf_token"]}
    for account_id in ("a1", "a2"):
        client.post("/accounts", json={"account_id": account_id, "broker": "paper"}, headers=csrf)
    rule_id = client.post(
        "/routing-rules", json={"source": "alpha", "destinations": ["a1"], "delivery_mode": "replicate"}, headers=csrf
    ).json()["id"]

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)
            await page.goto(f"{base_url}/#/trade/routing")
            await page.wait_for_selector("#tr11-draft-mode", timeout=10000)
            await page.select_option("#tr11-draft-rule", str(rule_id))
            await page.fill("#tr11-draft-destinations", "a1,a2")
            await page.click("#tr11-draft-save")
            await page.wait_for_selector("#tr11-draft-result :text('click \"Save draft rule\" again')", timeout=10000)
            await page.click("#tr11-draft-save")
            saved = await _saved_rule(client, rule_id, lambda r: r["destinations"] == ["a1", "a2"])
            assert saved["destinations"] == ["a1", "a2"]
            assert saved["delivery_mode"] == "replicate"
        finally:
            await browser.close()
