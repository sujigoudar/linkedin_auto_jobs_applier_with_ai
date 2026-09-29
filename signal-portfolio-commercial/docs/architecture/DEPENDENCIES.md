# Dependencies

## Internal dependency direction

```
app/api/*              (routes)
   │  depends on
   ▼
app/services/*         (business logic)
   │  depends on
   ▼
app/models/*  ────────▶ app/db.py (Base, engine, RLS/append-only DDL, set_tenant_scope)
   │
   ▼
app/config.py           (env-driven settings, no dependents above it)
```

Rules the codebase actually follows (verified by reading, not assumed):

- **`app/api/` never talks to `app/models/` directly** — every route
  goes through a service function, which is what makes
  `app/services/permissions.py`'s allow-list and RLS scoping
  unavoidable rather than optional.
- **`app/models/` never imports `app/services/`** — models are pure
  schema + column-level docstrings; business rules live in services.
- **`app/services/integration_inbox.py` is the one service most other
  services eventually feed from** — it is the sole writer of
  `InboxEvent` rows and (via `app/services/ledger.py`) the sole path by
  which a relay-sourced fact becomes a `LedgerEntry`. `source_coverage.py`
  and `integration_status.py` both read its output; nothing writes to
  `InboxEvent` except it.
- **`app/services/ledger.py` is the one service every P&L/reporting
  service reads through** (`platform_performance.py`,
  `analyst_attribution.py`, `customer_performance_report.py`,
  `business_economics.py` deliberately does NOT read it — subscription
  revenue and trading P&L are kept structurally separate, never
  blended).
- **`app/db.py` is a dependency of everything, and depends on nothing
  in this app except `app/config.py`** — it defines `Base` (every model
  inherits it), the RLS/append-only DDL functions, and
  `set_tenant_scope`.
- **The public catalog (`app/services/public_site.py` and the `PU-*`
  routes) is the only part of the dashboard layer that runs
  unauthenticated** — every other `/ops/*` and `/app/*` route resolves a
  `TenantScope` first.

## External dependency: `signal_platform_contracts`

`requirements.txt`'s own comment: "pure cross-service contract package
(event envelope + identity) — no DB/broker/secret imports." Installed
as an editable local package (`-e ../signal_platform_contracts`,
sibling to this repo) and is the **only** dependency shared between
signal-copier and signal-portfolio-commercial — it defines
`EventEnvelope`, `EventType`, every payload model
(`SourceReceiptPayload`, `ExecutionAppliedPayload`, `FeePayload`,
`RoutingAdmissionOutcomePayload`, `PositionSnapshotPayload`),
`EvidenceClass`, `CONTRACT_SCHEMA_VERSION`, and `compute_payload_hash`.
Neither app can silently drift from the other's understanding of an
envelope's shape because both import the same package.

## External dependencies (`requirements.txt`)

| Package | Why |
|---|---|
| `fastapi`, `uvicorn[standard]>=0.30` | The HTTP framework and real ASGI server. |
| `httpx>=0.27` | `TestClient` (dev) and the real production `fit_simulation_client.py` cross-service call. |
| `slowapi` | Per-IP rate limiting on the one public route that fans out into a cross-service call. |
| `jinja2>=3.1` | Server-rendered dashboard templates. |
| `python-multipart>=0.0.9` | Starlette form parsing for the dashboard's HTML forms. |
| `sqlalchemy>=2.0` | ORM + Core (used directly for RLS/DDL `text()` statements). |
| `psycopg[binary]>=3.1` | The Postgres driver — this app is Postgres-only (RLS, `Numeric`, `ARRAY` types are not portable to SQLite). |
| `pydantic>=2.0`, `pydantic-settings>=2.0` | Request/response models and `app/config.py`'s settings. |
| `pyjwt>=2.8` | JWT issuance/verification. |
| `pwdlib[argon2]>=0.2` | Real Argon2 password hashing (matches signal-copier's own choice). |
| `alembic` | Schema migrations. |
| `pytest`, `pytest-asyncio` | Test runner. |
| `ruff>=0.6` | Lint (CI, dev-only). |
| `mypy>=1.11` | Type checking (CI, dev-only). |

No ORM/driver dependency on SQLite exists anywhere in this service —
deliberately: signal-copier's own SQLite execution store is a
completely separate process/database this app never opens.
