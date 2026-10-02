# Whole-solution gap analysis

Date: 2026-10-02. Scope: `signal-copier` (private execution: ingestion, routing,
sizing, adapters, lifecycle, reconciliation, accounting, console, operations),
`signal_platform_contracts`, and `signal-portfolio-commercial` (customer and
publisher platform).

Method: seven read-only domain audits read the code — not the docs — and wrote
the raw findings in [`raw/`](raw/) (166 findings, every one cited to file:line).
The highest-severity findings were then re-verified by running the real engine
against the paper broker and a fresh SQLite database. Labels used below:

- **VERIFIED** — reproduced on the real engine, or the cited lines were read
  directly, by the author of this report.
- **PROBED** — reproduced by a domain audit's runtime probe; the verbatim probe
  output is in the raw file (`raw/probes/` holds the scripts).
- **REPORTED** — cited to file:line by a domain audit, not independently re-run.

Severity: **P0** can create unintended exposure, duplicate orders, an
unintended short, or an untracked real position; **P1** silently wrong
financial behavior or number; **P2** missing capability the owner would
reasonably assume exists; **P3** docs/tests.

---

## 1. Verdict

**The system is not safe to connect to real money, on any adapter, in its
current state.** This is not a matter of one or two bugs. The broadcast defect
(one signal executed on every eligible account) was the first instance found
of a pattern that runs through the whole execution path:

> The code does exactly what its docstrings, README and tests say.
> What they say is not what a trader following a signal provider needs.

The same shape appears in at least 27 distinct P0 findings. The ones that
would hurt first, each reproduced on the real engine today:

| | What happens | Where |
|---|---|---|
| 1 | A provider's **"sell"** on a follower who is long is executed as a *new short-side entry* sized from the provider's number, not as an exit. Long 5, provider "sell 10" → follower is **short 5**. "Sell" with no position opens a short. | `engine.py` entry path; `text_parser.py:35-38` |
| 2 | A signal with **no quantity** trades **1.0 unit** of whatever the instrument is: 1 share, 1 BTC (~$65k), 1 lot (100k EUR), 1 ES contract (~$250k). | `risk.py:17-18`, pinned by `test_risk_sizing.py` |
| 3 | A provider who **edits** their message ("buy 1.0" → "buy 1.5") causes a **second full entry**. Follower ends up long 2.5. | `test_telegram_cross_collector_dedup.py:106-133` asserts two orders |
| 4 | **Limit and stop entry types** are parsed, stored and exported, and **every adapter sends a market order**. A "buy limit 150" alert executes at the current price; a "buy stop 155" breakout fills before the breakout. | no adapter reads `entry_order_type` |
| 5 | A managed-lifecycle **CLOSE is journaled with `side='close'`**, which every replay skips. After one such close the account's P&L says the position is still open, the capital gate **rejects every new entry on a flat account**, and the strategy ceiling counts phantom exposure forever. | `engine.py` managed branch persists `side=signal.side`; reproduced |
| 6 | On a plain account, the **native bracket's stop/target legs are tracked by nobody**. The stop fills at the venue, local state still says long, and the next CLOSE sells again → short (IBKR/MT5) or is rejected forever (Alpaca). | `reconciliation.py` polls only the parent order |
| 7 | A CLOSE that carries SL/TP (every TradingView template does) is sent as a **bracket whose child legs are the opposite side** → after flat, a resting child leg opens a new position. | `engine.py:2200-2201` copies SL/TP onto the close |
| 8 | A reconciled **FILLED** whose status poll carries no quantity is applied as a **zero** position delta on six adapters → real position at the broker, zero locally, unprotected, unexitable by signal. | `reconciliation.py:482`; Tastytrade, Tradovate, TradeStation, Schwab, Robinhood, OANDA |
| 9 | A **managed short** is destroyed by the first reconcile pass (sign bug): lifecycle closed, a fabricated BUY of 2× exported, the real short left at the broker with its stop orphaned. | `reconciliation.py:320-323` |
| 10 | Flipping `managed_lifecycle` on an account (or via a provider override, which the TR-12 form does on every save) **with a position open** routes the next close through the wrong branch: orphaned resting stop → later short, or "no open position to close" → stranded. | `main.py:2465-2473` guards only `broker` |

