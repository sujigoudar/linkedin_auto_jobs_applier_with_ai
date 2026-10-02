# Paste into Claude Code in the existing signal-copier workspace

Implement and validate the attached `WORKFLOW_SPECIFICATION.md` and all applicable requirements in `SCENARIO_CATALOG.json`. This is an in-place completion of my copier's full decision workflow, not a new app, a plan-only exercise, a smoke test, or a sample audit.

## Authority and current stack

Preserve the latest October 2 decisions: SQLite, the private existing HTML/JavaScript UI, actual direct adapters where qualified, single physical-account identity, and one canonical signal selecting ONE eligible account. Alternatives are not broadcast destinations. Do not reintroduce Supabase, Dash, SignalStack or other services as mandatory because an older workbook mentions them. Discover current repository/application root, current/deployed commit, effective policy, account bindings and legacy writers from the actual workspace and authorized read-only evidence. Do not assume that an earlier audit describes today's code or that a missing historical specification path must exist.

Do not raise live limits, turn on new routes/budgets, place validation trades, cancel real orders, move money, add infrastructure, purchase data/services or make new paid commitments. Do not treat historic small-live-test permission as authority for this coding task. Do all coding/tests in isolated non-live fixtures. No live credentials in development, CI, model input or logs. No live fault injection. Keep paper/live/historical replay isolated. Continue independent safe work without another design questionnaire; genuine external identity/entitlement/authorization blockers must be documented precisely, never guessed.

## First actions

Read this prompt, the full workflow specification, README and scenario catalog; then inspect only relevant existing implementation paths and discover actual runtime wiring. Inventory every source/parser, schema, asset, account binding, route, entry/exit path, sizing mode, budget/risk field, order type/state, adapter operation, scheduled job, UI/API route and release gate. Resolve effective configuration precedence with provenance. Older v3.4 numerical settings are reference recommendations, not automatically current live settings. Preserve current stricter limits and approved behavior.

Create `docs/workflow-contract/requirements_traceability.json` or reconcile the actual existing equivalent. Map each requirement/scenario to existing code, actual runtime caller, tests, fixtures and evidence. Status must distinguish NOT_IMPLEMENTED, IMPLEMENTED_NOT_WIRED, WIRED_NOT_TESTED, TESTED_SIMULATOR, TESTED_BROKER_PAPER, TESTED_OWNER_LIVE, BLOCKED and reviewed NOT_APPLICABLE. Do not label a feature implemented merely because a class, enum, method or UI card exists.

## Financial and routing work

Implement/reconcile PhysicalAccount, AccountBinding, CapabilityProfile, Portfolio, StrategySleeve, backed budgets, resource reservations, canonical opportunities, immutable account-specific OrderIntent, PositionAllocation, order families, protection plans and policy releases without destroying state. Account bindings never create additional capital. Every new entry selects one account; every later update/exit stays on its owning account. UNKNOWN submission retains resources and cannot retry/fail over without authoritative resolution.

Build one wired risk/capital engine. Separate equity, cash, borrowing/buying power, initial/maintenance collateral, notional, original planned loss, current mark-to-stop giveback, and stress loss. Enforce owner/account/portfolio/sleeve/provider/underlying/correlation limits together, with pending commitments and manual exposure. Avoid double subtracting reservations already represented in broker buying power. Atomic reserve plus durable outbox precedes all effects; broker calls occur outside short DB transactions. Fenced account writers and global budget transactions prevent concurrency over-allocation.

Implement exact instrument/quantity/currency/tick semantics, no minimum-one override, no option/FX/futures share-math shortcuts and no product substitution. Validate current per-account margin regime and rules; no universal legacy PDT or universal new-rule assumption. Existing approved margin support remains possible within current caps; margin capacity never increases the risk budget automatically.

Implement bounded fixed risk and qualified robust fractional-Kelly sizing by provider/analyst/strategy/asset/horizon/exit policy. Distinguish no-data, insufficient evidence and negative edge. Use full net lifecycle distributions with gaps and dependency-aware uncertainty, not parser confidence or advertised win rates. Preserve cold-start/no-improvement branches. Research outputs cannot auto-promote live.

