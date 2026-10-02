# Audit G — signal-portfolio-commercial (customer/publisher platform) + contracts + copier export seam

Read-only code audit. Repos: `signal-portfolio-commercial` (SPC), `signal_platform_contracts` (SPCo), `signal-copier` only at `app/export_events.py`, `app/relay_worker.py` (plus the engine/broker lines that decide what those export). All paths below are relative to the repo named; line numbers from the files as read on 2026-10-02.

Severity: P0 = can place/duplicate real orders, leak tenant data, or mislead a paying customer about performance; P1 = silently wrong; P2 = missing capability; P3 = doc/test.
Classification: semantic-miss | disconnected | mock-only-tested | silently-unsupported | missing | ok.

## Question → finding index

| Q | Findings |
|---|---|
| 1 Execution authority | G-C-01 (ok), G-C-02, G-C-03, G-C-04, G-C-05, G-C-06, G-C-07 |
| 2 Copy semantics | G-C-08, G-C-09, G-C-10 |
| 3 Performance claims / four books | G-C-11 (ok map), G-C-12, G-C-13, G-C-14, G-C-15, G-C-16, G-C-17, G-C-18 |
| 4 Rights | G-C-19, G-C-20 |
| 5 Tenancy | G-C-21 (ok map), G-C-22, G-C-23 |
| 6 Ingest | G-C-24, G-C-25, G-C-26, G-C-27 |
| 7 Billing | G-C-28 |
| 8 PAMM/MAM | G-C-29 (ok), G-C-30 |

Bottom line: **no P0 found** — nothing in SPC can place, cancel or modify an order, hold a broker credential, or open the copier's SQLite; customer/public pages show no fabricated track record. The real problems are P1 semantic misses in how the copier's events are *interpreted* (no account dimension, scalar routing outcome, unlabelled paper/live), two permanent-stall modes on ingest, an unreachable customer copy journey, and the same "every OPEN is a BUY" direction assumption the copier had.

---

## Q1 — Execution authority

