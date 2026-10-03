# Audit F — Operations / Configuration / Security / Console UI (signal-copier)

Scope: read-only code audit of `signal-copier` (FastAPI + SQLite + static console). All paths relative to
`/home/user/linkedin_auto_jobs_applier_with_ai/signal-copier/`. Line numbers are from the working tree on
2026-10-02. Live evidence was taken read-only from the repo's `signal_copier.db` (python sqlite, `mode=ro`).

Severity: P0 = can create unintended exposure/loss or let an unauthorized actor trade; P1 = silently wrong;
P2 = missing capability; P3 = doc/test/UX. Classification: semantic-miss | disconnected | mock-only-tested |
silently-unsupported | missing | ok.

## Summary table

| ID | Sev | Class | Title |
|---|---|---|---|
| F-01 | P0 | semantic-miss | `managed_lifecycle` flip (account flag, provider/analyst override, or override deletion) mid-position routes CLOSE through the wrong branch: orphaned resting stop (managed→plain) or stranded position (plain→managed); UI can trigger it without warning |
| F-02 | P1 | disconnected / mock-only-tested | Daily-loss / min-equity / margin circuit breakers are not loadable from DB or YAML; the global default is a fail-closed entry kill-switch, the min-equity default is dead config, `daily_pnl`/`margin_call_alerts` are dead tables |
| F-03 | P1 | silently-unsupported | "Pause new entries" (TR-07) and the legacy account form POST a partial `AccountRequest`; the upsert clobbers `risk_percent_of_equity`, `qualification_level`, `exclusive_writer_qualified` → can block all plain CLOSEs on that account |
| F-04 | P1 | silently-unsupported | Stamp-once alembic bookkeeping + bootstrap-as-source-of-truth → every upgraded DB reports `schema_version != schema_head`; TR-16 shows "Mismatch", blocks runbook gate 3, and tells the owner to run `alembic upgrade head`, which the policy forbids and which fails on this DB; 0036's `config_accounts` columns exist only on fresh DBs |
| F-05 | P1 | disconnected | Command-ledger `UNKNOWN_AMBIGUOUS` / `PENDING_SUBMISSION` rows have no API, no UI, no reconciler pass; `promote_cli`'s unresolved-ledger check queries a nonexistent column and always prints "could not query" |
| F-06 | P1 | missing | Nothing notifies a human for protection deficit / halt / unknown submission / skipped allocation: Cloudflare worker only watches `/health` (db, price monitor, reconciler, lease); phone-escalation is an inbound design stub; attention queue is UI-only |
| F-07 | P1 | semantic-miss | Routing-rule DELETE/PUT has no exposure guard; CLOSE routes by *current* rules, so deleting/narrowing a rule silently strands provider exits; TR-11 only `confirm()`s deletes and the position-impact endpoint ignores `delivery_mode` and deletes |
| F-08 | P1 | silently-unsupported | `/health.database_ok` is a read probe; disk-full/read-only volume leaves status "ok" while every write (ledger, lifecycle persist, signals) fails |
| F-09 | P2 | missing | No guard against `--workers > 1`: second worker fences the first (same site_id), fenced worker returns 500 on every signal/close; in-memory allocator/lifecycle/locks/throttles are per-process |
| F-10 | P2 | missing | Allocation intents left `claimed`/`selected` by a crash are resolved only on redelivery; no startup sweep, no UI consumer of `GET /allocation-intents`; `/strategy-budgets` has no UI |
| F-11 | P2 | silently-unsupported | Rate limit + login throttle keyed on `request.client.host`; behind a non-loopback proxy (compose topology) all clients share one bucket → attacker can lock the owner out / 429 the real signal source |
| F-12 | P2 | missing | No market calendar / session / holiday awareness anywhere; "day" figures are UTC-day or rolling 24h; limiter uses naive `date.today()` |
| F-13 | P2 | missing | Backups: Litestream designed but "not yet operated"; no pre-migration DB snapshot; `downgrade()` is `NotImplementedError` for 0001–0035 and the bootstrap re-applies 0036/0037 on next open → no rollback path; config/.env not backed up |
| F-14 | P2 | missing | No TR screen can set `enabled=true` on an account (TR-08 always saves `false`, TR-07 only pauses) and the legacy form is behind `LEGACY_DASHBOARD_ENABLED=false` → activation needs the raw API |
| F-15 | P3 | silently-unsupported | Account `broker` is free text at the API (`POST /accounts`); only TR-08 restricts it to `GET /brokers`; a typo is discovered at signal time as an ERROR row |
| F-16 | P3 | doc/UX | Console claims that contradict the backend: TR-13/TR-06 "no run-now endpoint" (it exists), TR-16 "no automated fencing mechanism"/"trading authority placeholder" (writer lease exists and is reported), TR-14 "no fee column exists" (0036 added one), TR-05 routing preview lists every `single`-mode destination as ROUTED |
| F-17 | P3 | doc | Config/doc drift: `docs/risk/CIRCUIT_BREAKER.md` referenced but absent; `DEFAULT_DAILY_LOSS_LIMIT_PERCENT`/`DEFAULT_MIN_EQUITY_THRESHOLD` absent from `.env.example` and `docs/operations/CONFIGURATION.md`; `docs/database/MIGRATIONS.md` head table stops at 0015 and says `_COLUMN_MIGRATIONS` is frozen while 0036/0037 appended five entries |
| F-18 | P3 | missing | Dependencies unpinned (`>=` only, no lock file), base image floating tag |
| F-19 | ok | ok | Auth/CSRF/cookie/secret-compare/XSS posture is sound (details below); residual: one shared webhook secret for all `/webhook/{source}`, `/auth/logout` has no CSRF, CSP needs `'unsafe-inline'` |