Implement staged entry, pyramiding and runner capabilities in non-live modes under unchanged total caps. Adds are child intents of the same lifecycle, never fresh risk-budget resets. Preserve seed R; distinguish original risk, current profit giveback and gap stress; use confirmed protection only. Do not average down or ignore provider exits under a faithful-copy strategy. A derived runner beyond provider exit needs separate policy and attribution.

## Lifecycle and operations work

Implement full-message source grammars, compound/mixed-asset groups, exclusive alternatives, atomic recipes, revisions, origin deduplication and exact lifecycle matches. Unknown providers stay quarantine/shadow; ambiguous risk-reducing messages keep protection and create urgent incidents, not guessed closes.

Implement real partial-fill protection, desired-versus-confirmed stops, monotonic long floors/short ceilings, stop breaches, pending cancel/replace, late fills/corrections, duplicate delivery, original order-family recovery, scoped exits, partial quantities, manual activity, overlap, assignment/exercise/delivery, trading halts, expiry and durable deadlines. Entry pauses and loss halts cannot disable protection/exits/reconciliation. Never claim flat while an unresolved entry can reopen a lifecycle.

Wire all decision traces and controls into the actual UI/API. Every control needs backend enforcement and every state needs correct unknown/stale/degraded display. Test all actual routes and mobile/desktop journeys. Keep intake, broker, quotes, reconciliation, protection, writer ownership, deadlines and notification health separate. Preserve safe rendering, credential redaction and security boundaries.

## Verification — no sampling

The package has 292 named acceptance requirements and 9,228 generated finite input vectors. They are NOT claimed to have tested this application. The two restricted planning suites comprise 4,608 gate combinations and 4,500 synthetic sizing combinations; 120 event-order vectors need concrete independent lifecycle/reference-ledger oracles. Do not pass expected answers through to fake adapters, hardcode the sample output or count package helper passes as application evidence.

Build real adapters that invoke the actual wired application in disposable fixtures and collect outgoing payloads, broker effects or proven zero, persistent state, quantities/cash/risk, reasons and deadlines. Run all declared vectors and all applicable named cases. The supplied `run_contracts.py` deliberately fails unbound; `validate_evidence.py` deliberately fails the distributed NOT_RUN catalog. Keep these behaviors. Do not replace NOT_RUN with PASS without actual execution artifacts.

Expand finite models from the full code/configuration/route inventory. Execute every valid and invalid state/event/guard transition, all exact threshold-minus/equal/plus boundaries, and all causally permitted critical interleavings/crash boundaries. Use documented abstractions and invariants where state spaces need factoring; never quietly substitute random/pairwise sampling for critical exhaustive models. Property/fuzz/covering-array tests are supplemental. Every guard-removal mutation listed in the spec must cause a relevant mandatory test to fail unless equivalence is documented and reviewed.

Run original regression suites and new schema/unit/integration/persistence/replay/adapter/browser/chaos/mutation tests. Test no-data, denied, unsupported, stale, malformed, UNKNOWN and partial states as rigorously as successful trades. Real route qualification is specific to source/strategy/account/asset/adapter/recipe/policy/environment; unavailable external capabilities stay explicit blockers. All generic money/ownership/protection invariants apply to every route.

## Execution and final delivery

Work in dependency order: inventory/provenance -> schemas/identity -> budgets/reservations/sizing -> single-destination routing -> order effects/recovery -> protection/exits/overlap -> UI/operations -> Kelly/add/runner research -> full evidence. Parallelize independent code/test work in bounded worktrees with one integration/migration authority and no live writer. Continue internally actionable work without pausing for approval between phases. Retain regression failures and coherent commits.

Deliver changed files/commits and actual wiring; effective unchanged live settings and provenance; requirement/status counts; exact commands/environments/code/config/data hashes; executed/pass/fail/not-run/blocked counts; killed/surviving mutations; finite-domain and event-sequence coverage; actual UI/API journey evidence; remaining financial risks and external blockers. Separate software correctness from profitability evidence, and simulator from actual broker behavior. Do not claim complete or live-ready from test counts alone, and do not activate anything as part of this task.