Everything that *is* in place to prevent loss — the capital gates, the
qualification ladder, the command ledger, the close arbiter, the reconciler —
is real and mostly correct for the cases it was built for. The failures are in
what those mechanisms are fed and what they are not asked about.

**Counts.** 166 findings: 27 P0, 61 P1, 52 P2, 16 P3, 10 confirmations ("ok").
Of the 15 broker adapters, 2 are real (Alpaca, ccxt) plus the paper simulator,
9 are partial, 3 are fire-and-forget relays, and 5 cannot route any entry at
all in this build. Every external adapter is tested only against mocks.

---

## 2. The pattern, and why it survived

Six mechanisms let these defects pass a 3,100-test suite with mutation testing,
fuzzing and property tests:

1. **Tests pin the wrong semantics.** `test_risk_sizing.py` asserts the 1.0
   default; `test_position_tracking.py` asserts "sell then close flattens a
   short"; `test_telegram_cross_collector_dedup.py` asserts an edit places two
   orders; `test_tastytrade_broker.py` asserts "Sell to Open"; `test_trk23…`
   asserts the `side='close'` row. Each is a correct test of an incorrect
   intention.
2. **The paper broker accepts anything.** Fractional shares, sub-minimum
   crypto, shorts in a cash account, fills at price 0.0 (`paper.py:93`), cash
   that rises on a short sale. Every engine and lifecycle test passes against
   it; none of those behaviors survive contact with a venue.
3. **Mock-only adapters.** 289 mock references across 20 adapter test files,
   zero venue-gated tests. Status tokens for Tradovate, Schwab and Robinhood
   are self-declared unverified in the adapters' own docstrings.
4. **Disclosed-gap comments treated as closure.** `engine.py:2652-2663`
   calls zero-fraction targets "a known, disclosed gap"; `capital_allocator.py`
   discloses no currency conversion; `parser_tooling.py` is "intentionally NOT
   WIRED". Disclosure in a docstring is not a control.
5. **Track-scoped delivery.** Each track implemented its slice to its own
   acceptance test (E04 margin detector, E09 liquidation, E10 fees, Track 41
   edit ledgering) without the end-to-end check that the slice is reachable
   from a real signal with a real store. The margin detector is called with
   three `None`s; the loss limiter reads two store members that do not exist;
   the fee column is populated only by the paper broker.
6. **Side vocabulary.** `Side` has three values and `sell == short`. Most of
   the interpretation defects (1, 3, 7 above, plus trims, status messages,
   article "we are selling") descend from that one modelling choice.

---

## 3. P0 findings by lifecycle stage

IDs refer to the raw audit files. "Fix" is the minimal correct change, not a
design.

### 3.1 Interpreting the signal

| ID | Finding | Evidence | Fix |
|---|---|---|---|
| A-01 | "sell"/"short" is a new short-side entry, never an exit; bypasses every close gate (reconciliation, close lock, provider-ownership). Managed accounts reject it instead (position stays open, exit lost). | **VERIFIED** (engine run) | Add explicit exit intent: a SELL on an account holding a same-symbol long resolves through the close path (reduce by provider quantity, full close if none); opening a short requires `allow_short` and an explicit `short`. |
| A-02 | Edited entry → second live entry; edited stop → never applied. | **VERIFIED** (test pins it) | On EDIT, resolve the original by `(channel, message_id)`; amend a pending entry or a stop/target; never a fresh entry. |
| A-04 | "SELL half AAPL" → entry on symbol `HALF`; "BUY TO OPEN AAPL 150C" → symbol `TO`; "BUY 10 AAPL" → symbol `10`. No symbol validation before routing. | **PROBED** | Validate the symbol token; parse `half/all/N%` as a reduce fraction. |
| A-05 | "BUY AAPL 150C 1/17 @ 2.50" → equity buy of **150 shares** of AAPL (~$22k). | **PROBED** | Recognise strike/expiry tokens; MISSING_DATA unless a full contract resolves. |
| B-02 | No quantity → 1.0 unit × multiplier, any asset class. | **VERIFIED** (engine run: 1 BTC) | Reject an entry with no quantity unless the account has `fixed_quantity` or risk-based sizing; remove the default. |
| B-03 | Quantity has no unit; lots/units/shares/contracts all the same float; contract multipliers ignored by every notional and risk computation ("10 AAPL 200C @ 2.50" gates on $25 of notional). | REPORTED | Normalise to venue units per contract spec; multiply notional/risk by the multiplier; refuse FX/option/future signals with no spec. |
| C-01 | `entry_order_type` limit/stop never sent; 14 adapters hard-code market. | **VERIFIED** (grep) | Honour or reject at admission. |

