# End-to-End Test Patterns

This codebase has two real end-to-end fixtures, both defined around a
genuine running `app.main:app` — never a mocked HTTP layer or a DOM
mock — plus the specific pattern used when even a real subprocess server
can't reach into the running app's own live objects.

## Pattern 1: `live_server` — real uvicorn subprocess (`tests/conftest.py`)

```python
@pytest.fixture
def live_server(tmp_path):
    """A real `uvicorn` subprocess serving the real app.main:app."""
```

What it actually does:

- Picks a free local port, spawns `python -m uvicorn app.main:app` as a
  real subprocess against a fresh `tmp_path`-scoped SQLite file
  (`DATABASE_PATH`), with `OWNER_PASSWORD` / `SESSION_SECRET` /
  `WEBHOOK_SHARED_SECRET` set to known test values and
  `LEGACY_DASHBOARD_ENABLED=true` (kept on because at least one
  real-browser test still exercises the legacy dashboard's DOM directly).
- Polls `GET /health` (accepting `200` or `503` — a startup that reports
  itself unhealthy still means the process is up and answering) up to 75
  times at 200ms intervals before giving up.
- Yields the base URL; on teardown, terminates the subprocess (falls back
  to `kill()` after a 5s grace period).

Used by the two tests that genuinely need a real browser: **C14**
(`tests/test_c14_dashboard_xss_prevention.py`) and **C33/C34**
(`tests/test_c33_c34_dashboard_accessibility.py`, Playwright + axe-core).
Chromium resolution (`resolve_chromium_executable()`, same file) prefers
a pinned local build at `/opt/pw-browsers/` in this sandbox, falling back
to whatever `playwright install --with-deps chromium` fetched in CI (see
`.github/workflows/signal-copier-ci.yml`).

### What a genuine end-to-end scenario looks like here

`tests/test_c14_dashboard_xss_prevention.py` is the canonical example —
read it as the template for a new end-to-end test:

1. **Log in for real** (`POST /auth/login`) and capture the real CSRF
   token from the response, not a bypassed/mocked auth path.
2. **Configure state through the real API** (`POST /accounts`,
   `POST /routing-rules`) — no direct DB seeding.
3. **Send a real webhook signal** (`POST /webhook/tradingview`, with the
   real `X-Webhook-Secret` header) carrying attacker-controlled input
   (`symbol` with no character-class validation applied anywhere in
   `app/sources/webhook.py`'s JSON path) — this is genuinely
   attacker-reachable input flowing through the real engine, real
   routing, and a real (paper) broker fill, exactly the "webhook signal →
   real engine → real paper broker fill → real position tracked" chain.
4. **Drive the real rendered dashboard in a real headless Chromium tab**
   (Playwright) — log in through the real login form, wait for the real
   `#positions-table` row the signal produced, and assert on the actual
   rendered DOM/attribute value, not a snapshot or a component mock.
5. Assert **both** that the exploit doesn't fire *and* that legitimate
   functionality still round-trips correctly (the malicious string must
   still appear, correctly escaped, as one JS string argument — "proving
   this isn't just 'stop rendering the value at all'"). An end-to-end
   security test that trivially passes by breaking the feature is not
   trusted here — see `docs/standards/TESTING.md`'s load-bearing
   verification standard.

## Pattern 2: `live_server_inprocess` — real uvicorn, same process, background thread

Some newer real-browser tests (`tests/test_tr03_mae_mfe_chart.py`,
`tests/test_tr09_provider_scorecards_correlation.py`) need something the
subprocess fixture structurally cannot give them: **a live handle on the
exact same in-memory objects** (`lifecycle_manager`, `engine`) the
running server's HTTP handlers are reading from, so the test can inject a
real event (e.g. a price tick) directly onto them.

Why this is needed at all — read `test_tr03_mae_mfe_chart.py`'s own
docstring, which is the clearest statement of the constraint: `PaperBroker`
never implements `get_last_price`, so `PriceMonitor` never polls it, and
there is no HTTP route that accepts an external price tick for a managed
lifecycle — "that surface simply doesn't exist." The *only* real price
observation an HTTP-only flow can produce is the entry fill price itself,
which gives a degenerate single-point series. So the test drives the
exact same real production entry point a real price tick arrives through
— `PositionLifecycleManager.on_price_update` — directly, but scheduled
onto the **real server's own running event loop** via
`asyncio.run_coroutine_threadsafe`, so it genuinely races the real
HTTP-driven flow the way a real concurrent price feed would, instead of
mutating state from an unrelated thread/loop out of band.

`live_server_inprocess` (defined per-file, not in shared `conftest.py`)
runs uvicorn in a background thread with its own event loop, and — this
is the fixture's other job — **rewires the module-level singletons** on
`app.main` to a fresh, test-only `SignalStore`/`PositionLifecycleManager`
pair before starting the server, so this test's data can't leak into or
out of any other test sharing the same imported `app.main` module. See
`docs/testing/FIXTURES.md` for why this rewiring step is required and
what happens if it's skipped.

Use `live_server` (the subprocess fixture) by default for any real-browser
test. Reach for `live_server_inprocess` only when the scenario genuinely
requires injecting state directly onto the running app's live in-memory
objects that no real HTTP route can produce — it is the exception, not
the default, because it carries the module-singleton rewiring burden
described in `docs/testing/FIXTURES.md`.