---

## Detailed findings

### F-01 — P0 — semantic-miss — `managed_lifecycle` flip mid-position (account flag or provider/analyst override)

Evidence
- Branch selection is per signal, from the *effective* account: `app/engine.py:675-682` — `account = replace(raw_account, ..., managed_lifecycle=(effective.managed_lifecycle if effective.managed_lifecycle is not None else raw_account.managed_lifecycle))`; then `app/engine.py:880` `if account.managed_lifecycle:` (managed close via lifecycle manager) else `app/engine.py:992-999` plain close via `_resolve_and_submit_plain_close`.
- Plain-close path never consults the lifecycle manager: `app/engine.py:2511-2553` reads `store.get_position`, reconciles against the broker, builds an opposing market order. `grep get_lifecycle(` in engine.py hits only the managed branches (894, 2996, 3082, 3319). `positions` is updated for BOTH plain and managed fills (tr03.js:16-19 cites `record_fill`), so a managed position looks like a plain one to this path.
- Managed close with no lifecycle returns `"no open position to close"`: `app/engine.py:3114-3123`. Manual "Exit now"/"Flatten" use the same flag: `app/engine.py:3310` `if account.managed_lifecycle:`.
- Only `broker` changes are guarded: `app/main.py:2465-2473` (`existing["broker"] != request.broker and _account_has_exposure`). `managed_lifecycle`, `symbol_map`, `management_recipe` changes are not. Provider/analyst `managed_lifecycle` overrides (`ProviderRequest.managed_lifecycle`, `app/main.py:2922-2947`, `2967-2990`) and `DELETE /providers/{id}` (`app/main.py:2948-2966`) have no exposure guard at all.
- UI triggers: TR-12's sizing override form sends `managed_lifecycle: null` on every save (`app/static/views/tr12.js:636-642`) — editing a multiplier on a provider that had `managed_lifecycle=true` as an override silently removes that override.

Scenario (managed→plain): account A is `managed_lifecycle=true`, entry filled, protective stop resting at the broker. Owner edits the account (or saves a provider override) so the effective flag becomes false. Provider sends CLOSE → plain path submits a market close against `positions` (broker readback matches: the position really is open) → position flat, but the lifecycle's stop order is still resting; nothing cancels it (the lifecycle object remains "open" in memory/DB with `owned>0`, `retry_unprotected_positions` keeps it protected). Price touches the stop → the stop fills on a flat book → a new, unintended short/long with no stop. Scenario (plain→managed): an open plain position gets the flag flipped true; provider CLOSE and the owner's "Exit now" both return "no open position to close" → exposure persists silently (the signal is answered 200 with a REJECTED row).

Tests/docs encoding it: none found that assert the cross-branch behaviour; `test_exe10_*` covers broker change only (UNVERIFIED by name; EXE-10 docstring at `app/main.py:2459-2464` scopes itself to `broker`).

Fix: in `create_or_update_account`, `create_or_update_provider/analyst`, `delete_provider`, refuse (409) any change to the effective `managed_lifecycle` (and `symbol_map` entries for symbols with an open position) while `_account_has_exposure`; additionally make `_resolve_and_submit_plain_close` refuse when `lifecycle_manager.get_lifecycle(account, symbol)` is open (fail closed), and make the managed branch fall through to the plain path only when no lifecycle exists *and* the account is not managed.

### F-02 — P1 — disconnected / mock-only-tested — daily-loss / min-equity / margin circuit breakers