### 3.2 Submitting and tracking the order

| ID | Finding | Evidence | Fix |
|---|---|---|---|
| C-02 | Plain CLOSE carrying SL/TP is sent as a bracket with opposite-side child legs. | **VERIFIED** (`engine.py:2200-2201`) | Strip SL/TP/targets in `_resolve_close`. |
| C-03 | Tastytrade sends "…to Open" for every order; a close is an opening order. | REPORTED (docstring admits) | Thread close intent to adapters; emit "to Close". |
| C-04 | NinjaTrader sends `sentiment: short` for a close (never `flat`); MT5/MetaApi send a market sell with no position ticket (hedging accounts open an opposing position). | REPORTED | Emit `flat`; close by position ticket. |
| C-05 | OPTION declared on Tastytrade/TradeStation but only equity legs are built; an option route can be release-approved. | REPORTED | Remove the declarations until option legs exist. |
| C-06 / D-04 | Reconciler applies FILLED-without-quantity as a zero delta (six adapters). The engine's own sync path uses the requested quantity for the same case. | **VERIFIED** (`reconciliation.py:482`) | Fall back to `requested_quantity`; read the venue's filled-size field. |
| C-07 | Native-bracket exits invisible on adapters without readback (IBKR, MT5); `exclusive_writer_qualified` then lets a CLOSE open a short. | REPORTED | Refuse the flag for bracket adapters without readback, or implement readback and child tracking. |
| D-01 | Bracket child legs have no `orders` row and are never polled; local `positions` never learns the stop filled. | REPORTED (consistent with C-06/C-07 reads) | Persist child-leg ids as orders rows; periodic position readback for plain accounts. |

### 3.3 Protecting and exiting

| ID | Finding | Evidence | Fix |
|---|---|---|---|
| D-02 | Any broker-position deficit on a managed account is attributed to the stop; the resting venue stop is never cancelled or resized. Manual sale of 40 → stop still 100 → short 40 on trigger. | REPORTED | Poll the stop id; resize/cancel before applying a correction; never delete lifecycle state while a stop id is live. |
| D-03 | Managed short: `deficit = owned - broker_owned` with signed readback → 10 − (−10) = 20 → lifecycle closed, BUY 20 fabricated and exported, real short orphaned. | **VERIFIED** (`reconciliation.py:320-323`) | Sign `broker_owned` by plan side; add a short-side regression. |
| D-05 | CLOSE quantity is ignored; every CLOSE is a full flatten ("close BTC 5" sells 10). | **VERIFIED** (`_resolve_close`: `quantity = abs(position)`) | Honour the quantity (capped at owned) or reject a CLOSE that carries one. |
| D-06 | Multi-target signals become zero-fraction targets on managed accounts: **no take-profit ever executes**; plain accounts keep TP1 only. | REPORTED (engine comment calls it disclosed) | Default per-level sizing or reject targets without sizing. |
| D-07 / F-01 | `managed_lifecycle` flip with exposure (account flag, provider/analyst override, override deletion, TR-12 save) → wrong close branch → orphaned stop or stranded position. | **VERIFIED** (`main.py:2465-2473` guards only `broker`) | Choose the exit path from the existence of an open lifecycle, not a flag; refuse the flip while exposed. |
| D-08 | Managed exit returning ERROR (timeout after the venue filled) is treated as "0 filled" and a full-size stop is re-armed on a flat position. | REPORTED | Treat ERROR like the raised-exception branch (pending exit, resolve by readback). |
| D-09 | Ambiguous stop placement retried blind → duplicate resting stops (also across a crash); both fill → short. | REPORTED (P1 with P0 outcome) | Check the last attempted id via the ledger before re-placing; add open-orders readback. |

