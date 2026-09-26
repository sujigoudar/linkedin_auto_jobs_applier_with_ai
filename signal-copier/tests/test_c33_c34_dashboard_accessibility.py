"""C33/C34 (bounded): automated accessibility regression check for the
dashboard, using axe-core against the real running app (reusing
tests/conftest.py's `live_server` fixture, same infrastructure as C14's
XSS regression test).

Honest baseline, not a "zero violations" claim: a fresh run against this
dashboard found four real violations. Two were fixed here directly
(`landmark-one-main`/`region` -- wrapping the app's content in a real
`<main>` landmark, a clean, side-effect-free change) and are asserted
gone below. Two are disclosed, known, NOT fixed in this pass:

- `aria-required-children` (critical, 1 node): Tabulator (the vendored
  `orders-table` library) sets `role="grid"` on its container, but its
  virtualized body rows are plain unrowed divs -- the ARIA grid pattern
  that role implies is never actually complete. Attempting to strip the
  incomplete ARIA roles Tabulator injects was tried and reverted: it
  chased a moving target across Tabulator's own re-renders (stripping
  the container's role orphaned its header's `role="rowgroup"`, and a
  later Tabulator layout pass re-added roles this page's own JS had
  already removed) rather than converging on a stable fix. This is an
  upstream Tabulator limitation, not something safely patchable from
  outside its internal rendering in this pass.
- `color-contrast` (serious, ~29 nodes): the dashboard's current color
  palette doesn't meet WCAG AA contrast thresholds in ~29 places. Fixing
  this properly needs a real palette audit/redesign across the whole
  page, not a targeted one-line change, and is out of scope here.

This test's actual job: catch NEW violations (a real regression) without
demanding these two disclosed, known ones disappear on their own.
"""
from __future__ import annotations

import httpx
import pytest
from axe_playwright_python.async_playwright import Axe
from playwright.async_api import async_playwright

from tests.conftest import resolve_chromium_executable

_CHROMIUM_EXECUTABLE = resolve_chromium_executable()

#: See this module's own docstring for exactly what each of these is and
#: why it isn't fixed in this pass.
_KNOWN_VIOLATION_IDS = {"aria-required-children", "color-contrast"}


@pytest.mark.asyncio
async def test_dashboard_has_no_new_accessibility_violations(live_server):
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
            await page.wait_for_timeout(500)  # let async panel loads (economics chart, orders table) settle

            axe = Axe()
            results = await axe.run(page)
            violation_ids = {v["id"] for v in results.response["violations"]}

            new_violations = violation_ids - _KNOWN_VIOLATION_IDS
            assert not new_violations, (
                f"new accessibility violation(s) introduced: {sorted(new_violations)}\n"
                f"{results.generate_report()}"
            )

            # The two violations this fix actually closed must stay closed.
            assert "landmark-one-main" not in violation_ids
            assert "region" not in violation_ids
        finally:
            await browser.close()
