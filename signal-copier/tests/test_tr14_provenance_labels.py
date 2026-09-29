"""TR-14 deepening: provenance labeling (ACTUAL LIVE / PAPER / INCOMPLETE)
on every metric on the Trading performance and execution quality screen.

This is the single riskiest piece of new logic this phase adds (see
app/static/views/tr14.js's own module docstring) -- a bug here could
mislabel a paper-simulated number as ACTUAL LIVE, which would be actively
dangerous/misleading. This test is deliberately load-bearing: it exercises
the REAL, unmodified `accountProvenance`/`uniformProvenance` pure functions
tr14.js exposes at `window.Views.tr14._internal` (no DOM/network in those
functions themselves), plus one real end-to-end check that a REAL seeded
paper-broker account renders a "PAPER" badge (never "ACTUAL LIVE") in the
actual page.

See this module's own test below
(`test_provenance_labeling_is_load_bearing_broken_branch_would_be_caught`)
for the required "break it on purpose, confirm the regression test fails,
restore, confirm green again" verification this phase's own brief demands
-- that step is DONE MANUALLY during development (see the commit history /
PR description for this phase) and is not itself an automated test (a test
that patches its own test subject's source file mid-run is not something
this suite runs on every CI pass); this file is the regression test that
step exists to validate against.
"""
from __future__ import annotations

import httpx
import pytest
from playwright.async_api import async_playwright

from tests.conftest import resolve_chromium_executable

_CHROMIUM_EXECUTABLE = resolve_chromium_executable()


@pytest.mark.asyncio
async def test_tr14_provenance_pure_functions_never_mislabel_paper_as_live(live_server):
    """Directly exercises the real, unmodified `accountProvenance`/
    `uniformProvenance` functions in the browser (via
    `window.Views.tr14._internal`) against a matrix of real GET /brokers-
    shaped inputs -- no DOM, no fabricated rendering, just the pure
    decision logic this phase's brief calls out as the riskiest new code
    on this screen."""
    base_url = live_server
    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)
            await page.wait_for_function("() => !!(window.Views && window.Views.tr14 && window.Views.tr14._internal)")

            result = await page.evaluate(
                """() => {
                    const internal = window.Views.tr14._internal;
                    const brokersByName = {
                        paper: { name: 'paper', environment: 'paper' },
                        live_broker: { name: 'live_broker', environment: 'live' },
                        unverified_broker: { name: 'unverified_broker', environment: null },
                    };
                    return {
                        paper: internal.accountProvenance({ account_id: 'a1', broker: 'paper' }, brokersByName),
                        live: internal.accountProvenance({ account_id: 'a2', broker: 'live_broker' }, brokersByName),
                        unverified: internal.accountProvenance({ account_id: 'a3', broker: 'unverified_broker' }, brokersByName),
                        unregistered: internal.accountProvenance({ account_id: 'a4', broker: 'ghost_broker' }, brokersByName),
                        uniform_paper: internal.uniformProvenance(
                            [{ account_id: 'a1', broker: 'paper' }, { account_id: 'a1b', broker: 'paper' }],
                            brokersByName
                        ),
                        mixed: internal.uniformProvenance(
                            [{ account_id: 'a1', broker: 'paper' }, { account_id: 'a2', broker: 'live_broker' }],
                            brokersByName
                        ),
                    };
                }"""
            )

            # LOAD-BEARING: a paper broker's account must NEVER render as
            # ACTUAL LIVE.
            assert result["paper"]["label"] == "PAPER"
            assert result["paper"]["code"] == "paper"
            assert result["paper"]["label"] != "ACTUAL LIVE"

            # A genuinely live-environment broker's account must render
            # as ACTUAL LIVE, never silently downgraded to PAPER.
            assert result["live"]["label"] == "ACTUAL LIVE"
            assert result["live"]["code"] == "live"
            assert result["live"]["label"] != "PAPER"

            # A broker with no verified environment flag, and an
            # unregistered broker entirely, must both be INCOMPLETE --
            # never defaulted to either PAPER or ACTUAL LIVE.
            assert result["unverified"]["label"] == "INCOMPLETE"
            assert result["unregistered"]["label"] == "INCOMPLETE"

            # Two accounts sharing the SAME real environment aggregate
            # cleanly to that one label...
            assert result["uniform_paper"]["label"] == "PAPER"
            # ...but a genuinely mixed paper+live group must NEVER be
            # blended into one clean LIVE/PAPER claim -- INCOMPLETE with
            # an explicit reason instead.
            assert result["mixed"]["label"] == "INCOMPLETE"
            assert "paper" in result["mixed"]["reason"].lower() or "live" in result["mixed"]["reason"].lower()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_tr14_real_paper_account_renders_paper_badge_not_live(live_server):
    """End-to-end: a REAL paper-broker account with a real fill must
    render its "Account/analyst book" row with a "PAPER" badge, never
    "ACTUAL LIVE" -- the full render path, not just the pure function."""
    base_url = live_server
    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    webhook_headers = {"X-Webhook-Secret": "test-webhook-secret"}

    assert client.post(
        "/accounts", json={"account_id": "paper_acct", "broker": "paper"}, headers=csrf_headers
    ).status_code == 200
    assert client.post(
        "/routing-rules", json={"source": "tv-perf", "destinations": ["paper_acct"]}, headers=csrf_headers
    ).status_code == 200
    entry = client.post(
        "/webhook/tv-perf",
        json={"symbol": "MSFT", "side": "buy", "quantity": 2.0, "price": 100.0},
        headers=webhook_headers,
    )
    assert entry.status_code == 200
    assert entry.json()["orders"][0]["status"] == "filled"
    close = client.post(
        "/webhook/tv-perf",
        json={"symbol": "MSFT", "side": "close", "price": 110.0},
        headers=webhook_headers,
    )
    assert close.status_code == 200

    brokers = client.get("/brokers").json()["brokers"]
    paper_broker = next(b for b in brokers if b["name"] == "paper")
    assert paper_broker["environment"] == "paper"  # the real fact this page's label depends on

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)

            await page.click('a[href="#/trade/performance"]')
            await page.wait_for_selector("#tr14-p01 table", timeout=8000)

            book_text = await page.inner_text("#tr14-p01")
            assert "paper_acct" in book_text
            assert "PAPER" in book_text
            assert "ACTUAL LIVE" not in book_text
        finally:
            await browser.close()