### 3.4 Accounting, scoring and the capital gate

| ID | Finding | Evidence | Fix |
|---|---|---|---|
| E-01 | Managed provider CLOSE journaled as `side='close'` → P&L, episodes, provider score and the capital gate all drop the exit; the account is locked out of new entries on a flat book. | **VERIFIED** (reproduced end to end) | **Fixed in this PR**: the row carries the lifecycle's resolved exit side (`tests/test_alloc11_journal_and_ledger_fixes.py`). Existing rows are not backfilled. |
| E-02 | Partial fill then cancel: 30 shares applied to `positions`, status `rejected` → excluded from every analytics query and the export. The later sell is replayed as opening a short. | PROBED | Select replays on `filled_quantity > 0`, not status; export the partial. |
| E-03 | Async-resolved target/time exits journaled with `filled_price=NULL` although the broker reported one → symbol poisons P&L and the gate; fill never exported. | PROBED | Thread `result.filled_price` through `resolve_pending_exit`. |
| E-04 | The per-source filter behind the new strategy ceiling cannot see lifecycle-initiated exits (`source='lifecycle_manager'`) or manual closes → **strategy exposure never decreases after a stop-out**; the strategy eventually locks itself out. | **VERIFIED** (strategy notional 500 on a flat account) | **Fixed in this PR**: fills are attributed to the entry's source via `family_id` (stop-out and manual-close regressions in `test_alloc11`). |
| E-06 | Provider win rate / promotion use a survivorship denominator: open, unknown-price and all plain-account episodes are excluded. Ten synchronous wins + 25 losses exited via CLOSE or async stops → `win_rate 1.0`, `promote`. | REPORTED (consequence of E-01/E-03) | Gate promotion on zero excluded episodes; report coverage. |

---

## 4. P1 findings (silently wrong), by domain

Compact index; full text in the raw files.

**Interpretation (A):** A-03 Telegram channel-post edits collapse onto the
original and are silently ignored (UNVERIFIED); A-07 Rithmic source exits become
SELL entries, MT5/NinjaTrader partial exits become full closes; A-08 `%`/`$`/
ranges misread as quantities ("BUY AAPL 150-152" buys 150 shares); A-09 no chase
guard, stale provider price used for gating, stale "sell" exits rejected as
stale entries; A-10 stop/target/add/trim/cancel classifier exists and is
deliberately unwired; A-11 deleted or cancelled messages never cancel a resting
entry ("cancel" is a negation word); A-12 "hold for swing" drops a real entry
while "I'm long AAPL" places one; A-13 asset class inferred from symbol shape
("BUY BTC 0.1" → EQUITY → rejected on a ccxt route; "BUY ES 1" → equity broker);
A-14 priceless alerts and re-posts beyond 15 min are not correlated; A-19
non-parsed live messages are dropped at debug level with no record; A-21
article "we are selling X" becomes a live SELL entry.

**Sizing/risk (B):** B-01 no equity- or risk-based sizing anywhere;
`risk_percent_of_equity` only rejects; B-04 rounding/lot/min-notional enforced
by two adapters only; B-05 definite 4xx rejections classified ambiguous →
reservation held until an operator resolves it (made more consequential by
ALLOC-05); B-06 all gating prices from the message, never a live quote; B-07
buying-power check fails open, real figures from Alpaca and paper only, paper
cash rises on a short; B-08 daily-loss/min-equity breakers unloadable from DB,
YAML or API, `daily_pnl` never written, no latch, no weekly halt, and the global
default now rejects every entry; B-10 ceilings sum raw numbers across
currencies; B-11 no leverage cap (Alpaca buying power implicitly permits 2–4×);
B-13 qualification keyed without venue environment — a paper-qualified route
stays approved after the endpoint is switched live; B-14 no `allow_short`.

