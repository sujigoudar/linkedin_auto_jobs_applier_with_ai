# Venue Qualification Testing

## Overview

Venue qualification tests exercise real broker/exchange integrations
against actual trading venues. These are separate from unit tests and
protocol-level adapter tests (which can run against mocks); venue tests
require real credentials and connectivity to live or sandbox venues.

See `app/qualification.py` for the full specification of the
qualification ladder that these tests help populate.

## The Qualification Ladder

Each route (a specific combination of `adapter_type`, `route_key`,
`asset_class`, `product_type`) progresses through these states in strict
order:

1. **IMPLEMENTED** — The adapter's code exists and has `place_order` (and
   other required methods) — verified by `app/brokers/base.py`'s
   capability introspection, not by tests.

2. **CONFIGURED** — The specific account/venue variant has real config
   (credentials in `accounts.yaml` / `config_accounts`). Can be checked
   manually; no special test needed.

3. **AUTHENTICATED** — A real auth handshake against the actual
   broker/venue API has succeeded (login, token exchange, or webhook
   token validation). This is a one-time manual check; tests that
   exercise real adapters prove it implicitly.

4. **ACCOUNT_ENTITLED** — The account is confirmed entitled for this
   exact asset class/instrument type. Requires genuine
   account/order/position feedback from the broker to verify.
   `CCXTBroker` and others check `has_account_order_position_feedback()`
   before allowing this and higher rungs to be recorded.

5. **PROTOCOL_TESTED** — This repo's own test suite exercises the real
   protocol path for this route. The test that records this state should
   call `place_order()`, `get_order_status()`, and other real protocol
   methods against this adapter.

6. **VENUE_TESTED** — A real, deliberate test against the actual
   venue/sandbox (not a mock) has been performed. Almost always NOT
   achieved in a sandbox/CI environment with no live credentials — that
   is the honest, expected state, not a bug.

7. **RELEASE_APPROVED** — An explicit human/operator sign-off that this
   route is approved for real, live trading. This is a deliberate
   governance step, never recorded automatically by any code.

## Running Venue Tests

### CI / Sandbox (No Credentials)

Venue tests automatically skip when required credentials are absent:

```bash
$ cd signal-copier
$ python -m pytest tests/venue -q -p no:cacheprovider
# Output: all tests skipped, exit 0
# Example:
# tests/venue/__init__.py .....
# 5 skipped in 0.23s
```

No explicit action needed; the skip markers handle it.

### Local Development (With Credentials)

Set up broker credentials in `accounts.yaml` or environment variables
(per your broker's adapter requirements), then run:

```bash
$ cd signal-copier
$ python -m pytest tests/venue -q -p no:cacheprovider -v
# Output: tests run and exercise real venues
```

## Writing a Venue Test

### 1. Check Credentials

Each venue test starts by checking for the presence of required
credentials. Use pytest's `skipif` marker:

```python
import os
import pytest

@pytest.mark.skipif(
    not os.environ.get("ALPACA_API_KEY"),
    reason="ALPACA_API_KEY not set; skipping Alpaca venue tests"
)
def test_alpaca_venue_order_feedback(tmp_path):
    """Alpaca real venue: place order and read back status."""
    from app.brokers.alpaca import AlpacaBroker
    from app.db import SignalStore
    
    store = SignalStore(tmp_path / "test.db")
    # ... test implementation
```

### 2. Exercise Real Protocol Paths

The test must actually call the broker's real methods:

```python
def test_alpaca_venue_order_feedback(tmp_path):
    # Create a real adapter instance
    broker = AlpacaBroker(api_key=..., api_secret=...)
    
    # Exercise the real order path
    order_id = broker.place_order(
        symbol="AAPL",
        quantity=1,
        side="buy",
        order_type="market",
    )
    
    # Read back real status from the venue
    status = broker.get_order_status(order_id)
    assert status.filled_quantity >= 0
    # ... verify real feedback
```

### 3. Record Qualification State (Optional)

If the test is meant to populate the qualification ladder for a
specific route, call `SignalStore.record_route_qualification()` at the
end:

```python
def test_alpaca_venue_order_feedback(tmp_path):
    # ... test implementation above ...
    
    # Record that we achieved PROTOCOL_TESTED for this route
    store.record_route_qualification(
        adapter_type="alpaca",
        route_key="alpaca_live",
        asset_class="equities",
        product_type="stock",
        state=QualificationState.PROTOCOL_TESTED,
    )
```

Note: `VENUE_TESTED` must be recorded manually via the
`POST /qualifications` endpoint, not by any test.

### 4. Cleanup

Like all tests in this suite, venue tests use `tmp_path` for any
database or file state. Brokers/venues are responsible for their own
cleanup (order cancellation, position closure) at the broker level, not
in test code.

## Troubleshooting

### All tests are skipped

This is expected in CI/sandbox. Verify credentials are set:

```bash
env | grep -i alpaca    # or CCXT_*, IBKR_*, etc.
env | grep -i accounts  # Check ACCOUNTS_YAML path
```

### Test fails with "403 Unauthorized"

Credentials are set but invalid/expired. Verify:
- API keys are correct (typos are common)
- API keys have the right permissions (e.g., live vs. sandbox)
- Session/token has not expired

### Broker returns "Account not entitled for asset class"

The account lacks the entitlements for the requested asset class (e.g.,
options trading not enabled). Either:
1. Enable the asset class on the real broker (if testing live)
2. Use a sandbox that permits all asset classes
3. Skip the test for that particular route (adjust `skipif` condition)

## Related Documentation

- `app/qualification.py` — the qualification model and ladder definition
- `app/db.py` → `SignalStore.record_route_qualification()` — the write path
- `app/main.py` → `POST /qualifications` — the HTTP API for manual qualification
- `app/brokers/base.py` → `has_account_order_position_feedback()` — why
  certain brokers cannot reach `ACCOUNT_ENTITLED` and higher rungs