### G-C-01 — No execution authority anywhere in SPC — **ok**
- Severity: n/a (confirmation). Classification: **ok**.
- Evidence:
  - Only two outbound HTTP calls in `app/`: `grep httpx|requests|urllib|socket|sqlite3` → `app/services/fit_simulation_client.py:186-194` (`post(url, content=body, headers={"x-catalog-fit-sim-signature": ...})` to `{SIGNAL_COPIER_BASE_URL}/catalog/providers/{source}/fit-simulation`, body = `{source, account_size, max_per_trade, lookback_days, csv_paths}` at `:175-183`) and `app/main.py:347-348` (`client.get(f"{SIGNAL_COPIER_BASE_URL}/health")`). Neither carries a signal, size, side or account id for execution.
  - Collective2: `app/services/collective2_publisher.py:1-8` "Builds and validates the outbound request; does NOT transmit it"; `build_order` (`:86-140`) returns a dict `{StrategyId, C2Symbol, OrderType, Side1, OrderQuantity, TIF}` — an API4 *strategy signal post* (StrategyId = publisher's own strategy), never an order on a customer's account. No HTTP client in the module.
  - eToro: `app/services/etoro_adapter.py:48-59` refuses `account_mode != "demo"`, builds a dict only. CopyFactory: `app/services/copyfactory_close_only.py:7-10` "does not call CopyFactory".
  - Credentials: `credential_ref` is "an opaque reference, never a real secret" (`app/models/publisher_destination.py:13`, `app/models/integration_configuration.py:10`); `PlatformConnection.masked_account_label` "never a real account number or credential" (`app/models/platform_connection.py:66-68`); only `local_simulation` environment can be saved (`app/services/publisher_destination.py:76-80`, `platform_connection.py:63-67`).
  - SQLite: `app/db.py:1-5` "this process never opens that file"; no `sqlite3` import anywhere in `app/`.
  - Relay ingress is inbound-only, on `relay_role` with SELECT/INSERT/UPDATE on `inbox_events`, INSERT on `ledger_entries`, SELECT on `export_stream_registrations` (`app/db.py:249-263`).
  - Duplication guard on the (unused) intent table: `idempotency_key` unique + natural key unique (`app/models/publication.py:90-95,125`; `app/services/publication.py:28-42`).
- Scenario: none — fan-out/duplication cannot originate here today.
- Fix: none; keep `tests/test_commercial_live_secret_guard.py` and the relay-role tests as regression anchors.

### G-C-02 — `PublicationIntent` has no side/direction; both adapters hard-code BUY for OPEN/ADD — **P1**
- Classification: **semantic-miss**, **mock-only-tested**.
- Evidence:
  - `app/models/publication.py:97-132` — columns: `action`, `instrument_id`, `quantity`, `quantity_basis`, `price_basis`… **no `side`/`direction` column**. `PublicationAction` (`:52-63`) is OPEN/ADD/REDUCE/CLOSE/… (lifecycle verbs, not directions).
  - `app/services/collective2_publisher.py:131` — `side = Side.SELL if intent.action is PublicationAction.REDUCE else Side.BUY`.
  - `app/services/etoro_adapter.py:92-96` — `_build_open_request` returns `"direction": "BUY"` unconditionally.
  - Test encodes it as intended: `tests/test_collective2_publisher.py:52-55` `test_an_open_builds_a_buy_market_order`, `:60-63` ADD → BUY.
- Scenario: a provider "sell/short XYZ" signal mapped to an OPEN intent would be published to C2/eToro as a long BUY (and a REDUCE on a short would be published as a SELL that *adds* to the short). Exactly the copier's "sell executed as short entry" class of miss, inverted. Not live today (nothing transmits, G-C-03), so P1 not P0.
- Fix: add a required `side` (buy/sell) to `PublicationIntent` and `PublicationIntent.schema.json`, derive `Side1`/`direction` from it, and make `build_order` refuse an OPEN/ADD/REDUCE without one.

### G-C-03 — The whole publish pipeline is disconnected: nothing creates, admits, transitions or sends an intent — **P2**
- Classification: **disconnected**.
- Evidence: `grep build_order\(|build_trade_request\(|admit_publication_intent\(|enqueue_intent\(|transition\(session` in `app/` → only their definitions (`collective2_publisher.py:86`, `etoro_adapter.py:48`, `publication_admission.py:48,91`, `publication.py:23,70`). `grep PublicationIntent\(` → only the model. Readers of the table that nothing writes: AD-10 `publication_admin.py:41-52`, AD-01 `operations_overview.py:75-83`, CU-04/05 `customer_alerts.py:51-97`.
- Scenario: the rights/entitlement/writer-claim admission gate (`publication_admission.py:66-89`) and the state machine are correct but have no caller; CU-04 "Alerts" is structurally always empty.
- Fix: either wire an intent-creation path behind `admit_publication_intent` or label AD-10/CU-04/CU-05 as "no publisher pipeline" rather than empty tables.

### G-C-04 — Provider CLOSE cannot be published to Collective2 (unmapped action) — **P2**
- Classification: **silently-unsupported** (loud at call time, silent at design time).
- Evidence: `app/services/collective2_publisher.py:52` `_QUANTITY_ONLY_ACTIONS = {OPEN, ADD, REDUCE}`; `:105-110` any other action raises `UnsupportedQuantityBasisError`; `tests/test_collective2_publisher.py:101-105` asserts CLOSE raises. eToro maps CLOSE only with an explicit `position_id` (`etoro_adapter.py:61-67`) that no SPC model stores.
- Scenario: if G-C-03 were wired, every provider exit would raise and the published strategy would never flatten.
- Fix: map CLOSE to a full-quantity opposite-side order using a tracked external position (needs position identity on the intent).

### G-C-05 — INT-033 "real account cannot gain a second writer" guard has no caller — **P2**
- Classification: **disconnected**.
- Evidence: `grep register_real_account_route\(|qualify_exclusive_ownership_plan\(` → only definitions `app/services/real_account_route.py:16,78`.
- Scenario: the alias/second-writer protection the model docstring promises is never enforced on any route.
- Fix: call `register_real_account_route` from publisher-destination / platform-connection creation once a real account reference exists.

### G-C-06 — Writer claim is global, first-come, and its error echoes another tenant's `tenant_id` — **P2**
- Classification: **silently-wrong** (cross-tenant metadata leak + squatting).
- Evidence: `app/services/publisher_writer_claim.py:23-29` raises `f"... already claimed by {existing.writer_identity!r}"`; `app/services/publisher_destination.py:83-87` re-raises `str(exc)`; `app/api/dashboard_routes.py:1762-1773` renders `"error": str(exc)` into AD-09. `writer_identity=tenant_id` (`publisher_destination.py:84`). `publisher_writer_claims` is not in `_TENANT_SCOPED_TABLES` (`app/db.py:35-44`).
- Scenario: any PUBLISHER_OPERATOR in tenant B saves `collective2 / strategy-123` (local_simulation, no proof of ownership) and (a) blocks tenant A from ever registering it, (b) learns A's tenant_id from the error.
- Fix: scope claims per tenant or require proof-of-ownership; return a generic "already claimed" message.

### G-C-07 — Fit-simulator sends server-side `csv_paths` to the copier under a placeholder-default secret — **P2 (UNVERIFIED copier side)**
- Classification: **silently-wrong** (latent).
- Evidence: `app/services/fit_simulation_client.py:171-183` forwards `csv_paths` from `FIT_SIM_CATALOG_CONFIG_JSON`; signing secret default `"LOCAL_SIM-not-a-real-catalog-fit-sim-secret-..."` (`app/config.py:102`) refused only in COMMERCIAL_LIVE (`:157-171`). Rate-limited 10/min/IP (`app/rate_limit.py:29`, `dashboard_routes.py:1346`). Whether the copier validates `csv_paths` against an allow-list was not read (out of the stated scope) — UNVERIFIED.
- Scenario: a non-LIVE deployment with the placeholder secret lets anyone who knows it request arbitrary CSV paths on the copier host.
- Fix: refuse placeholder secrets whenever `SIGNAL_COPIER_BASE_URL` is set, and have the copier resolve `source → csv_paths` itself.

---

## Q2 — Customer copy semantics

### G-C-08 — A "copy" is a draft record with no sizing rule, no sell/close/trim handling, no edit/cancel handling, and nothing executes it — **P2**
- Classification: **missing** (honestly labelled).
- Evidence:
  - `app/models/copy_mandate.py:46-53` states only `draft`/`cancelled`; fields `allocation_amount`, `allocation_currency`, `max_trade_risk`, `max_loss`, `start_mode` (`:70-81`) — no multiplier, no equity-fraction, no "copy raw provider quantity" choice, and nothing reads them.
  - `app/services/copy_mandate.py:5-31` "never activates anything, never touches a broker"; `trading_authority.py:207-220` reports `missing_input:execution_activation_pipeline`.
  - UI labels: `app/templates/cu09_copy_wizard.html:67,77`, `cu10_manage_mandate.html:31,36,50` — Preview/Activation/Pause/Wind-down all `UNSUPPORTED`.
  - Edited/cancelled publication: `PublicationState.SUPERSEDED` exists (`publication.py:84`) but nothing creates or supersedes intents (G-C-03). Provider DELETE/CANCEL/CLOSE on ingest never voids the SOURCE entry (`integration_inbox.py:616-624`).
- Scenario: a customer sees "allocation 10,000 USD" and nothing in the system defines what a provider "buy 100 AAPL" would become for them (10,000/price? 100 shares? allocation × multiplier?). Nothing is executed or simulated; it is display-only.
- Fix: define the sizing contract on the mandate (equity-fraction vs fixed-multiplier vs raw) *before* any activation pipeline, and reject activation without it.

### G-C-09 — Double exposure across two portfolios is neither shown nor recordable per portfolio — **P2**
- Classification: **missing**.
- Evidence: `PortfolioSelection` uniqueness is per (tenant, user, product) only (`app/models/portfolio_selection.py:68-75`); no customer position/exposure model exists; `LedgerEntry` FOLLOWER rows carry `follower_connection_id` only, `sleeve_id` is SOURCE-only (`app/models/ledger.py:134-168`); CU-06 replays per (tenant, book, instrument) across the customer's connections (`customer_performance_report.py:221-224`).
- Scenario: customer copies portfolios A and B (both long AAPL) into one connection; before fills nothing warns of 2× AAPL; after fills (if a FOLLOWER connector ever lands) the net AAPL position is correct but unattributable to A vs B.
- Fix: carry `portfolio_version_id`/`mandate_id` on FOLLOWER entries (contracts already define `PortfolioIdentity`/`CommercialIdentity`, `identity.py:102-127`) and add an exposure preview on CU-09.

### G-C-10 — Self-signup customers can never select a product, so the copy journey is unreachable end-to-end — **P1**
- Classification: **disconnected**.
- Evidence:
  - `app/services/local_auth.py:128-133` — signup creates a **new `Tenant`** and a CUSTOMER membership in it.
  - `app/services/portfolio_selection.py:55-59` → `get_product(session, product_id, tenant_id=tenant_id)`; `product_admin.py:73-76` returns None when `product.tenant_id != tenant_id` → `PRODUCT_NOT_PUBLISHED_OR_NOT_FOUND`.
  - CUSTOMER cannot be invited into the operator tenant: `app/services/staff_access.py:40-48` `GRANTABLE_ROLES` excludes CUSTOMER; `ops/bootstrap.py:143` provisions OWNER only.
  - Downstream gates all key off an ACTIVE selection: `copy_mandate.py:102-106`, `onboarding_progress.py:117`, `customer_alerts.py:56-62`.
- Scenario: public catalog (cross-tenant via `product_visibility`, `db.py:141-151`) shows the operator's PUBLISHED product; the customer signs up, goes to CU-02, submits the product id, gets `PRODUCT_NOT_PUBLISHED_OR_NOT_FOUND`; CU-09 then shows "A verified eligible connection and released portfolio are required" forever. Tests only exercise same-tenant customers (`tests/test_copy_mandate.py:111-176`).
- Fix: decide the tenancy model (customers as members of the operator tenant vs customer-tenants referencing operator products) and make `create_portfolio_selection` accept any PUBLISHED product under that rule.

---

## Q3 — Performance claims and the four books

### G-C-11 — What actually feeds each book and each displayed number — **ok (map)**
- Classification: **ok** (nothing seeded/demo; nothing fabricated).
- Evidence:
  - **SOURCE** ← `SOURCE_RECEIPT` only when the recommendation carries both quantity and price (`app/services/integration_inbox.py:399,460-476`); revisions become corrections (`:400-451`). Signals without an explicit size produce no row (`:366-377`). Not displayed as performance anywhere (`grep Book.SOURCE` → ingest and docs only).
  - **MODEL** ← nothing (ADR-0004 `docs/adr/0004-four-book-economic-ledger.md:29-30`).
  - **PLATFORM** ← `EXECUTION_APPLIED` (copier exports only `OrderStatus.FILLED`, `signal-copier/app/export_events.py:121-123`; lifecycle stop/target exits do export, `signal-copier/app/lifecycle/manager.py:1557-1586`) + activated snapshot baselines at average cost (`integration_inbox.py:932-948`). Displayed only to OWNER/RESEARCHER at `/ops/trading` and `/api/v1/ops/platform-performance` (`dashboard_routes.py:507-512,583-589`; `permissions.py:179`).
  - **FOLLOWER** ← `ingest_observed_fill` (`follower_observation.py:97-142`) which has **no caller** (grep) → CU-06/CU-01/CU-03 always `AWAITING_OBSERVATIONS`/`NOT_CONNECTED` (`customer_performance_state.py:56-71`).
  - **Public PU-02/03/04** show no track record: `pu03_portfolio_detail.html:62-65` "A released track record is not available"; `public_site.py:65-70,185-189`. Only number: the fit simulator (G-C-16).
  - No seed/demo/fixture data path in `app/` (grep `seed|demo|fixture` in routes → only publisher-environment option lists; `ops/bootstrap.py` seeds tenant/streams only).
- Scenario: a paying customer today cannot be misled by a number because none is shown; the risk is concentrated in the owner-facing PLATFORM view (G-C-12/13/14) and in latent FOLLOWER logic (G-C-17).

### G-C-12 — PLATFORM book has no account dimension: all copier accounts (and a fan-out) collapse into one position per instrument — **P1**
- Classification: **semantic-miss**.
- Evidence:
  - Copier exports one stream per account (`signal-copier/app/engine.py:358` `f"signal-copier:{account.account_id}"`); `ops/bootstrap.py:211-218` registers *every* account stream to the same tenant.
  - `app/services/integration_inbox.py:343-360` `append_entry(... book=Book.PLATFORM, instrument=..., side=..., quantity=payload.filled_quantity ...)` — `payload.account` is discarded; `app/models/ledger.py:84-175` has no `account_id` column.
  - `app/services/platform_performance.py:226-230,328` groups by `(tenant_id, book)` then `fill.instrument`; `analyst_attribution.py:196-219` likewise.
- Scenario: the copier's fan-out bug (one signal filled on N accounts) shows as an N× position and N× realized P&L on the owner's `/ops/trading`; a paper account and a live account trading AAPL are averaged into one cost basis; a stop-out on account A "closes" shares bought on account B.
- Fix: persist `account_id` from `PrivateAccountIdentity` on `LedgerEntry`, replay per `(account, instrument)`, and show per-account rows.

### G-C-13 — Paper/live/simulated is a single process-wide label on the copier and is neither enforced nor displayed on the commercial side — **P1**
- Classification: **silently-wrong**.
- Evidence:
  - Copier: every envelope uses `EvidenceClass[config.RELAY_EVIDENCE_CLASS]` / `Environment[config.RELAY_ENVIRONMENT]` (`signal-copier/app/engine.py:368-369,390-391,412-413,450-451`; `lifecycle/manager.py:1575-1576`) — one value per deployment regardless of which broker/account filled (`config.py:340-343`, default `INTERNAL_PAPER`/`LOCAL_SIM`).
  - SPC ingest never reads `envelope.environment` and never compares it to `ExportStreamRegistration.environment` (`integration_inbox.py:1010-1037` vs `:176-197`); `evidence_class` is stored (`:358`) but `compute_book_performance`/`compute_analyst_attribution`/`compute_customer_equity_series` never filter or group by it (`platform_performance.py:226-230`; `analyst_attribution.py:196-198`; `customer_performance_report.py:130-132`).
  - Display: `app/templates/ad_trading_performance.html:62-100` and the JSON at `dashboard_routes.py:513-529` carry no evidence class; the "Environment" column (`ad_trading_performance.html:17,31`) is the operator-typed registration value (`integration_status.py:143`), not the events'.
- Scenario: a deployment routing to `paper` and `alpaca` accounts with `RELAY_EVIDENCE_CLASS=OBSERVED_OWNER_LIVE` books paper fills as live; a stream registered `LOCAL_SIM` accepts `COMMERCIAL_LIVE` envelopes unchallenged; the owner page sums them with no label.
- Fix: copier derives evidence class per account/broker; SPC parks an envelope whose `environment` ≠ the stream's registration, groups replays by evidence class, and labels every displayed figure.

### G-C-14 — Net P&L is structurally never available: copier drops the fee at export and never emits FEE — **P2**
- Classification: **silently-unsupported**.
- Evidence: `signal-copier/app/export_events.py:143` `fee=None` hard-coded although `OrderResult.fee` is populated by the paper broker (`signal-copier/app/brokers/paper.py:121`); `grep EventType.FEE|FeePayload` in `signal-copier/app` → no matches. SPC then counts every entry as unknown-fee (`platform_performance.py:329-339`) → `net_pnl is None` on every instrument → page shows "net unavailable" (`ad_trading_performance.html:65,85`).
- Scenario: the owner can never see a net figure, and the "unknown fee" badge is permanent noise rather than a transient state.
- Fix: carry `result.fee`/`fee_currency` into `ExecutionAppliedPayload.fee`.

### G-C-15 — Public PU-03 version/sleeve facts are hidden by RLS in production; only tested under the superuser fixture — **P2**
- Classification: **mock-only-tested**.
- Evidence: anonymous route `dashboard_routes.py:1322-1333` uses bare `get_db_session` (no `set_tenant_scope`); `public_site.py:82-92` reads `PortfolioVersion`/`PortfolioVersionSleeve`, both in `_TENANT_SCOPED_TABLES` under `FORCE ROW LEVEL SECURITY` (`app/db.py:35-44,74-83`); production role is `NOBYPASSRLS` (`ops/bootstrap.py:105`). `tests/test_public_site.py:116-117` asserts `portfolio_version_number == 3`/`sleeve_count == 1` on the superuser `db_session` fixture only (no `tenant_session_factory` in that file).
- Scenario: every public portfolio page shows "no version selected yet", capacity/cutoff "not set", sleeve count missing — silently, with green tests.
- Fix: add a `published_portfolio_version_visibility` policy (join to a PUBLISHED product) and an RLS-role test for PU-03.

### G-C-16 — The only public number is a backtest of provider *signals* (not fills), labelled "Simulated" but not evidence-classed, rendered unvalidated — **P2**
- Classification: **silently-wrong** (latent; feature unreachable until configured).
- Evidence: `pu03_portfolio_detail.html:108` "historical signals", `:119-120` "Simulated P&L at your size", `:164` methodology note; values rendered straight from `response.json()` (`fit_simulation_client.py:209`) with `"%.2f"|format(fit_sim_result.report.summary.*)`; no `HYPOTHETICAL_BACKTEST` label, no signal-vs-fill or slippage disclosure; gated off by default (`config.py:117` `FIT_SIM_CATALOG_CONFIG_JSON="{}"`). No RightsGrant check on this path (see G-C-19).
- Scenario: once configured, a prospect sees "$X simulated P&L" derived from unfilled, unslipped provider alerts.
- Fix: validate the response with a typed model, stamp it `HYPOTHETICAL_BACKTEST`, and state "signals, not executions".

### G-C-17 — CU-06 would silently drop a DISCONNECTED connection's history (latent) — **P1**
- Classification: **silently-wrong** (latent until a FOLLOWER writer exists).
- Evidence: `customer_performance_report.py:203-205,221-224` scopes `follower_connection_ids` to `state == DECLARED` only; `follower_observation.py:114-118` deliberately keeps a disconnected connection's history as real. Equity series, drawdown, win rate and unresolved count all use that filtered set (`:225-243`).
- Scenario: customer disconnects the account that stopped out; drawdown and win rate improve on CU-06.
- Fix: scope by all of the customer's connection ids (any state) and label disconnected rows.

### G-C-18 — SOURCE book books a provider "sell" as `Side.SELL`; the shared replay treats it as a short open when no long exists — **P2 (latent)**
- Classification: **semantic-miss** (mirror of the copier's).
- Evidence: `integration_inbox.py:77` `_SIDE_BY_PAYLOAD_VALUE = {"buy": BUY, "sell": SELL}`, `:465`; `platform_performance.py:269-278` "Opening or adding to a position on the same side" for a SELL with `open_quantity == 0`. Not displayed today (no SOURCE reader).
- Scenario: a SOURCE-book P&L report (future) would show short positions for providers whose "sell" means "exit".
- Fix: carry the provider's intent kind (entry/exit) on `SourceReceiptPayload`, not only buy/sell.

---

## Q4 — Rights registry

### G-C-19 — Rights are checked on three paths; the one path that actually exposes provider-derived output publicly (fit-sim) and the public catalog are not gated — **P1**
- Classification: **missing**.
- Evidence:
  - Checked: `product_admin.compute_publication_blockers` (`:196-205`, `COMMERCIAL_ALERTS`/`web`/`US`); `trading_authority.assess_trading_authority` (`:168-178`, `AUTOMATED_PUBLICATION`/`MANAGED_ACCOUNTS`, **hard-coded** `channel="execution", jurisdiction="US", asset="equity"`); `publication_admission.admit_publication_intent` (`:66-80`, no caller — G-C-03).
  - Not checked: `RightsUse.PUBLIC_METRICS` is never referenced outside the enum (`grep PUBLIC_METRICS` → `models/rights.py:35`); public catalog/detail/compare query lifecycle only (`product_admin.py:220-232`, `public_site.py:72-76`); `POST /portfolios/{slug}/fit-simulation` (`dashboard_routes.py:1345-1402`) runs the copier backtest over a provider's signal history with no `check_rights`/`check_portfolio_rights`; customer alerts gate on selection only (`customer_alerts.py:51-65`).
- Scenario: a provider whose grant is UNKNOWN (`NAMED_UNKNOWN_SOURCES`, `rights_registry.py:40`) has its signal-derived "simulated P&L" shown to anonymous visitors; a non-US customer or a crypto sleeve is never actually rights-checked by the trading-authority gate because jurisdiction/asset are literals.
- Fix: gate fit-sim and PU-03 on `check_portfolio_rights(use=PUBLIC_METRICS, channel="web", ...)`; pass real jurisdiction/asset into `assess_trading_authority`.

### G-C-20 — Rights register is platform-wide and readable by any tenant's OWNER/REVIEWER — **P2** (P1 if more than one operator tenant shares the DB)
- Classification: **silently-wrong**.
- Evidence: `rights_registry.py:84-93` plain `select(RightsGrant)`; `rights_grants` absent from `_TENANT_SCOPED_TABLES` (`db.py:35-44`); route `dashboard_routes.py:1159-1163` uses `get_current_scope` + `view_rights_register` (`permissions.py:44`). Rows carry `source_id`, `grantee_entity`, `contract_hash` (`models/rights.py:45-48`) which the spec calls private.
- Scenario: UNVERIFIED whether multiple operator tenants are an intended deployment; if so, operator B sees A's provider contracts.
- Fix: add `tenant_id` to `rights_grants` (or document single-operator as a hard invariant and assert it at bootstrap).

---

## Q5 — Tenancy / isolation

### G-C-21 — Auth dependency per router (map) — **ok**
- Classification: **ok**.
- Evidence (all `app/api/dashboard_routes.py`):
  - Every `/ops/*` and `/app/*` handler: `Depends(require_tenant_scope)` (sets `app.tenant_id` GUC, `dependencies.py:114-166`) + a per-route `require_permission` helper (e.g. `:419,786,861,1444,1506,1706,1784,2159,2236,2320,2473,2641,2718,2741,...,3311`). Documented exceptions using `get_current_scope`: `rights_register_page:1162`, `deployment_status_page:3192`, `platform_connection_wizard_page:3245` (no tenant-scoped query).
  - Public: `/`, `/portfolios`, `/portfolios/{slug}`, `/compare`, `/pricing`, `/help`, `/status`, `/methodology`, `/terms`, `/privacy`, `POST /portfolios/{slug}/fit-simulation` use bare `get_db_session`; isolation relies on bespoke RLS policies `product_visibility` / `content_document_visibility` (`db.py:141-208`) and lifecycle filters.
  - Auth bootstrap: `sign_in_submit:3547-3560`, `verify_email_page:3646-3654` via `set_current_user_scope` (ADR-0009).
  - Relay: `relay_routes.py:57` `get_relay_db_session` → tenant resolved from `export_stream_registrations` only (`integration_inbox.py:1012-1017`).
  - Services add app-level `tenant_id` filters and scoped-404s: `copy_mandate.py:68-75`, `platform_connection.py:46-55`, `portfolio_selection.py:40-49`, `customer_support_view.py:86-89`, `publication_admin.py:44-48`, `managed_program.py:42-46`, `research_run.py:55-59`.
  - RLS exercised under a real `app_role` in `tests/test_row_level_security.py:45-306`, `test_trk38_rls_ledger_and_inbox.py`, `test_relay_role_access.py`, `test_login_membership_bootstrap_rls.py`.
- Result: no endpoint found that takes a customer/publication/selection/mandate id and returns another tenant's row. Non-tenant tables: `rights_grants` (G-C-20), `publisher_writer_claims` (G-C-06), `real_account_routes`, `processed_webhook_events`, `tenants`, `user_identities`, `web_sessions`, `auth_tokens`.

### G-C-22 — Login binds a multi-membership user to an arbitrary tenant — **P2**
- Classification: **silently-wrong**.
- Evidence: `dashboard_routes.py:3548-3550` `select(Membership).where(Membership.user_id == user.user_id)).scalars().first()` (no ORDER BY, no chooser); same at `:3647-3649`.
- Scenario: a user who is RESEARCHER in the operator tenant and also self-signed-up (own CUSTOMER tenant) lands in whichever row Postgres returns first.
- Fix: present a tenant chooser or order deterministically and persist the choice.

### G-C-23 — `Subscription` is tenant-level; CUSTOMER members of one tenant share a single entitlement — **P1** (see Q7, G-C-28)

---

## Q6 — Event ingestion from the copier

### G-C-24 — Idempotency and ordering are correct for exact redelivery, but a reused `event_id` with a different payload permanently stalls the stream — and the default paper broker reuses ids after every restart — **P1**
- Classification: **silently-wrong**.
- Evidence:
  - Dedup: `integration_inbox.py:1019-1026` — same `event_id` + same `payload_hash` → return existing (no double booking); different hash → `EventIntegrityError` **before** the inbox row is inserted.
  - Ordering: `_next_expected_sequence` = max applied + 1 per (stream, generation) (`:207-238`); a missing sequence parks everything after it (`:1088-1101`).
  - Consequence: the rejected event's `export_sequence` is never applied, so every later event on that stream is gap-parked forever; the relay keeps resending it (`signal-copier/app/relay_worker.py:242-244` integrity_error left undelivered, `:268-272`). No incident is raised (`grep integrity|parked` in `app/services/incident.py` → none); `integration_status.py:122-140` only surfaces schema parks, so the page shows a growing "Unapplied" count with "compatible".
  - Trigger: `signal-copier/app/export_events.py:154` `event_id=f"execution-applied:{account.account_id}:{result.broker_order_id}"`; `signal-copier/app/brokers/paper.py:117,125` `broker_order_id=f"paper-{len(self.fills) + 1}"` on an in-memory list → after a restart `paper-1` is reissued for a new fill. Same failure for any broker that reports two FILLED results under one order id (partial fills) — UNVERIFIED which live adapters do.
- Scenario: restart the copier once; the next paper fill is `execution-applied:acct1:paper-1` with a new hash → integrity error → the owner's PLATFORM book stops updating permanently and silently.
- Fix: copier — include `signal_id`/a UUID in the fill's `event_id` and persist order ids; SPC — raise an `Incident` on `EventIntegrityError`/stalls and expose "stalled at sequence N" per stream.

### G-C-25 — `InboxEvent.routing_outcome` is one scalar per receipt, overwritten by every per-account outcome; the new allocation states export nothing — **P1**
- Classification: **semantic-miss**, **mock-only-tested**.
- Evidence:
  - `integration_inbox.py:527-535` `source_row.routing_outcome = payload.outcome` (last write wins); column is scalar (`models/integration_inbox.py:165`).
  - Copier emits one `ROUTING_ADMISSION_OUTCOME` **per account** on the same source stream with per-account ids (`signal-copier/app/export_events.py:327` `f"routing-outcome:{signal.id}:{account_id}"`; `engine.py:670,713,1324`); `_KNOWN_ROUTING_OUTCOMES` (`signal_platform_contracts/payloads.py:290-297`) has no "selected"/"eligible_not_selected"/"skipped".
  - Non-selected single-mode alternatives `continue` with no export (`engine.py:1082-1089`, `:899-901`); a skipped intent exports nothing (`:1336-1344`).
  - Tests cover exactly one outcome per receipt (`tests/test_integration_inbox.py:606-660`, `test_source_coverage.py:125-165`).
- Scenario: replicate-mode fan-out: account A `admitted_filled`, account B `disabled_by_settings` → source-coverage shows `disabled_by_settings` for a signal that was filled (or vice versa, by arrival order). Allocation flow: SOURCE_RECEIPT + one `admitted_filled` looks identical to "only one account was eligible" — the commercial side cannot infer "one of N selected".
- Fix: store outcomes as rows keyed `(receipt_event_id, account_id)`; add `eligible_not_selected`/`skipped` to the contract vocabulary and export them.

### G-C-26 — Any additive vocabulary change in the copier bricks every stream until a commercial deploy — **P1**
- Classification: **silently-unsupported**.
- Evidence: unknown `EventType` → `unimplemented_event_type` park that never advances the cursor (`integration_inbox.py:728-739`, acknowledged in-line); unknown `outcome`/payload field → pydantic `ValidationError` → `malformed_envelope` (`relay_routes.py:92-114`) → the copier treats it as an unrecognized status and resends forever (`relay_worker.py:268-272`) while the slot stays unapplied; a new `parked_reason` is classified "structural" and dropped from polling (`relay_worker.py:129-150`) but the gap remains on the commercial side.
- Scenario: the copier ships "allocation skipped" as a new outcome string → every source stream halts at that sequence with no alert.
- Fix: advance the cursor for non-economic unknown kinds (store-and-skip with an incident), or negotiate `schema_version` before export.

### G-C-27 — FEE correlation key has no account dimension — **P2 (latent)**
- Classification: **semantic-miss**.
- Evidence: `integration_inbox.py:137-141` `f"{broker}|{broker_order_id}"`, lookup `.first()` at `:481-487`; paper ids collide across accounts/restarts (`paper.py:117`). No FEE is emitted today (G-C-14).
- Fix: include `account_id` in the key.

---

## Q7 — Billing / subscriptions

### G-C-28 — Entitlement is tenant-level; one payer entitles every CUSTOMER in the tenant; nothing charges, and no entitlement depends on performance numbers — **P1**
- Classification: **silently-wrong** (entitlement scope); **ok** (no charge on wrong numbers).
- Evidence: `app/models/billing.py:63-82` no `user_id`; `customer_billing.py:5-12` "every CUSTOMER-role member of a tenant therefore sees the same, single shared subscription"; gates: `publication_admission.py:82-87`, `onboarding_progress.py:98-120` (`_current_subscription(tenant_id)` drives PAYMENT_COMPLETED/ENTITLEMENT_VERIFIED), `customer_managed_programs.py:130-133` ("Payment MET"). No Stripe SDK (`stripe_webhook.py:7-15`); fixture prices hidden until a real secret (`public_site.py:127-132`); `business_economics.py` structurally never reads the ledger (`tests/test_books_separation.py:37-52`). Q3-derived numbers are not used by any pricing/entitlement path.
- Scenario: in the operator tenant, one ACTIVE_PAID row makes every customer "entitled" and advances every customer's onboarding; in a self-signup tenant there is nothing to subscribe to (G-C-10).
- Fix: key `Subscription` by `(tenant_id, user_id)` and gate per customer.

---

## Q8 — PAMM/MAM

### G-C-29 — Simulation-only confirmed; no customer capital path; labelled — **ok**
- Classification: **ok**.
- Evidence: `allocate_fills`, `compute_simple_hwm_fee`, `units_for_cashflow` have no callers in `app/` (grep); `ManagedProgram` stores policy ids only, states DRAFT/SUBMITTED_FOR_REVIEW (`models/managed_program.py:44-66`); customer-visible states `frozenset()` (`customer_managed_programs.py:52`); UI: `cu15_managed_programs.html:45,52,57,67`, `ad15_managed_operations.html:31-56`, `ad14_managed_programs.html:93` all `UNSUPPORTED`/"not custody or a deposit action".

### G-C-30 — Stale UI copy on AD-14 — **P3**
- Classification: **doc**.
- Evidence: `app/templates/ad14_managed_programs.html:83` "no copy-mandate model exists in this build" — `CopyMandate` exists (`models/copy_mandate.py`).
- Fix: update the note or link to CU-09 drafts.

---

## Test/doc notes (P3)
- `tests/test_collective2_publisher.py:52-63` encode BUY-for-OPEN/ADD as the intended semantics (G-C-02).
- `tests/test_integration_inbox.py:606-660` encode one routing outcome per receipt (G-C-25).
- `tests/test_public_site.py:116-117` assert RLS-hidden fields under the superuser fixture (G-C-15).
- `docs/KNOWN_ISSUES.md` discloses the publisher/activation/PAMM gaps accurately but not G-C-12/13/24/25/26.