**Adapters (C):** C-08 Alpaca cancel race leaves a stop believed-confirmed;
C-09 ccxt treats cancel ack as done; C-10 five adapters can never route an
entry (undocumented); C-11 Schwab/Robinhood map every ≥400 incl. 5xx to
REJECTED-confirmed (capital released, position possibly real); C-12 MT5 maps
timeout/requote to REJECTED; C-13 TradeStation partial-then-cancel reported as
FILLED; C-14 IBKR order status is process-memory only; C-15 ccxt has no
`get_order_status`; C-16 ccxt spot readback returns 0.0; C-17 env flip takes a
sandbox-qualified route live; C-18 IBKR uses the local id as the IB account code.

**Lifecycle (D):** D-10 stop fills detected only by position deficit (none on
ccxt spot); D-11 `reduce_fraction` is of planned, not owned, quantity; no
quantization on exits; D-12 trailing/time exits unreachable from any signal,
targets are process-bound and fed by an unchecked REST poll; D-13 no
production path *acted on* unresolved command-ledger rows (an operator API now
exists, this PR; no reconciler pass yet); D-14 lost-response managed entry blocks the symbol permanently;
D-15 partial coverage deficit never retried; D-16 crash between fill and stop
persist orphans the position; D-17 ccxt cancel needs the symbol, kept in memory
only → every managed exit refused after a restart.

**Accounting (E):** E-05 deficit-inferred stop fills journaled and exported
with a proxy price; E-07 reconciled fills take the poll time as `executed_at`
(latency inflated, replay order can flip); E-08 synthetic exit signals dilute
latency; E-11 no account currency anywhere, export guesses USD; E-12 unpriced
open symbols fold 0.0 into drawdown/correlation series; E-14 commercial
`routing_outcome` is last-writer-wins; E-15 four fill classes never reach the
commercial book; E-16 paper fills managed exits at 0.0 and exports it; E-17
both dashboards render the deprecated scorecard; E-19 `/capital-allocation`
"deployed" inherits all of the above.

**Operations (F):** F-02 (= B-08); F-03 "Pause new entries" and TR-08 POST a
partial account and **clobber** `exclusive_writer_qualified`,
`risk_percent_of_equity`, `qualification_level`; F-04 every upgraded DB
reports schema mismatch forever, TR-16 blocks the runbook and recommends a
command that fails; F-05 unresolved ledger rows have no API, UI or reconciler
pass, and `promote_cli` queries a column that does not exist; F-06 **nothing
notifies a human** for a protection deficit, halt, unknown submission or skipped
allocation; F-07 rule deletion strands provider exits; F-08 health cannot see a
full disk.

**Commercial (G):** G-C-02 `PublicationIntent` has no side — every OPEN/ADD is
published as BUY; G-C-10 self-signup customers can never select a product
(copy journey unreachable); G-C-12 PLATFORM book has no account dimension — a
fan-out shows as N× position; G-C-13 paper/live/simulated is one process-wide
label, neither enforced nor displayed; G-C-17 CU-06 would drop a disconnected
connection's losses; G-C-19 fit-simulator and public catalog are not
rights-gated; G-C-23/28 entitlement is tenant-level; G-C-24 a reused `event_id`
with a different payload permanently stalls a stream — **and the paper broker
reuses order ids after every restart**; G-C-25 routing outcome overwritten per
account; G-C-26 any additive vocabulary change bricks every stream.

P2 and P3 findings (52 + 16) are indexed in the raw files.

---

## 5. Defects in the allocation work shipped this week (self-audit)

The ALLOC-01…10 commits fixed the broadcast and added strategy ceilings and the
unknown-submission hold. The audits found four problems with that work:

| ID | Finding | Status |
|---|---|---|
| G-07 | Rule precedence is insertion order, so a catch-all rule listed first beats a more specific symbol-filtered rule listed later. `test_tr11_routing_simulator` now asserts exactly this. | Open. Needs explicit priority or most-specific-first. |
| E-04 | The strategy ceiling's per-source replay cannot see lifecycle or manual exits; strategy notional never decreases after a stop-out. | **Fixed in this PR** (attribute by `family_id`). |
| B-05 / C-20 | ALLOC-05 holds the reservation for any ERROR result. Alpaca, ccxt and Tradovate return ERROR for definite 4xx rejections (insufficient funds, invalid order), so a plain venue rejection now locks capital until an operator resolves it. | **Operator path added in this PR**: `GET /command-ledger/unresolved`, `POST /command-ledger/resolve` (only `not_placed` with evidence releases). Adapter classification (definite reject vs ambiguous) still open; until it lands, every venue rejection on those adapters needs an operator resolution. |
| F-10 | Allocation intents left `claimed`/`selected` by a crash are resolved only on redelivery; no startup sweep; no console for intents or budgets. | Open. |

---

## 6. Adapter reality

From `raw/C_broker_adapters.md` (built from code, not the catalog):

| Verdict | Adapters |
|---|---|
| Real (submit, status, cancel, position, balance) | **Alpaca**; ccxt (cancel-ack trusted, no status polling, spot readback wrong); paper simulator |
| Partial | IBKR (status in-memory only, no cancel/position/balance), Tastytrade (wrong intent, OPTION unconstructable), Tradovate, OANDA, TradeStation (OPTION/FUTURE unverified), Schwab (live-only, ≥400→rejected), Robinhood (unofficial, live-only), MT5, MetaApi |
| Fire-and-forget (PENDING on HTTP accept, no venue evidence) | NinjaTrader, Rithmic, SignalStack |
| Structurally unable to route any entry (cannot reach `release_approved`) | NinjaTrader, Rithmic, SignalStack, MT5, MetaApi |

No adapter sends a client order id, so a lost response can never be
deduplicated at the venue.

---

## 7. What is sound

So the report is read correctly: these were checked and hold.

- Auth, CSRF, cookie flags, constant-time secret comparison, structlog
  redaction, XSS escaping, owner gating on every financial route (F-19).
- Writer-lease fencing on every broker-write path (D-20; gaps are DB-side).
- EXE-07 lifecycle-deletion guard; account delete and broker change refused
  under exposure (D-24).
- Close arbiter covers every managed exit kind (D-19; bypasses are D-02/D-07).
- Reconciliation is "broker wins", single-shot release, never invents plain
  positions (D-25).
- The commercial platform has no execution authority, no credentials, no
  SQLite access; PAMM/MAM is simulation-only and labelled; RLS is exercised
  under a real role; no fabricated public track record (G-C-01, 11, 21, 29).
- ALLOC-01…10: one signal → one account, cross-process claim and admission,
  crash-boundary resume, hold on ambiguous submission.

---

## 8. Remediation order (dependency gates, not a schedule)

Each gate names what it unblocks. Nothing below is a live-release decision.

**R0 — Stop the bleeding (small, mechanical).** Done in this PR: E-01 exit
side on managed CLOSE; E-04 strategy attribution; D-13/F-05 operator endpoints
for unresolved ledger rows. Still open:
C-02 strip SL/TP on close; D-03 sign the readback; D-04/C-06 requested-quantity
fallback; D-07/F-01 refuse `managed_lifecycle` flip under exposure; F-03 PATCH
semantics on `/accounts`; B-02 remove the 1.0 default.

**R1 — Intent model.** Replace `Side` with an explicit instruction model
(`entry_long`, `entry_short`, `exit`, `reduce(fraction|qty)`, `stop_update`,
`target_update`, `cancel`, `add`) carried from parser to adapter. Unblocks A-01,
A-02, A-04, A-10, A-11, D-05, C-03, C-04, G-C-02, G-C-18. This is the single
largest lever; most P0s in sections 3.1 and 3.2 are consequences of its absence.