Evidence
- Per-account values are never persisted or loaded: `SignalStore.list_config_accounts` selects 12 columns, not `daily_loss_limit_percent`/`min_equity_threshold` (`app/db.py:4293-4300`); `upsert_config_account` never writes them (`4325-4375`); `load_routing_config_from_store` never passes them (`app/routing.py:211-227`); the YAML loader doesn't read them either (`app/routing.py:160-187`) although `app/config.py:415,421` says "Can be overridden per-account in accounts.yaml"; `AccountRequest` has no such fields (`app/main.py:2402-2420`). Columns exist in `SCHEMA` only (`app/db.py:317-319`).
- Engine fallback: `app/engine.py:749` `account.daily_loss_limit_percent or config.DEFAULT_DAILY_LOSS_LIMIT_PERCENT`. `DEFAULT_MIN_EQUITY_THRESHOLD` (`app/config.py:421,566`) is never read: `app/engine.py:821` uses only `account.min_equity_threshold` (always None from DB/YAML) → dead config.
- Limiter depends on store attributes that `SignalStore` does not have: `getattr(self.store, "get_daily_pnl", None)` → reject if missing (`app/daily_loss_limiter.py:38-44`); `getattr(self.store, "_broker_adapters", {})` (`52-56`, `109-113`). `grep` over `app/` finds no `def get_daily_pnl` and no `_broker_adapters` outside the limiter; `tests/test_e09_account_liquidation.py:42,160,291` and `tests/test_integration_e09_commercial_accounting.py:68-282` set `mock_store._broker_adapters = {...}` — the only place they exist. `tests/test_alloc09_loss_limit_fails_closed.py:1-5` already documents the mismatch and pins the fail-closed outcome.
- `daily_pnl` table is never written (grep: only SCHEMA and the limiter's getattr); live DB: 0 rows. `margin_call_detector` is always invoked with `None, None, None` (`app/engine.py:785-791`) → `check_and_persist_margin_call` returns at `app/margin_call_detector.py:54-55` → never fires; `margin_call_alerts` is dead.

Scenario: operator sets `DEFAULT_DAILY_LOSS_LIMIT_PERCENT=5` expecting a breaker → every ENTRY on every account is REJECTED with "Daily loss limit check failed: no daily P&L source" (CLOSE still allowed) — a silent full trading halt that looks like risk control. Operator sets `DEFAULT_MIN_EQUITY_THRESHOLD=10000` → nothing happens.

Fix: either delete the knobs/columns/tables (honest), or implement `SignalStore.get_daily_pnl` from `account_equity_snapshots`, inject `brokers` into `DailyLossLimiter`, add the two columns to `list/upsert_config_account`, `AccountRequest`, both loaders and `_COLUMN_MIGRATIONS`, and read `DEFAULT_MIN_EQUITY_THRESHOLD` in the engine.

### F-03 — P1 — silently-unsupported — partial `POST /accounts` clobbers safety fields

Evidence: TR-07 "Pause new entries" sends only 8 fields (`app/static/views/tr07.js:574-583`); the legacy dashboard form sends 7 (`app/static/dashboard.html:864-872`); TR-08 "Save inactive account" sends 8 (`tr08.js:326-335`). `AccountRequest` defaults the rest (`risk_percent_of_equity=None`, `management_recipe=None`, `qualification_level=None`, `exclusive_writer_qualified=False`, `app/main.py:2402-2420`) and the upsert overwrites every column `ON CONFLICT` (`app/db.py:4347-4355`).

Scenario: plain account on a broker without position readback, operator-set `exclusive_writer_qualified=true` (the only thing that allows its CLOSEs, `app/engine.py:2392-2408`). Owner clicks "Pause new entries" → flag reset to 0 → every subsequent provider CLOSE and dashboard "Exit now" is REJECTED ("cannot reconcile ... not marked exclusive_writer_qualified") until someone re-sets it via the API; `risk_percent_of_equity` sizing is also silently dropped, so re-enabling later trades a different size.

Fix: make `POST /accounts` PATCH-like (sentinel/`exclude_unset` merge with the stored row) or have every UI call round-trip the full `GET /accounts` row.

### F-04 — P1 — silently-unsupported — schema stamping drift; TR-16 "Deployment" check inverted; remediation forbidden/destructive

Evidence
- Bootstrap is the real migration mechanism: `SignalStore.__init__` runs `SCHEMA` + `_COLUMN_MIGRATIONS` on every open (`app/db.py:2019-2043`); `_stamp_alembic_head_if_needed` stamps only if `alembic_version` is absent (`2060-2066`) and never re-stamps. `docs/database/MIGRATIONS.md:107-112`: a SignalStore DB "never needs (and should never be run through) `alembic upgrade head`".
- Live DB (read-only): `alembic_version = 0035`, code head `0037`, yet `allocation_intents`, `strategy_budgets`, `daily_pnl`, `margin_call_alerts`, `config_routing_rules.delivery_mode`, `orders.fee` are all present (bootstrap applied them) and `config_accounts` lacks `daily_loss_limit_percent`/`min_equity_threshold` (SCHEMA-only, not in `_COLUMN_MIGRATIONS`) → fresh DB ≠ upgraded DB.
- Console: `/system/info` returns both (`app/main.py:814-815`); `tr16.js:477` `schemaMatch`, KPI "Deployment: Mismatch" (`503`), Subsystems row remediation "run `alembic upgrade head`" (`633-637`, `661-665`), and runbook gate 3 is disabled until `schemaMatch` (`346,373-375`).
- Running the suggested command on this DB fails: `0016` does a plain `op.add_column("signals","import_batch")` on a column that exists (`alembic/versions/0016_add_signals_import_batch.py:36`); `0036` does `op.create_table("daily_pnl")` on an existing table (`0036:37`) and `batch_op.add_column("fee")` duplicate (`0036:23`). Downgrade is `NotImplementedError` for 0001–0035 (`0035:37`).
- Policy drift: `_COLUMN_MIGRATIONS` is documented as frozen (`MIGRATIONS.md:28-30,160`) but 0036/0037 appended `strategy_key`, `delivery_mode`, `fee`, `fee_currency`, `slippage` (`app/db.py:1867-1868,1999-2001`); `MIGRATIONS.md:127` still says head is 0015.
- Latent: alembic's `margin_call_alerts.resolved` has `default=False` (Python-side) but no `server_default` (`0036:63`) while SCHEMA has `DEFAULT 0`; `persist_margin_call_alert` omits `resolved` in its INSERT (`app/db.py:9114-9116`) → NOT NULL failure on an alembic-provisioned DB (unreachable today because of F-02).

Scenario: every deployment that has lived through ≥1 release shows TR-16 "NOT at head / SCHEMA_MISMATCH" forever; the guided recovery runbook can never pass gate 3; an operator who follows the on-screen remediation gets a hard alembic error mid-incident.

Fix: in `_stamp_alembic_head_if_needed`, re-stamp to head whenever `version_num != alembic_code_head()` (bootstrap already brought the schema there); change TR-16 remediation text to "stamp"; add the two `config_accounts` columns to `_COLUMN_MIGRATIONS`; add `server_default` for `resolved`; update MIGRATIONS.md.

### F-05 — P1 — disconnected — unresolved command-ledger rows are invisible and unrecoverable

Evidence: rows are marked `UNKNOWN_AMBIGUOUS` at `app/engine.py:1127-1136` (crash-window duplicate), `1191-1195` (exception during submit), and in `app/lifecycle/manager.py:1671-1676,1801-1806,1944-1949,1994-1999`. `SignalStore.list_unresolved_command_ledger_entries` (`app/db.py:4089`) is referenced only by tests (`tests/test_p0_2_command_ledger.py`, `test_alloc05/07`, `test_e03`) — no route in `app/main.py` (route listing: none), no consumer in `app/reconciliation.py` (grep `ledger|ambiguous`: none; `reconcile_once` iterates `store.list_pending_orders()` only, `app/reconciliation.py:146`). `app/promote_cli.py:91-93` runs `SELECT COUNT(*) FROM command_ledger WHERE outcome IS NULL OR outcome = 'unresolved'` but the table has `uncertainty_state`/`resolved_at`, no `outcome` (`app/db.py:860-874`) → always hits the `except` at `99-100` ("could not query ... treat as unknown"). UI: TR-06 "Unknown outcome queue" = `orders.status == 'pending'` only (`tr06.js:430-462`); TR-01 "Unknown broker operations" = lifecycle `pending_entry/pending_exit` only (`tr01.js:1207`).

Scenario: process crashes after `broker.place_order` returns but before `save_order_result` → no `orders` row, ledger row stuck → broker holds a position nothing tracks; no screen, no health flag, no alert; `python -m app.promote_cli status` prints "could not query" and the RUNBOOK's "review unresolved commands" step is a no-op.

Fix: fix the promote_cli query to use `list_unresolved_command_ledger_entries`; add `GET /command-ledger/unresolved` and feed TR-01/TR-06/TR-13 attention items; add a reconciler pass that tries `get_order_status` by the ledger's `remote_identifiers` and otherwise keeps the row loudly unresolved in `/health`.

### F-06 — P1 — missing — no human notification path

Evidence: `deploy/cloudflare-heartbeat/worker.js:53-66` alerts only when `/health.status != "ok"`; `/health.status` folds only `database_ok`, `price_monitor_ok`, `reconciler_ok`, `writer_lease_ok` (`app/main.py:740`) — protection gaps, halts, ambiguous ledger rows, skipped allocation intents and stale sources are deliberately informational (`666-719`). `/system/readiness` (which does fail closed on `protection_status == "gap"`, `869-874`) is owner-authenticated and unreachable by the worker. `app/phone_escalation.py` is a *retrieval* design stub (`AdbPhoneControlAdapter` raises `NotImplementedError`, `573-640`; `_resolve_phone_control_adapter` always returns `(None, None)`, `app/main.py:4866-4879`); `app/notification_bridge.py` is inbound-only. `grep -i "notify_owner|alert_owner|page_owner|send_alert"` over engine/lifecycle/reconciliation/writer_lease: nothing. The attention queue (`app/static/components/attention-queue.js`) renders only while a browser tab polls.

Scenario: an entry fills, the stop placement fails (`PROTECTION_FAILED`), the owner is asleep; `/health` stays "ok", the worker never fires, and the only record is a red row on a dashboard nobody is looking at.

Fix: emit an outbound alert (webhook/email/Twilio, reusing the worker's `ALERT_WEBHOOK_URL` shape) from `retry_unprotected_positions` failures, `CloseArbiter.halt`, `UNKNOWN_AMBIGUOUS` ledger writes, `FencedOutError`, and expose an unauthenticated `attention_count` in `/health` for the worker to gate on.

### F-07 — P1 — semantic-miss — rule deletion / narrowing strands CLOSE routing

Evidence: CLOSE destinations come from the *current* rules each time: `app/engine.py:610-611` `self.routing.destinations_for(signal.source, signal.symbol, include_disabled=True)`; no destinations → `not_routed`, `return []` (`640-645`). `DELETE /routing-rules/{id}` and `PUT` have no exposure check (`app/main.py:2538-2551`). TR-11 delete is a bare `confirm()` (`tr11.js:355-365`); the position-impact preview runs only for save, and `routing_rule_position_impact` rebuilds rules without `delivery_mode` (`app/main.py:2865-2878`) so it cannot show the single→replicate impact. Replicate-vs-single for CLOSE is safe (each account exits only what it owns) — confirmed at `610-611`.

Scenario: owner deletes the only rule for source `tv` while `tv`-originated positions are open; the provider's exit arrives → 200 with `orders: []`; positions stay open until the owner notices and uses "Exit now".

Fix: in DELETE/PUT, run the same `destinations_for(..., include_disabled=True)` before/after comparison as `routing_rule_position_impact` and refuse (409) when an open position loses its exit path unless `?force=true`; pass `delivery_mode` through `PositionImpactRequest`.

### F-08 — P1 — silently-unsupported — health cannot see a full/read-only disk

Evidence: `database_ok` = `store.get_position("__healthcheck__", ...)` read (`app/main.py:658-662`); no write probe, no `shutil.disk_usage` (grep: none); `_immediate` retries only `locked/busy` (`app/db.py:4465-4474`). An idle reconciler pass (no pending orders) performs no writes, so `reconciler_ok` keeps advancing (`app/reconciliation.py:101-106,146`).

Scenario: `/app/data` fills. Webhook → `save_signal` raises → 500 (fail closed, fine). But a managed lifecycle that was already mid-flight: `place_order` succeeds, `_persist`/`save_order_result` raise → in-memory state diverges from DB, next restart loses it; `/health` remains "ok", Cloudflare never alerts.

Fix: add a cheap write probe (`INSERT OR REPLACE` into a heartbeat row) and a free-space threshold to `/health`; fold both into `status`.

### F-09 — P2 — missing — multi-worker deployment is unguarded

Evidence: lease acquired at import time (`app/main.py:229-250`); same `site_id` re-acquire bumps the token (`app/db.py:4213-4221`) so worker B fences worker A; `FencedOutError` is uncaught (`app/engine.py:494-498`; only `RateLimitExceeded` has a handler, `app/main.py:566`) → HTTP 500 for every signal/close that lands on A; heartbeat logs CRITICAL every 10 s (`454-481`). Per-process state: `CapitalAllocator` reservations (tr02.js:48-50), `_plain_close_locks` (`app/engine.py:2511`), `_login_failures` (`app/main.py:1138`), slowapi (`app/rate_limit.py:10-16`). Deploy defaults are single-process (`Dockerfile:49`, `deploy/systemd/signal-copier.service` ExecStart) but nothing asserts it.

Fix: refuse startup when `WEB_CONCURRENCY`/`--workers` > 1 (or when a second same-site holder appears within N seconds), and map `FencedOutError` → 503 + `Retry-After`.

### F-10 — P2 — missing — crash-window allocation intents and strategy budgets have no recovery/UI

Evidence: `claim_allocation_intent` returns the existing row; a `selected` intent is honoured only when the same signal is redelivered (`app/engine.py:551-559,624-637`); no startup sweep (`lifespan`, `app/main.py:485-558`). `GET /allocation-intents` (`2584-2590`) and `/strategy-budgets` (`2560-2581`) have no consumer in `app/static/` (grep: none).

Scenario: crash between `bind_allocation_intent` and the ledger write → intent stays `selected` forever; if the provider never redelivers, the owner cannot see that an opportunity was half-allocated.

Fix: on startup mark `selected` intents older than the lease window with no ledger row as `skipped(reason=crash_window)`; add a TR-11 panel over `/allocation-intents?state=claimed|selected` and `/strategy-budgets`.

### F-11 — P2 — silently-unsupported — IP-keyed throttles behind a proxy

Evidence: `_client_key` = `request.client.host` (`app/main.py:1141-1142`), 5 failures / 15 min lockout per key (`1136-1160`); slowapi `get_remote_address` (`app/rate_limit.py:27`). No `--proxy-headers`/`forwarded-allow-ips` anywhere in `deploy/`, `Dockerfile`, `docker-compose.yml` (grep). docker-compose publishes `127.0.0.1:8000` and the docs say to put nginx/Caddy in front; in a container the proxy is not `127.0.0.1`, so uvicorn ignores `X-Forwarded-For` and every client is the proxy IP.

Scenario: anyone who can reach the login page submits 5 wrong passwords → the real owner is locked out for 15 min (the docstring at `1127-1135` says per-IP scoping exists precisely to prevent this). A second webhook sender's burst 429s TradingView.

Fix: document/require `uvicorn --proxy-headers --forwarded-allow-ips=<proxy ip>`; key the login throttle on a trusted forwarded header when configured.

### F-12 — P2 — missing — no market-calendar awareness

Evidence: grep for `holiday|market_calendar|exchange_calendar|trading_hours|is_market_open|zoneinfo|pytz` over `app/`: no hits. All timestamps are tz-aware UTC (`Signal.received_at` default `datetime.now(timezone.utc)`, `app/models.py:136`; ISO strings in DB; naive values coerced to UTC in 8 places). The one naive call is `date.today()` in `app/daily_loss_limiter.py:37` (local date, dead path per F-02). "Today's net P&L" is rolling 24 h (`tr01.js:1251-1268`); daily P&L is UTC-day differenced (`tr14.js:271-283`).

Fix: document "UTC-day/24 h, not exchange session"; add an exchange-calendar gate only if session-aware entries are required.

### F-13 — P2 — missing — backup/rollback story

Evidence: `docs/operations/BACKUPS.md:3,55-63` ("design draft, not yet operated"), `67-76` (no config/.env backup), `80-84` (no replication monitoring). No snapshot of `signal_copier.db` before a schema change (no code, no Makefile target: `Makefile` targets lint/typecheck/test/security/audit/docs-check). `downgrade()` raises for 0001–0035 (`0035:37`); 0036/0037 implement downgrade but the next `SignalStore.__init__` re-creates everything (`app/db.py:2019-2026`), so a rollback is undone on restart.

Fix: `VACUUM INTO` a dated copy at startup when `alembic_code_head()` changed; state in MIGRATIONS.md that rollback = restore from that copy.

### F-14 — P2 — missing — account activation is not drivable from the TR console

Evidence: TR-08 always posts `enabled: false` (`tr08.js:332`); TR-07 offers only "Pause new entries" (`tr07.js:553-589`); the only `enabled=true` UI is the legacy form (`dashboard.html:848-880`) gated by `LEGACY_DASHBOARD_ENABLED` default `False` (`app/config.py:84`). TR-08's own note points the owner at that hidden form (`tr08.js:283,296`).

Fix: add an explicit "Enable account" action (full-row round trip, see F-03) on TR-07.

### F-15 — P3 — silently-unsupported — `broker` not validated at creation

Evidence: `create_or_update_account` stores `request.broker` verbatim (`app/main.py:2474-2477`); docstring admits it (`2442-2446`). Credentials are env-only (`app/main.py:149-223`, `{BROKER}_{ACCOUNT_ID}_*`), never accepted by any UI/API (tr08.js:176-184 renders the naming convention only). With `delivery_mode=single` a typo'd broker yields an ERROR row and the loop continues to the next candidate before binding (`app/engine.py:701-718` precedes `1077-1090`), so the trade is not lost on multi-destination rules, only on single-destination ones.

Fix: 422 when `request.broker not in brokers` unless `allow_unregistered=true`.

### F-16 — P3 — console statements contradicting the backend

- TR-13 says no `run now` endpoint exists and renders it `unsupported` (`tr13.js:74-78,481,515-519`); TR-06 says "there is no owner-facing action to trigger that pass" right above its own button (`tr06.js:627`); `POST /reconciliation/run-now` exists (`app/main.py:2083-2094`).
- TR-16 "Trading authority — placeholder pending P0-6" (`tr16.js:28-30,300`) while `/system/readiness` reports the live fencing token (`app/main.py:966-972`); "No automated fencing/epoch mechanism exists" (`tr16.js:138-139,416,651`) contradicts `app/writer_lease.py`.
- TR-14 "no fee column exists in `orders`" (`tr14.js:6-7,61-64,730`) — `orders.fee/fee_currency/slippage` exist since 0036 (`app/db.py:244-247`) and are written by `save_order_result` (`2844,2869-2871`); live DB has 2 rows with `fee` set. TR-03/TR-14 compute fees from `fee_per_fill × fills` instead of the column.
- TR-05 "Routing preview" lists every destination of a matching rule as `matched/ROUTED` (`tr05.js:181-184,205-215`) — post-ALLOC-01 a `single` rule selects one; TR-11's simulator is correct, TR-05 is not.
- TR-04 "Trading authority (per source) is not exposed by any endpoint" (`tr04.js:533-539`) — global authority is exposed by `/system/readiness`.
- TR-03 "Exit now" uses bare `confirm()/alert()` (`tr03.js:1670-1673`) unlike every other trade action's `confirmAction` flow.

### F-17 — P3 — doc drift
`app/config.py:413` → `docs/risk/CIRCUIT_BREAKER.md` (absent; `docs/risk/` does not exist). `.env.example` and `docs/operations/CONFIGURATION.md` do not mention `DEFAULT_DAILY_LOSS_LIMIT_PERCENT`/`DEFAULT_MIN_EQUITY_THRESHOLD` (grep). `docs/database/MIGRATIONS.md:127-149` lists head 0015. README `uvicorn app.main:app --reload` (`README.md:1317`) is fine (one worker) but no doc states the single-worker requirement in operational terms other than the rate-limit note (`README.md:1476-1479`).

### F-18 — P3 — dependency pinning
`requirements.txt` uses `>=` for every runtime dependency, no `requirements.lock`/`uv.lock`/`poetry.lock` (ls), `pyproject.toml` has no `[project] dependencies`; `python:3.11-slim` floating tag (acknowledged in `docs/operations/DEPLOYMENT.md:35-37`). Vendored JS is pinned (`app/static/vendor`).

### F-19 — ok — security posture (Q5 checklist)
- Auth: every financial/config route in the 140-route listing carries `Depends(require_owner)` (mutations) or `require_owner_read` (GETs). Unauthenticated by design: `/health`, `/auth/*`, `/`, `/static/*`, the five ingress routes, `/ingest/notification-bridge/{device}` (argon2 per-device pairing token, `app/main.py:5414-5419`), `/catalog/providers/{source}/fit-simulation` (HMAC service token, `5942-5956`).
- Webhook: fail-closed when unset (`1253-1256`), `hmac.compare_digest` (`1257`); one global `WEBHOOK_SHARED_SECRET` for all `/webhook/{source_name}` (no per-source secret — a leaked secret can post as any source; P3 given single owner). Twilio signature + sender allowlist; WhatsApp HMAC + allowlist; NinjaTrader shared header, constant-time.
- CSRF: token returned in login body, required on non-GET (`app/auth.py:175-177`); `/auth/logout` exempt (low). Cookie: httponly, samesite=strict, `secure` only with HTTPS or `FORCE_SECURE_COOKIES` (`app/main.py:1164-1177`). Credential-epoch session revocation (`app/auth.py:79-92`).
- Secrets in logs: structlog redacts keys matching password/secret/token/api_key/auth (`app/logging_config.py:40-49`), only for structlog callers; no raw-payload `logger.*` calls found (grep). Raw webhook payload is persisted/exported as `raw_source_event` (`app/main.py:1299-1309`) — operators must keep secrets out of alert bodies.
- Account-id-from-payload: close/flatten validate against `routing_config.accounts` (`2011-2013`, `2061-2063`); no route trusts a payload account id blindly.
- XSS: every view escapes through `escapeHtml/escapeAttr`; heuristic scan of all `innerHTML` interpolations left only numbers, enum strings and pre-escaped fragments; CSP `script-src 'self' 'unsafe-inline'` (`app/main.py:629-633`) is defence-in-depth only.

---

## Q6 — Per-screen truthfulness table

Legend: **Real** = rendered from the named endpoint(s); **Placeholder** = `renderCapabilityState('not_tracked'|'unsupported')` / StateMatrix `unsupported`; **Hardcoded** = literal data used as data. ⚠ = flagged.

| Screen (route) | Real-endpoint panels | Placeholders | Hardcoded | Flags |
|---|---|---|---|---|
| TR-01 `/trade` | /health, /accounts, /accounts/{id}/balance|economics|equity-history|statistics, /accounts/correlation, /positions, /orders, /signals, /capital-allocation, /system/info, /providers | NAV, Reserved risk, Capacity; conditional: Today's P&L, Open risk, Available capital, downside corr., marginal risk, diversification | palette only | ⚠ "Unknown broker operations"/attention `unknown_order` = lifecycle pending_* only, ledger UNKNOWN rows excluded (F-05); donuts are position *counts* (disclosed) |
| TR-02 `/trade/positions` | /positions, /accounts, /saved-views (CRUD), /capital-allocation, /equity-history | Analyst, Possible closes, P&L basis, Return on deployed capital | none | none |
| TR-03 `/trade/positions/:a/:s` | /positions, /accounts, /economics, /orders, /brokers, /signals, /stop-events, /lifecycle/*/preview-*, POST close, POST reconciliation/run-now | Current mark, Unrealized P&L, Strategy, Portfolio, Latency cost, conditional exit-management/fees | none | ⚠ Fees from `fee_per_fill × fills` ignores `orders.fee` (F-16); Exit now bare confirm (F-16) |
| TR-04 `/trade/signals` | /health, /signals, /orders | Per-source connection status, Trading authority, Parser status (per row), Backlog | none | ⚠ "trading authority not exposed" stale (F-16) |
| TR-05 `/trade/signals/:id` | /signals, /routing-rules, /accounts, /brokers, /orders, POST classify-messages | Parsed-field provenance, Horizon, Compare parser versions, Reclassify (structured sources) | none | ⚠ Routing preview marks every destination of a `single` rule ROUTED (F-16) |
| TR-06 `/trade/orders` | /orders, /signals, /accounts/{id}/execution-quality, /positions, POST flatten, POST reconciliation/run-now | 3 of 6 latency segments, Correlations | `LATENCY_SEGMENTS` (labels/reasons, not data) | ⚠ Unknown-outcome queue = pending orders only (F-05); stale "no on-demand trigger" sentence |
| TR-07 `/trade/accounts` | /accounts, /brokers, /positions, /qualifications (GET/POST), /orders?account_id, /balance, POST /accounts | Credential ref, Account attributes, Venue/Env (when null), Latency/quota, Writer site | `CREDENTIAL_REF_PATTERNS` (naming convention, disclosed) | ⚠ Pause clobbers safety fields (F-03); no Enable action (F-14) |
| TR-08 `/trade/accounts/new` | /brokers, POST /accounts | Environment override, External ref, Position mode, live checks, 6 "not tracked" attributes | `CREDENTIAL_REF_PATTERNS` | ⚠ Always `enabled=false`, upserts (silently disables an existing id) |
| TR-09 `/trade/sources` | /providers, /routing-rules, /signals(100), /health, /orders, /positions/excursions, /providers/value, /stop-events(≤25), /accounts, /equity-history, /statistics, /accounts/correlation, POST /providers/{id} | Transport health (unsupported), Rights, Channel/product, Cursor, Parse %, Capital util, per-provider drawdown | `SOURCE_MODULES` (hand-maintained transport map, disclosed) | none beyond disclosed caps |
| TR-10 `/trade/sources/new` | /providers, /routing-rules, /signals, POST classify-messages, POST import-signals, POST providers, POST analysts | Transport instance, Channel/product, Rights, Expected/Unconsumed/Difference, Import job | none | ⚠ "Save source draft" posts only `display_name` → may reset existing provider overrides (same class as F-03; UNVERIFIED for `upsert_config_provider` NULL handling) |
| TR-11 `/trade/routing` | /routing-rules CRUD, /accounts, /providers, POST simulate, POST position-impact | Instrument filter | none | ⚠ delete = bare confirm, no impact check (F-07); client-side `destinationsFor` mirror (disclosed) |
| TR-12 `/trade/policies` | /providers, /accounts, /signals, /brokers, /positions, /excursions, /orders, /capital-allocation, /stop-events, /policies/sizing-preview, POST providers/analysts | Product profile, Fallback stop recipe, Trailing, Deadlines, Versions, Request review, target-count, stop-vs-MFE | none | ⚠ override save sends `managed_lifecycle: null` (F-01 trigger) |
| TR-13 `/trade/incidents` | /positions, /orders, /health, /accounts, /brokers, POST close | Broker qty, Detected time, Containment (non-halt), Resolution | none | ⚠ claims no run-now endpoint (F-16); incidents exclude ledger UNKNOWN rows and plain-account exposure (F-05) |
| TR-14 `/trade/performance` | /accounts, /brokers, /economics, /execution-quality, /equity-history, /statistics?window, /orders, /capital-allocation, /excursions, /providers/value, /stop-events | Exposure-adjusted return, fees (non-paper), funding, slippage, return histogram, holding time, exit reasons, strategy/regime | `ORDERS_FEE_SAMPLE_LIMIT`, provenance vocabulary | ⚠ "no fee column" stale (F-16); CSV header `net_realized_pnl_gross_of_fees` repeats gross |
| TR-15 `/trade/backtests` | /signals, /accounts, POST /backtest, /backtest/runs, /backtest/runs/{id}, market-path | Capital utilization, MAE/MFE, Baseline, Resume job, costs (when not requested) | none | none |
| TR-16 `/trade/system` | /system/info, /health, /positions, /metrics (text), /system/readiness | Backup, Restore, Fencing, Release status, 3 actions | runbook gate text | ⚠ Deployment "Mismatch" inverted + destructive remediation, gate 3 blocked (F-04); fencing/authority notes stale (F-16) |
| TR-17 `/trade/providers/add` | /connections/catalog, /connections/catalog/{t}/setup-fields, POST providers/connections/sources | none | `QUICK_ADD_PRESETS` (2, filtered by catalog) | partial-failure rows left (disclosed) |
| TR-18 `/trade/mobile-devices` | /mobile-devices (GET/PATCH), /apps, POST test | Test App always `unavailable` | none | none |
| TR-19 `/trade/provider-catalog` | /provider-catalog/* GETs | none | none | two provider models (disclosed) |

Numbers not backed by their claimed endpoint: none found that are fabricated; the mis-labelled ones are the
F-16 items (stale "does not exist" claims) and the F-05 "unknown operations" counts that *under*-report.

## Q6 — Owner workflows

| Workflow | UI-drivable? | Notes |
|---|---|---|
| Create account | TR-08 (inactive only) | credentials = env vars + restart (`{BROKER}_{ACCOUNT_ID}_*`) |
| Activate account (`enabled=true`) | **No** on TR screens | legacy form behind `LEGACY_DASHBOARD_ENABLED=false`, else API (F-14) |
| Pause account | TR-07 | clobbers fields (F-03) |
| Record route qualification (needed before live ENTRY) | TR-07 POST /qualifications | ok |
| Create/edit/delete routing rule (incl. delivery_mode) | TR-11 | no delete guard (F-07) |
| Provider/analyst overrides, pause provider | TR-09/TR-10/TR-12 | override save can flip managed branch (F-01) |
| Add push source (webhook) | TR-17/TR-10 + env `WEBHOOK_SHARED_SECRET` | restart needed for secret |
| Add pull source (Telegram/Discord/Slack/Twitter/email) | registry rows via API/TR-17, credentials env + restart | per-collector env var + restart |
| See signal → position → close | TR-04/05 → TR-02/03 → TR-03/TR-13/TR-06 | ok |
| Strategy budgets, allocation-intent audit | API only | F-10 |
| Backups/restore/promotion | CLI/runbook only | TR-16 gate 3 blocked (F-04) |

---

## Q-by-Q quick answers

- **Q1 drift**: fresh DB (SCHEMA) ⊃ upgraded DB for `config_accounts.daily_loss_limit_percent/min_equity_threshold` (SCHEMA `db.py:317-319`, not in `_COLUMN_MIGRATIONS`; live upgraded DB lacks them). All other 0036/0037 objects reach both paths (tables via `CREATE TABLE IF NOT EXISTS`, `strategy_key`/`delivery_mode`/`fee*` via `_COLUMN_MIGRATIONS:1867-1868,1999-2001`). Alembic path is documentary only; `alembic upgrade head` on any bootstrapped DB fails from 0016 onward. `list_config_accounts`/`load_routing_config_from_store` do **not** read the loss-limit columns (F-02). Type drift: `margin_call_alerts.resolved` server default (F-04).
- **Q2 config safety**: delete/broker-change refusal verified (`main.py:2465-2473,2499-2503`); `managed_lifecycle`, `symbol_map`, provider/analyst `managed_lifecycle` override, provider delete — unguarded (F-01); rule deletion unguarded, CLOSE routes by current rules (F-07); `enabled=false` at account/provider level correctly lets CLOSE through (`routing.py:144`, `engine.py:659`).
- **Q3 startup/loops**: lifespan starts heartbeat, webhook, configured pull sources, reconciler, price monitor, provider scout, equity snapshotter, relay (if configured) (`main.py:519-538`). Every loop wraps each pass in `except Exception` and continues (`reconciliation.py:101-110`, `pricing.py:83-103`, `provider_scout.py:82-90`, `equity_history.py:121-129`, `relay_scheduler.py:51-74`) — no restart needed; a source whose `start()` raises is logged and stays down (`main.py:522-526`). `/health` is truthful for what it folds (OPS-01 fixed, `740`) but not for disk-full (F-08) or protection/ledger issues (F-06). Startup: lifecycles restored (`manager.py:572-602`), unprotected ones retried every reconcile pass (`335-369`); command-ledger rows and allocation intents: no recovery pass (F-05, F-10).
- **Q4 writer lease**: single writer per `site_id`; no same-host multi-process check; `--workers 2` → second worker fences first, 500s, split in-memory state (F-09). Deploy configs use one worker.
- **Q5 security**: F-19 (ok), F-11, F-18.
- **Q7 credentials**: env only; UI cannot add; `broker` unvalidated at API (F-15).
- **Q8 backups/migrations**: F-04, F-13.
- **Q9 time**: F-12.
- **Q10 notifications**: F-06.

UNVERIFIED items: `upsert_config_provider` NULL semantics for TR-10's `display_name`-only save (not read); whether any external broker adapter populates `OrderResult.fee` (live DB shows 2 rows with `fee`, origin not traced); TradingView retry behaviour on a 500 (F-09 impact).
