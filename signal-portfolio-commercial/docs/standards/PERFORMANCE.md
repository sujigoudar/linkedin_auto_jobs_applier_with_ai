# Performance Standards

This service documents real, deliberate performance/capacity constraints
inline, next to the code that enforces them, rather than in a separate
tuning guide that drifts from reality. This file collects and cross-
references those real constraints; it does not invent new ones.

## 1. Public fit-simulator rate limit (`app/rate_limit.py`)

`POST /portfolios/{slug}/fit-simulation` -- the one public,
unauthenticated route that does real off-box work per request (a signed
call to `signal-copier`) -- is rate-limited at **10 requests/minute per
client IP** (`PUBLIC_FIT_SIM_RATE_LIMIT = "10/minute"`), using `slowapi`'s
default in-memory fixed-window store keyed by `get_remote_address`.

Documented reasoning, verbatim from the module docstring:

> "The limit is a defensive ceiling against abuse/flooding of the one
> route that fans out into a cross-service call, not a constraint on
> legitimate browsing -- a real prospect trying a few account sizes on
> one portfolio page stays far under it."

Documented limitation that must be respected before this is ever
deployed behind more than one process:

> "A deployment running multiple processes behind a shared load balancer
> would need a shared backend (slowapi's `storage_uri`, e.g. Redis) --
> not implemented here since nothing in this project runs that way yet."

If a future deployment does move to multiple processes/replicas behind a
shared load balancer, this in-memory limiter must be swapped for a shared
backend *before* that deployment ships -- otherwise each process enforces
its own independent 10/minute, silently multiplying the real ceiling by
the replica count.

The limit is intentionally **lower** than `signal-copier`'s own
`CATALOG_FIT_SIM_RATE_LIMIT` (20/minute), because this service is the
layer that actually sees each individual visitor's real IP --
`signal-copier` only ever sees this service's own egress IP for every
visitor combined, so it cannot enforce a real per-visitor bound itself.

## 2. Cross-service HTTP timeout (`app/services/fit_simulation_client.py`)

The signed POST from this service to `signal-copier`'s
`/catalog/providers/{source}/fit-simulation` endpoint uses a **15-second**
`httpx` timeout. A timeout/unreachable failure is not swallowed silently
-- it produces a real `FitSimulationOutcome(available=False, reason=
FitSimUnavailableReason.SERVICE_UNREACHABLE, ...)` the caller can render
as a real "unavailable" state, never a hang or a generic 500.

## 3. Storage-ceiling pattern (cross-repo, for context)

The sibling `signal-copier` service documents a real storage-ceiling
policy for its own export outbox (`EXPORT_OUTBOX_SIZE_CEILING_BYTES`,
256 MiB default, wired into its `/health` rollup as an informational-only
signal, never an auto-pruning trigger -- see that repo's INT-040 commit).
`signal-portfolio-commercial` has no equivalent unbounded-growth table of
its own yet, but the same shape -- a real, live measurement (never an
estimate), a documented, configurable ceiling, and a health-check signal
rather than silent data loss -- is the pattern to follow if one of this
service's own append-only tables (`ledger_entries`, `audit_events`,
`portfolio_versions`) ever needs the same treatment. Never add code that
prunes, truncates, or discards rows from an append-only table (see
`docs/standards/CODING.md`) as a way to manage its size -- growth is
managed by ceilings/alerting, never by silently deleting history.

## 4. Test-suite cost is treated as a real constraint, not ignored

`tests/conftest.py` uses one Postgres cluster **per test session**
specifically because starting a cluster is expensive, while dropping and
recreating tables **per test function** is cheap -- this scoping choice is
itself a documented performance decision (see `docs/testing/FIXTURES.md`),
not an accident of pytest defaults. Do not add a second, competing
Postgres-cluster fixture at a narrower scope (e.g. per-test) without a
real reason; it would multiply CI time for the entire suite.