**R2 — Sizing model.** Quantity with a unit and contract spec; risk-based
sizing as the default for provider signals; venue quantization; reject on
unsized entries. Unblocks B-01, B-03, B-04, D-11, E-18.

**R3 — Order-family tracking.** Persist bracket child legs; poll stop ids;
venue client order ids; adapter status classification (definite reject vs
ambiguous) with venue-verified tokens. Unblocks D-01, D-02, D-09, D-10, C-07,
C-08, C-09, C-11, C-12, C-15, C-19, C-20, B-05.

**R4 — Journal correctness.** One `orders` row per confirmed delta with real
side, price, time and fee; replay on quantity not status; family attribution
for every exit; per-currency reporting. Unblocks E-02, E-03, E-05, E-07, E-09,
E-11, E-15, E-16, E-17, E-19, G-C-12, G-C-14, G-C-24.

**R5 — Risk controls that exist.** Wire loss limits end to end with a real
daily P&L source and a persisted latch; feed the margin detector from balances;
leverage cap; basis currency; qualification keyed by venue environment; human
notification path. Unblocks B-07, B-08, B-09, B-10, B-11, B-13, F-06, F-02.

**R6 — Adapter qualification.** Sandbox-gated venue tests per adapter; drop
OPTION/FUTURE declarations until legs are built; document the five
entry-incapable adapters. Unblocks C-05, C-10, C-27.

**R7 — Commercial seam.** Account dimension on ledger entries; per-account
routing outcomes; evidence class per account; cursor advance on unknown
non-economic kinds; customer tenancy model. Unblocks G-C-10, 12, 13, 25, 26.

Each gate's acceptance is a regression that runs the scenario through the real
engine, the real store and (for R3/R6) a sandbox venue — never a mocked adapter
or the paper broker alone.

---

## 9. Release posture

| Capability | State |
|---|---|
| Paper trading on the paper simulator | Usable for routing/allocation rehearsal only. The simulator's permissiveness (section 2.2) means a green paper run is not evidence of venue safety. |
| Any live adapter, any asset class | **Not releasable.** Sections 3.1–3.4 apply to every adapter. |
| Provider scorecards / promotion | Not trustworthy until E-01, E-03, E-06 are fixed. |
| Commercial customer copy | Unreachable end to end (G-C-10); nothing executes (G-C-08). |
| Commercial owner performance view | Numbers are real fills but collapse accounts and environments (G-C-12, G-C-13); net never available (G-C-14). |

---

## 10. Index

Raw audits (file:line evidence, probe output, per-question answers):

- [`raw/A_signal_interpretation.md`](raw/A_signal_interpretation.md) — A-01…A-21, instruction table
- [`raw/B_sizing_and_risk.md`](raw/B_sizing_and_risk.md) — B-01…B-19
- [`raw/C_broker_adapters.md`](raw/C_broker_adapters.md) — C-01…C-28, per-adapter matrix
- [`raw/D_lifecycle_protection_reconciliation.md`](raw/D_lifecycle_protection_reconciliation.md) — D-01…D-26
- [`raw/E_accounting_analytics_export.md`](raw/E_accounting_analytics_export.md) — E-01…E-23
- [`raw/F_operations_config_security_ui.md`](raw/F_operations_config_security_ui.md) — F-01…F-19, per-screen truthfulness table
- [`raw/G_commercial_platform.md`](raw/G_commercial_platform.md) — G-C-01…G-C-30
- [`raw/probes/`](raw/probes/) — the scripts behind every PROBED label

Related: [`../design/PORTFOLIO_ALLOCATION.md`](../design/PORTFOLIO_ALLOCATION.md),
[`../design/ALLOCATION_DECISION_LEDGER.md`](../design/ALLOCATION_DECISION_LEDGER.md),
[`../testing/ALLOCATION_TRACEABILITY.md`](../testing/ALLOCATION_TRACEABILITY.md).
