# Audit A — Signal interpretation (signal-copier)

Scope: what a provider message can express vs. what `Signal` represents vs. what `app/engine.py` actually
executes. Read-only; no repo files edited, no pytest run. Evidence is from reading the code plus two
plain-Python probes (text parser; engine + PaperBroker + SQLite) whose verbatim output is in the appendix.
Line numbers are from `/home/user/linkedin_auto_jobs_applier_with_ai/signal-copier/` at the time of the audit.

Severity: P0 = unintended exposure/loss or duplicate orders; P1 = silently wrong financial behaviour;
P2 = missing capability the owner would expect; P3 = doc/test/UX.
Classification: semantic-miss | disconnected | mock-only-tested | silently-unsupported | missing | ok.
"REGISTERED: X-nn" = the owner's own `docs/testing/ALLOCATION_TRACEABILITY.yaml` already lists this as a gap
(it is still live in the code). Everything else is new.

---

## 0. Headline

The side vocabulary is the root defect. `Side` has three values (`buy`/`sell`/`close`) and the text parser
aliases `sell` and `short` to the same value (`app/sources/text_parser.py:32-39`). The engine treats everything
that is not `CLOSE` as a **new entry** sized by account policy (`app/engine.py:698`, `:1014`). So the single
most common exit phrasing a human provider uses — "SELL AAPL" — is executed as a fresh, multiplier-sized
opposite order that bypasses every close safety gate, and opens a short when the account is flat. Edits,
trims, stop moves, cancels and "don't chase" caps either become new entries, are mis-parsed into another
instrument, or are dropped at `logger.debug` with no persisted record. Several of these are already in the
owner's traceability registry as gaps (B-11, B-13, B-15, B-16, H-10, J-12, L-03) but remain fully live.

---

## 1. Instruction table (Q1)

| # | Instruction a provider expresses | Example | Parsed? | Represented on `Signal`? | Executed by engine? | What actually happens if it arrives |
|---|---|---|---|---|---|---|
| 1 | Entry long | `BUY AAPL 10 @ 150 SL 145 TP 160` | yes | `Side.BUY`, qty, price, stop, tp | yes (plain: market order, bracket only if broker `supports_native_bracket`; managed: `PositionPlan`) | OK. Caveats: price is informational only (market order), no quantity → 1 unit (A-08). |
| 2 | Entry short | `SHORT TSLA 10 @ 250 SL 260` | yes → `Side.SELL` | same value as "sell" (indistinguishable) | yes, as an entry | Opens a short (probe: flat account → pos −5). Tastytrade sends "Sell to Open". |
| 3 | Full exit via `close`/`exit` | `CLOSE AAPL`, `exit AAPL`, webhook `"side":"close"` | yes → `Side.CLOSE` | yes | yes: resolved against tracked position with reconciliation + provider-ownership gates (`engine.py:2472-2593`, `:3015-3262`) | OK (plain accounts need broker readback or `exclusive_writer_qualified`). |
| 4 | **Full exit phrased "sell"** | `SELL AAPL`, `SELL AAPL at market`, webhook `"side":"sell"`, Rithmic SELL fill | yes → `Side.SELL` | identical to a short entry | **executed as a NEW ENTRY** sized by multiplier/`fixed_quantity`; plain-close reconciliation, `claim_close` lock and provider-ownership gate all bypassed; managed account → REJECTED | Long 10, "SELL" with no qty → sells 1, leaves 9 (probe). Flat → opens short. Managed → nothing closes. **A-01** |
| 5 | Partial trim | `SELL half AAPL`, `Close half ETHUSDT`, `Trim 50% TSLA` | "half" parsed as the **symbol** `HALF`; "Trim" → NO_MATCH | no fraction/quantity-modifier field | `SELL HALF` → entry on symbol HALF; `CLOSE HALF` → "no open position to close"; trim → dropped | Position never reduced; a bogus instrument may be submitted. **A-04, A-10** (REGISTERED B-15) |
| 6 | Scale-in / add | `Add to AAPL here 5 more` | NO_MATCH (parser_tooling labels ADD but is unwired) | no (`is_add = False` hard-coded in freshness) | dropped | "BUY AAPL 5" instead: plain adds; managed → REJECTED (EXE-09 one lifecycle per symbol). **A-10** |
| 7 | Stop move | `Move stop to 140 on AAPL`, `AAPL SL to breakeven`, or editing the original message | NO_MATCH; edit → new `revision_id` | `SourceEventKind.STOP_UPDATE` exists but no adapter emits it | dropped; edited original → plain: 2nd entry (or rejected), managed: REJECTED; stop never changes | **A-02, A-10** (REGISTERED B-11, B-13) |
| 8 | Target move | `Move TP to 160` | NO_MATCH | `TARGET_UPDATE` exists, never emitted | dropped | **A-10** |
| 9 | Cancel pending entry | `Cancel the AAPL order`, `Cancel BUY AAPL 10`, message deleted | NO_MATCH / IGNORED ("cancel" is a negation word) / DELETE `SourceEvent` | `CANCEL` kind never emitted; `DELETE` emitted by user collectors only | nothing cancels; DELETE → export-outbox row only | Resting entry stays working. **A-11** (REGISTERED B-16) |
| 10 | "Don't chase above X" | `BUY AAPL 10 don't chase above 152` | IGNORED (whole message) | no field | entry dropped entirely; no price-distance gate exists anywhere | **A-09, A-12** |
| 11 | Limit entry | `BUY AAPL limit 150`, `BUY AAPL @ 150 limit`, webhook `entry_order_type:"limit"` | text: price **lost** / price=150 with no type; webhook: parsed | `EntryOrderType` field | **not executed**: every adapter sends market; `entry_order_type` consumed only by `export_events.py` | Fills at market regardless of limit. **A-09** (REGISTERED H-10) |
| 12 | Entry range | `BUY AAPL 150-152`, webhook `price_low/high` | text: **quantity=150**; `@ 150-152` → price 150, range lost; webhook: parsed | `price_low/high` | exported only | 150 shares bought. **A-08** |
| 13 | Time-based exit | `BUY AAPL 10 @ 150, exit by Friday` | AMBIGUOUS (two side words) → dropped | `entry_expiration`/`management_horizon` never set by any adapter; `PositionPlan.time_exit` never set from a Signal | no | Entry itself is dropped. **A-17** (REGISTERED H-09) |
| 14 | Options, single leg | `BUY AAPL 150C 1/17` / `BTO AAPL 150C` / `BUY AAPL260918C00200000 1` | **EQUITY buy of 150 shares** / NO_MATCH / OPTION (OCC only) | `Signal.option` set only by the article classifier | no adapter reads `Signal.option`; Tastytrade sends an Equity leg | Stock bought instead of the contract. **A-05, A-06** (REGISTERED L-03) |
| 15 | Options, multi-leg | `AAPL 150/160 call spread` | NO_MATCH (text); article classifier refuses unresolved spreads | none | no | Dropped (safe), unrecorded. silently-unsupported (REGISTERED L-07) |
| 16 | Multiple targets | `TP1 155 TP2 160 TP3 170` | yes | `targets` | managed: one SELL target per level, but a level with `quantity` and no `fraction` is a 0-fraction no-op; plain: only TP1 goes in the bracket | TP2/TP3 silently dropped on plain accounts. **A-16** |
| 17 | Quantity as %, $, lots | `2% risk`, `$500`, `0.5 lots` | 2.0 units / None → 1 unit / 0.5 with unit discarded | unitless float | executed in each broker's native unit (MT5 lots, OANDA units, Alpaca shares) | **A-08** (REGISTERED G-03, M-08) |
| 18 | Status / commentary | `I'm long AAPL`, `going long AAPL`, `Still holding AAPL long from 150` | PARSED BUY / PARSED BUY / IGNORED | — | **an entry is placed for a status statement** | **A-12** |
| 19 | Result messages | `TP1 hit on AAPL`, `Stopped out of AAPL`, `Closed AAPL +5%` | NO_MATCH (parser_tooling RESULT, unwired) | — | dropped (correct) but not recorded | **A-19** |
| 20 | Compound | `Buy AAPL 10 @150 and sell half at 155` | AMBIGUOUS | — | dropped, not recorded | **A-19** |

---

## 2. Findings

### A-01 — Provider "SELL" is a new short-side entry, never an exit (P0, semantic-miss)
Evidence:
- `app/sources/text_parser.py:32-39` — `"sell": Side.SELL,` / `"short": Side.SELL,` (same value; "exit"/"close" → `Side.CLOSE`).
- `app/engine.py:698` — `order_purpose = "close" if signal.side == Side.CLOSE else "entry"`; `:720`, `:748`, `:781`, `:820` all gate on `signal.side != Side.CLOSE`; `:992-1012` is the only close path; `:1014` — `order_signal, quantity = signal, size_for_account(signal, account)`.
- `app/risk.py:16-19` — size is `fixed_quantity` or `(signal.quantity or 1.0) * multiplier`; the open position is never consulted.
- `app/engine.py:2472-2593` — the P0-5 reconciliation (`_reconcile_before_plain_close`), `claim_close` cross-process lock and Track-18 `_gate_close_by_provider_ownership` run **only** for `Side.CLOSE`; a `SELL` entry skips all of them.
- `app/engine.py:2612-2616` — managed account: `if signal.side == Side.CLOSE: ... return await self._handle_managed_entry(...)`; `app/lifecycle/manager.py:684-688` rejects a SELL with no stop ("no stop-loss resolved"), `:727-743` rejects one while a lifecycle is open — so a managed position is never exited by "sell".
- `app/brokers/tastytrade.py:161` — `action = "Buy to Open" if signal.side == Side.BUY else "Sell to Open"`; `app/brokers/alpaca.py:134` passes `"side": signal.side.value` (short if flat, shortable).
- `app/sources/webhook.py:12` documents `"side": "buy",  # buy | sell | close` with no definition of "sell".
- Pinned as intended: `tests/test_position_tracking.py:59-67` `test_sell_then_close_flattens_short_position` (SELL 1.5 → position −1.5); `tests/test_tastytrade_broker.py:83-102` `test_sell_sends_sell_to_open`.
- Probe (appendix): `SELL-no-qty probe: calls = [('buy', 10.0, 'AAPL'), ('sell', 1.0, 'AAPL')] ... pos after = 9.0`; `SELL-flat probe: calls = [('sell', 5.0, 'AAPL')] ... pos after = -5.0`.
Scenario: provider posts "BUY AAPL" then later "SELL AAPL". Plain Alpaca account (multiplier 1): 1 share sold, 9 remain open with no exit ever coming. Same account if the entry had been rejected/flat: a 1-share short is opened. Tastytrade: "Sell to Open" short. Managed account: "no stop-loss resolved" rejection; position stays open. A second provider sharing the account can "sell" another provider's long because the ownership gate is close-only.
Fix: make exit intent explicit — add `Side.SHORT` (or an `action` field) so "sell" on an account with a same-symbol long resolves through the close path (reduce by provider quantity, full close if none), and refuse a SELL entry that would flip or exceed a long unless the message said "short".
REGISTERED partially as J-12 (Tastytrade) only.

### A-02 — Edited entry message places a second live entry; edited stop never applied (P0, semantic-miss) — B-11 verified
Evidence:
- `app/sources/telegram.py:91-97` — `is_edit = edited_message is not None` ... `revision_id = f"{message_id_str}:{edit_date.isoformat()}" if is_edit and edit_date and message_id_str else None`; `:108-112` sets `revision_id`/`original_message_id` and still calls `await self.on_signal(signal)`.
- `app/sources/telegram_user.py:345-367`, `app/sources/slack_user.py:310-316`, `app/sources/twitter_user.py:241-250`, `app/main.py:5274-5298` (notification bridge) — identical pattern: new `revision_id`, then `engine.handle_signal`.
- `app/db.py:2186-2188` — dedup is exact on the revision: `AND ((revision_id IS NULL AND ? IS NULL) OR revision_id = ?)`; so an edit never matches the original and gets a fresh `signal.id` (`app/engine.py:516-520`).
- Nothing supersedes: no caller of `find_signal_id_by_provider_identity`-by-`message_id`-only exists; `original_message_id` is read only by `app/export_events.py:218,389`.
- Test that pins it: `tests/test_telegram_cross_collector_dedup.py:106-133` — docstring "it must NOT be collapsed onto the original's dedup identity", `assert len(place_order_calls) == 2  # two DIFFERENT real provider events`. Also `tests/test_telegram_source.py:181-195` (edit still dispatched to `on_signal`). `tests/test_notification_bridge_api.py:246-293` is titled "...not a duplicate or a second signal" but asserts only `classification == "live"`, `is_edit`, `revision_seq == 2` and never checks `/orders` (REGISTERED B-12 notes this).
- Probe: `EDIT probe: place_order calls = [('buy', 1.0, 'BTCUSDT'), ('buy', 1.0, 'BTCUSDT')] | tracked position = 2.0`.
- Edited stop: plain account → the edit is just another entry (with Alpaca a second bracket order; with no-bracket brokers both are rejected, `EDIT-STOP probe`); managed account → `validate_plan` "an active managed lifecycle already exists" (`manager.py:739-742`) → REJECTED; stop unchanged. Only trailing/dashboard paths move stops (`manager.py:536 preview_stop_change`, `:2040 _replace_stop_price`).
Scenario: provider posts "BUY BTCUSDT 1 @ 65000 SL 63000", then edits to "SL 64000". Plain account: exposure doubles. Managed: edit rejected, stop stays at 63000.
Fix: on an EDIT, resolve the original signal id by `(channel_id, message_id)` ignoring revision; if it has orders, treat the revision as an amendment (stop/target replace via the lifecycle manager; cancel+replace for a still-pending entry) and never as a new entry.
REGISTERED B-11 ("HIGH").

### A-03 — Telegram bot adapter cannot see edits of channel posts; they collapse onto the original (P1, semantic-miss) — UNVERIFIED
Evidence: `app/sources/telegram.py:91-92` reads only `update.edited_message`. python-telegram-bot's `Update` carries edited channel posts in a separate `edited_channel_post` attribute (with `effective_message` covering both); the `MessageHandler(filters.TEXT ...)` at `:141` receives both update kinds. For a broadcast channel (the common provider setup) an edit therefore has `is_edit=False`, `revision_id=None`, identical `(channel_id, message_id, None)` identity → `find_signal_id_by_provider_identity` returns the original id → `list_orders_for_signal` non-empty → the edit is **replayed as a duplicate** and silently ignored (`engine.py:545-559`). UNVERIFIED: `python-telegram-bot` is not installed in `.venv`, so the attribute shape could not be checked in this environment; `tests/test_telegram_source.py` fakes only `edited_message`.
Scenario: channel edit "SL 63000 → 64000" or a corrected size: ignored with no log beyond "replaying them instead of re-submitting".
Fix: detect `update.edited_channel_post` too; then apply the A-02 amendment semantics.

### A-04 — Trim/open-close verbs are parsed as instruments: `HALF`, `TO`, `10` (P0, semantic-miss)
Evidence: `app/sources/text_parser.py:59-60` — `(?P<side>buy|sell|long|short|close|exit)\s+` / `(?P<symbol>[A-Za-z0-9/.\-]+)`; `:183-184` — `if bare.isalpha() and 1 <= len(bare) <= 5:` / `return AssetClass.EQUITY`. Probe: `'SELL half AAPL' -> parsed side=sell sym=HALF ac=equity`; `'Close half ETHUSDT' -> parsed side=close sym=HALF`; `'BUY TO OPEN AAPL 150C' -> parsed side=buy sym=TO ac=equity`; `'BUY 10 AAPL' -> parsed side=buy sym=10 ac=crypto`. No symbol validation exists before routing (`app/risk.py:22-25` only maps). On a plain account with a release-approved equity route, `SELL HALF` is submitted to the broker as a 1-share short of "HALF"; `CLOSE HALF` → "no open position to close"; the real reduction never happens.
Fix: validate the symbol token (reject pure digits, stop-words like HALF/TO/ALL/NOW; require a `symbol_map` hit or an allow-list for text sources) and parse `half|all|N%` as a reduce-fraction on an explicit reduce action.
REGISTERED B-15 (trim) only; the mis-parse itself is new.

### A-05 — Retail option alerts become equity share purchases (P0, semantic-miss)
Evidence: `app/sources/text_parser.py:61` — quantity group `(?:\s+(?P<quantity>""" + _NUMBER + r""")\s*(?:lots?|units?|shares?)?)?` captures the strike's digits; `:122` — only `\d{6}[CP]\d{8}` (OCC) is spliced into the symbol; `:141` `_OPTION_SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")`. Probe: `'BUY AAPL 150C 1/17' -> parsed side=buy sym=AAPL ac=equity qty=150.0`; `'BUY AAPL 150C 1/17 @ 2.50' -> ... qty=150.0 px=None`; `'BTO AAPL 150C 1/17 @ 2.50' -> no_match`. Tests pin only the OCC shape (`tests/test_sig03_mixed_provider_asset_classification.py:25,39-45`).
Scenario: options provider posts "BUY AAPL 150C 1/17 @ 2.50"; equity-approved Alpaca route buys 150 shares of AAPL (~$22k) at market.
Fix: recognise `<strike>[CP]` and date tokens next to a ticker and return MISSING_DATA unless a full contract can be resolved into `Signal.option`.

### A-06 — Options are never executable even when classified OPTION (P1, silently-unsupported)
Evidence: no broker reads `Signal.option` (grep: only `app/signal_correlation.py:110-116` and `app/sources/article_classifier.py`); `app/brokers/tastytrade.py:90` `supported_asset_classes = frozenset({AssetClass.EQUITY, AssetClass.OPTION})` while `:171` hard-codes `"instrument-type": "Equity"`; the text parser sets `asset_class=OPTION` but never `Signal.option` (`text_parser.py:366-379`). An OCC-formatted alert therefore passes `can_trade_asset_class` and is sent as an equity leg whose symbol is the OCC string.
Fix: either drop OPTION from `supported_asset_classes` until an adapter builds a real option leg from `Signal.option`, or build it.
REGISTERED L-03 ("HIGH").

### A-07 — Copy-source fills: Rithmic exits become SELL entries; MT5/NinjaTrader partial exits become full closes (P1, semantic-miss)
Evidence: `app/sources/rithmic.py:72` — `side = Side.BUY if notification.transaction_type == TransactionType.BUY else Side.SELL` (no exit/entry distinction; pinned by `tests/test_rithmic_source.py:98-112` SELL fill → signal). `app/sources/ninjatrader.py:55-58` claims "app/sources/rithmic.py already make[s] ... an exit fill always maps to a full CLOSE" — the docstring is wrong about Rithmic. `app/sources/mt4_mt5.py:109-112` — `if entry_type in ("DEAL_ENTRY_OUT", "DEAL_ENTRY_OUT_BY"): side = Side.CLOSE` (a partial close on the source is a full close here, as `ninjatrader.py:54-58` discloses).
Scenario: source Rithmic account sells 2 ES to flatten a long; copier (A-01) sells `2 × multiplier` ES as an entry, or shorts if its own position was already flat.
Fix: derive exit vs entry from the source account's position before/after the fill; map partial exits to a reduce-by-quantity instruction once one exists.

### A-08 — Quantity has no unit, no provider quantity means "1", and `%`/`$`/ranges are misread (P1, semantic-miss)
Evidence: `app/risk.py:18` — `base_quantity = signal.quantity if signal.quantity is not None else 1.0`; `app/sources/text_parser.py:61` discards `lots/units/shares`; `FxContractSpec`/`unit` (`models.py:87-95`) is never populated. Brokers interpret the same float differently: `app/brokers/mt4_mt5.py:113,269-271` `volume=quantity` (lots), `app/brokers/oanda.py:117` `units = quantity`, `alpaca.py:133` `"qty"` shares. Probe: `'BUY AAPL 2% risk SL 140' -> qty=2.0`; `'BUY AAPL 2%' -> qty=2.0`; `'BUY AAPL $500' -> qty=None` (→ 1 share); `'BUY AAPL 150-152' -> qty=150.0`; `'BUY BTCUSDT' -> qty=None` (→ 1 BTC × multiplier; `NO-QTY BUY probe (multiplier 2): [('buy', 2.0, 'BTCUSDT')]`). Pinned: `tests/test_risk_sizing.py:51-58`.
Scenario: "BUY EURUSD 0.5 lots" routed to OANDA → 0.5 units (rejected/rounded); "BUY EURUSD 50000 units" routed to MT5 → 50,000 lots. "BUY AAPL 150-152" buys 150 shares.
Fix: refuse to size an entry with no quantity unless the account has `fixed_quantity`/risk-based sizing; parse `%` only as a risk-percent when `risk_percent_of_equity` sizing exists; parse `N-M` into `price_low/high`; carry a `quantity_unit` and convert per broker.
REGISTERED G-03 ("arguably unsafe"), M-08.

### A-09 — Price semantics: market orders always, no chase guard, priceless entries pass the buying-power gate, "sell" exits are freshness-gated as entries (P1, semantic-miss)
Evidence:
- Entry type never honoured: `app/brokers/alpaca.py:135` `"type": "market",`; `tastytrade.py:167` `"order-type": "Market",`; `oanda.py:124` `"type": "MARKET",`; `app/brokers/ninjatrader.py:76` `"price": signal.price or 0,` (what the C# strategy does with price 0 is UNVERIFIED); `app/brokers/paper.py:93` `price = signal.price or 0.0` (priceless signals fill at 0 → P&L garbage). `entry_order_type` is consumed only by `app/export_events.py:240`. Text: `'BUY AAPL limit 150' -> px=None`.
- Gates: `app/engine.py:1928-1934` buying-power check "skipped ... reason=no_resolvable_price" (fails open); `:2048-2057` rejects a priceless entry only when an exposure gate is configured; `:2077` notional = `quantity * signal.price` — the provider's quoted price, which may be minutes old, never a live quote.
- No chase guard: no `max_chase`/`price_distance` anywhere in `app/` (grep). Freshness (`app/signal_freshness.py`) is time-only: `:234-235` disabled unless the provider is in the Track-14 catalog (`:329-330` `if provider_row is None: return FreshnessConfig.disabled()`); `:242-243` `ceiling = config.max_entry_age_seconds` / `is_exit = signal.side.value == "close"`; `:252` `is_add = False`; `:276-291` a stale non-close is `StalenessAction.REJECT`. So a stale provider "SELL" (exit) is **rejected** instead of following `exit_stale_behavior`, leaving the position open. `engine.py:1389-1397` resolves the source row only by `channel_id`.
Scenario: "BUY AAPL @ 150 limit" arrives 20 minutes late with AAPL at 156 → market buy at 156; nothing compares 156 to 150.
Fix: honour `entry_order_type=limit` with `price`/`price_high` as the limit; add a configurable max-chase check against a broker quote (Robinhood already has `_ask_price_for`); treat SELL-on-long as an exit for freshness.
REGISTERED H-10 ("HIGH: limit signals execute as market").

### A-10 — Stop/target update, add, trim, cancel: classifier exists but is deliberately unwired (P1, disconnected)
Evidence: `app/parser_tooling.py:25-27` — "It is also intentionally NOT WIRED into live signal routing"; `:120-138` `MessageType` has STOP_UPDATE/TARGET_UPDATE/ADD/TRIM/CANCEL; `:88-93` the ACTIVE parser profile is never consulted on the live path. `app/models.py:276-297` `SourceEventKind.STOP_UPDATE/TARGET_UPDATE/ADD/CANCEL` are emitted by **no** adapter (grep: only ORIGINAL/EDIT/DELETE/REPLY are constructed) and `app/engine.py:396-415` `export_source_event` only does `self.store.append_export_event(envelope)`. Probe: `'Move stop to 140 on AAPL' -> no_match ... msgtype=stop_update`; `'Trim 50% TSLA' -> no_match ... msgtype=trim`; `'Add to AAPL here 5 more' -> no_match ... msgtype=add`; `'Cancel the AAPL order' -> no_match ... msgtype=cancel`.
Scenario: provider moves a stop to breakeven; the copier's stop stays at the original level and the trade is stopped out for a full loss.
Fix: route PARSED-but-non-entry message types to lifecycle actions (`_replace_stop_price`, `request_exit` with a fraction, pending-entry cancel) keyed by `(source, symbol[, analyst])`.
REGISTERED B-13, B-15, B-16.

### A-11 — Deleted/cancelled messages never cancel a resting entry (P1, semantic-miss)
Evidence: `app/sources/telegram_user.py:384-413` and `app/sources/slack_user.py:332-354` emit `SourceEventKind.DELETE` only; `app/main.py:299,334,372,413,432` wire `on_source_event=engine.export_source_event` (outbox append, `engine.py:396-415`); nothing reads DELETE back. Bot API has no delete update at all (`telegram.py:22-24`). The word "cancel" is a negation word: `text_parser.py:96` `"never", "no", "avoid", "skip", "cancel", "cancelled", "canceled",` → `'Cancel BUY AAPL 10' -> ignored`.
Scenario: provider posts a limit entry, then deletes it (or posts "cancel the AAPL order") before fill; the copier's working order stays and fills later.
Fix: on DELETE/CANCEL of a message whose signal has PENDING orders → cancel via the command ledger; if FILLED → raise an operator alert, never auto-close.
REGISTERED B-16.

### A-12 — Negation/conditional word list drops real entries and promotes status statements to entries (P1, semantic-miss)
Evidence: `app/sources/text_parser.py:93-104` word list incl. `"wait", "waiting", "hold", "holding", ...`; `:294-301` any such word in the 4 preceding words **or anywhere after** the match → IGNORED. Probe: `'BUY AAPL 10 hold for swing' -> ignored`; `'BUY AAPL 10 - wait for pullback' -> ignored`; `"BUY AAPL 10 don't chase above 152" -> ignored` (the cap becomes a total drop); but `"I'm long AAPL" -> parsed side=buy`; `'going long AAPL' -> parsed side=buy`; `'SELL AAPL 10 (closing long)' -> ambiguous` (the one message that disambiguates "sell" as an exit is refused).
Fix: separate cancel verbs from negations; treat trailing horizon words ("hold for swing") as `management_horizon`; treat "I'm/we're long" as a status statement (RESULT/COMMENTARY), not an instruction.

### A-13 — Asset-class inference by symbol shape misroutes crypto/futures and rejects valid crypto (P1, semantic-miss)
Evidence: `app/sources/text_parser.py:156-186`; `:183-184` any 1–5 letter alpha token → EQUITY. Probe: `'BUY BTC 0.1' -> ac=equity` (in a CRYPTO channel), `'BUY ES 1 @ 5000' -> ac=equity`, `'BUY NQ' -> ac=equity`, `'BUY MES 2' -> ac=equity`, `'Buy GOLD' -> ac=equity`, `'BUY ESZ6 1' -> ac=crypto` (default), `'BUY XAUUSD 0.1' -> ac=crypto`. The inferred class overrides the source's configured default (`:351-354`; pinned by `tests/test_sig03_mixed_provider_asset_classification.py:69-75`). Downstream `engine.py:849-876` rejects by `can_trade_asset_class`, and `_check_route_qualified` (`:1748-1754`) keys on the inferred class — so "BUY BTC 0.1" on a ccxt route is **rejected** and a futures alert is routed to an equity broker.
Scenario: crypto channel posts "BUY BTC 0.1"; order rejected "broker 'ccxt' cannot trade asset_class='equity'" with no alert to the owner that a valid signal was lost.
Fix: per-source instrument allow-list / `symbol_map` lookup before shape inference; add a futures-root list; return MISSING_DATA instead of guessing in mixed channels.

### A-14 — Duplicate/correlation: priceless alerts and re-posts beyond 15 min are not correlated; two analysts stack (P1, semantic-miss)
Evidence: `app/signal_correlation.py:317-318` — `if new_price is None or candidate_price is None:` / `return None` ("not eligible"); `app/engine.py:1453-1454` requires `channel_id` and `message_id`; `:1493-1498` window is on `received_at` (not provider time); `app/config.py:439` `SIGNAL_CORRELATION_TIMESTAMP_WINDOW_SECONDS: float = 900.0`; fingerprint includes `side` (`signal_correlation.py:104-109`). Webhook retries with no `Idempotency-Key` and no body `id`/`event_id` are not deduped (`app/main.py:1266-1287`; docstring `:1247-1249` admits TradingView cannot set headers) — REGISTERED B-03.
Probe: `2-transport no-price probe: calls = [... 2 orders]`; `2-transport with-price probe: calls = [1 order]`; `re-post 20min later probe: 2 orders`; `2-analysts same channel probe: 2 orders, pos= 2.0` (managed accounts instead reject the second, `manager.py:727-743`). "Still holding ..." reminder → IGNORED (safe); "I'm long AAPL" → entry (A-12). Duplicate CLOSEs are handled (`manager.py:284 check_duplicate_exit`, plain flat → rejected) — REGISTERED B-14 covered.
Fix: correlate priceless signals on `(source, symbol, side)` within the window with an explicit HOLD; use `source_created_at` for the window; make per-analyst stacking a policy.
REGISTERED B-03, B-09 (partial).

### A-15 — Analyst is attribution-only; sizing overrides use it, ownership/exits do not (P2, missing)
Evidence: set by `telegram.py:88-89` `analyst = (user.username or user.full_name) if user else None` (None whenever there is no `effective_user` — `tests/test_telegram_source.py:128-135`; channel posts typically carry no user: UNVERIFIED here), `telegram_user.py:533`, `discord.py:66` `str(message.author)`, `slack.py:70` user id, `email_source.py:576-577` from-address, webhook payload, bridge title rules. Consumers: `app/providers.py:80-83` settings overrides; `app/provider_value.py:118` reporting key `(source, analyst, asset_class)`; `app/export_events.py`. Ownership/exit gating keys on `signals.source` only: `app/db.py:2614-2617` `JOIN signals s ON o.signal_id = s.id ... s.source`, `app/engine.py:2447-2449`, `:3171-3176` `owning_source != signal.source`. Traceability B-10 (yaml:297): "get_provider_position_ownership groups by Signal.source; analyst is used only for settings overrides and reporting."
Scenario: analysts X and Y in one Discord both long AAPL on a shared plain account; X posts "close AAPL" → the pooled position including Y's share is flattened.
Fix: include `analyst` (when present) in the ownership key and the managed-lifecycle identity, behind a per-source option.
REGISTERED B-10.

### A-16 — Multi-target quantities are no-ops; plain accounts keep only TP1 (P2, silently-unsupported)
Evidence: `app/engine.py:2651-2671` (managed) — comment "a level that carries a `quantity` but no `fraction` ... resolves here to `reduce_fraction=None`, which `PositionLifecycleManager` ... treats as a real, harmless 0.0-fraction no-op"; `app/brokers/alpaca.py:138-149` embeds only `signal.take_profit`; `ccxt_broker.py:174-175`, `mt4_mt5.py:124-125` likewise. Text parser sets `take_profit` = TP1 (`text_parser.py:271`).
Fix: resolve per-level `quantity` against the sized quantity; refuse multi-target signals on plain accounts or document that only TP1 is honoured.

### A-17 — Time-based exits and entry expiry are representable but never executed (P2, silently-unsupported)
Evidence: `app/models.py:182,185` fields; set by no adapter (webhook parses neither); consumed only by `app/export_events.py:241-242`; `app/engine.py:2673-2687` builds `PositionPlan(...)` without `time_exit` although `app/lifecycle/models.py:131` and `manager.py:371 check_time_exits` support it. Probe: `'BUY AAPL 10 @ 150, exit by Friday' -> ambiguous`.
Fix: parse "exit by/EOD/swing" into `management_horizon`/`entry_expiration` and map to `PositionPlan.time_exit`; cancel unfilled entries at `entry_expiration`.
REGISTERED H-09.

### A-18 — Tests and docs pin the unintended semantics (P3, mock-only-tested / doc)
Evidence: `tests/test_telegram_cross_collector_dedup.py:106-133` (edit = "legitimate revised instruction" → two orders); `tests/test_tastytrade_broker.py:83-102` ("Sell to Open"); `tests/test_position_tracking.py:59-67` (SELL = short); `tests/test_risk_sizing.py:51-58` (default 1.0); `tests/test_notification_bridge_api.py:246` (title claims no second signal, no order assertion); `app/sources/ninjatrader.py:55-58` misdescribes `rithmic.py:72`; `docs/requirements/FUNCTIONAL.md:12-14` lists `side (buy/sell/close)` without defining "sell"; `docs/testing/ALLOCATION_TRACEABILITY.md:38-50` already records B-11, B-13, B-15, B-16, H-10, J-12, L-03 as open gaps. `docs/KNOWN_ISSUES.md` and `docs/TECH_DEBT.md` mention none of them (grep).
Fix: convert the three pinning tests into "exit phrased as sell / edit / trim" acceptance tests with the intended semantics; add the side definition to FUNCTIONAL.md and the webhook docstring.

### A-19 — Non-PARSED messages on live transports are dropped at debug level with no record (P1, silently-unsupported)
Evidence: disposition enum `app/sources/text_parser.py:189-211`; live path `parse_text_signal` `:395-402` raises `SignalValidationError` for every non-PARSED outcome; every adapter swallows it: `telegram.py:101-103` `except SignalValidationError:` / `logger.debug("telegram message did not parse as a signal: %r", text)` / `return`; same at `discord.py:67-69`, `slack.py:73-75`, `telegram_user.py:296-298, 357-359`, `email_source.py:520-522`, `twitter.py:79-81`, `whatsapp.py`/`sms_twilio.py` (raise to the route). Only the notification bridge persists a classification (`app/main.py:5271-5272`, `:5335-5345`) and the batch endpoints (`:4040`, `:4109`) classify history. No message is ever executed "with a default" — but the mis-parses in A-04/A-05/A-12 are PARSED with wrong content, which is worse than a default.
Fix: persist a disposition row (outcome, detail, message_type) for every live message and surface non-PARSED ones for review.

### A-20 — Email "corrections" are independent signals (P2, semantic-miss)
Evidence: `app/sources/email_source.py:531-536` "A correction email (In-Reply-To set) is STILL a fresh, distinguishable Signal"; `:546` `await self.on_signal(signal)` before `:551-556` emits `SourceEventKind.REPLY` with `parent_message_id`. A reply "Correction: BUY AAPL 10 @ 150" is a second entry; "SL should be 140" is NO_MATCH → dropped.
Fix: when `in_reply_to` points at a message with orders, treat a parsable reply as an amendment.

### A-21 — Article sources turn "we are selling X" into a live SELL entry (P1, semantic-miss)
Evidence: `app/sources/article_classifier.py:117` `r"\bwe(?:'re| are) selling\b",` is in `_ACTIONABLE_PHRASES` (classified as a fresh actionable trade, not ADJUSTMENT_OR_EXIT); `:374` `_SELL_VERB_PATTERN = re.compile(r"\b(selling|sold|sell|short(?:ing)?)\b", re.IGNORECASE)`; `:467-468` `elif sell_hits and not buy_hits:` / `side = Side.SELL`; symbol = most frequent caps token (`:398-411`); `app/sources/website.py:354-357` and `rss_source.py:488-495` hand a resolved candidate to `self.on_signal` → A-01 makes it a short/sell entry.
Fix: classify "we are selling/sold our position" as ADJUSTMENT_OR_EXIT and never build an entry candidate from it.

---

## 3. Direct answers to Q2–Q9 (pointers)

- **Q2 revisions**: edited entry → second live entry on plain accounts, REJECTED on managed (A-02; pinned by `test_telegram_cross_collector_dedup.py:133`). Edited stop → never updates the position's stop (A-02). Channel-post edits in the bot adapter likely collapse onto the original and are ignored (A-03, UNVERIFIED). Deleted/cancelled message → export row only, nothing cancelled (A-11).
- **Q3 side**: "sell" == "short" == new entry. Plain long account: sells `fixed_quantity` or `(qty or 1)×multiplier`, not the position; flat → short. Short-capable brokers open shorts (Tastytrade "Sell to Open", Alpaca `side=sell`). No guard distinguishes exit intent; the close gates run only for `Side.CLOSE` (A-01).
- **Q4 quantity**: provider units, unit word discarded, no quantity → 1.0 × multiplier, `fixed_quantity` overrides; `$` amounts → None → 1 unit; `2%` → 2 units; ranges → quantity (A-08).
- **Q5 price**: message price is informational only; all adapters send market; notional/risk gating uses the message price (or rejects only when a gate is configured; buying-power check fails open); no max-chase; `signal_freshness.py` gates on age only, only for catalog-registered providers, and treats "sell" as an entry (A-09).
- **Q6 duplicates**: same transport exact redelivery → deduped; two transports → deduped only if both carry a price and are within 15 min of `received_at`, else two orders; two analysts → two orders (plain) / second rejected (managed); re-post after 15 min → second order; "still holding" → IGNORED, but "I'm long X" → entry (A-14, A-12).
- **Q7 unsupported**: `classify_text_signal` returns PARSED/IGNORED/AMBIGUOUS/MISSING_DATA/NO_MATCH (`text_parser.py:189-211`); live adapters raise-and-swallow, so non-PARSED messages are dropped silently (debug log), never persisted except via the bridge/batch endpoints (A-19). Nothing executes "with a default", but the HALF/TO/150C mis-parses execute with wrong content (A-04, A-05).
- **Q8 asset class**: webhook `asset_class` field; text parser infers from symbol shape (OCC → OPTION, 6-letter FX codes → FOREX, USDT-style suffix → CRYPTO, 1–5 alpha → EQUITY, else the source default); "AAPL 150C 1/17" → EQUITY buy of 150 shares (A-05); "BTC"/"ES"/"NQ" → EQUITY (A-13); OPTION signals are not executable (A-06).
- **Q9 analyst**: set when the adapter can identify the poster (often None for channels); used for settings overrides and provider-value reporting only; ownership/exits keyed on `source` (A-15).

---

## Appendix — probe output (verbatim)

Parser probe (`classify_text_signal(..., asset_class=CRYPTO)` / `classify_message_type`):
```
'BUY AAPL 150C 1/17'                                    -> parsed       side=buy   sym=AAPL       ac=equity qty=150.0 px=None sl=None tp=None tgts=[] | msgtype=entry
'BUY AAPL 150C 1/17 @ 2.50'                             -> parsed       side=buy   sym=AAPL       ac=equity qty=150.0 px=None sl=None tp=None tgts=[] | msgtype=entry
'BTO AAPL 150C 1/17 @ 2.50'                             -> no_match     detail='no recognizable trade instruction' | msgtype=unknown
'SELL half AAPL'                                        -> parsed       side=sell  sym=HALF       ac=equity qty=None px=None sl=None tp=None tgts=[] | msgtype=entry
'Close half ETHUSDT'                                    -> parsed       side=close sym=HALF       ac=equity qty=None px=None sl=None tp=None tgts=[] | msgtype=exit
'Trim 50% TSLA'                                         -> no_match     detail='no recognizable trade instruction' | msgtype=trim
'Move stop to 140 on AAPL'                              -> no_match     detail='no recognizable trade instruction' | msgtype=stop_update
'Add to AAPL here 5 more'                               -> no_match     detail='no recognizable trade instruction' | msgtype=add
'Cancel the AAPL order'                                 -> no_match     detail='no recognizable trade instruction' | msgtype=cancel
'Cancel BUY AAPL 10'                                    -> ignored      detail='negated, conditional, or still-pending commentary rather than a trade instruction' | msgtype=cancel
'Still holding AAPL long from 150'                      -> ignored      ... | msgtype=commentary
'BUY AAPL 10 hold for swing'                            -> ignored      ... | msgtype=commentary
'BUY AAPL 10 - wait for pullback'                       -> ignored      ... | msgtype=commentary
'BUY BTC 0.1'                                           -> parsed       side=buy   sym=BTC        ac=equity qty=0.1 ...
'BUY BTCUSDT'                                           -> parsed       side=buy   sym=BTCUSDT    ac=crypto qty=None ...
'BUY EURUSD 0.50 lots SL 1.0950 TP 1.1050'              -> parsed       side=buy   sym=EURUSD     ac=forex  qty=0.5 px=None sl=1.095 tp=1.105 ...
'BUY AAPL $500'                                         -> parsed       side=buy   sym=AAPL       ac=equity qty=None ...
'BUY AAPL 2% risk SL 140'                               -> parsed       side=buy   sym=AAPL       ac=equity qty=2.0 px=None sl=140.0 ...
'BUY AAPL limit 150'                                    -> parsed       side=buy   sym=AAPL       ac=equity qty=None px=None ...
'BUY AAPL @ 150 limit'                                  -> parsed       side=buy   sym=AAPL       ac=equity qty=None px=150.0 ...
'Short TSLA 10 @ 250 SL 260'                            -> parsed       side=sell  sym=TSLA       ac=equity qty=10.0 px=250.0 sl=260.0 ...
'BUY ES 1 @ 5000'                                       -> parsed       side=buy   sym=ES         ac=equity qty=1.0 px=5000.0 ...
'BUY NQ'                                                -> parsed       side=buy   sym=NQ         ac=equity ...
'BUY XAUUSD 0.1'                                        -> parsed       side=buy   sym=XAUUSD     ac=crypto qty=0.1 ...
'Buy GOLD'                                              -> parsed       side=buy   sym=GOLD       ac=equity ...
'exit AAPL'                                             -> parsed       side=close sym=AAPL       ac=equity ...
'AAPL hit TP1, closing half'                            -> no_match     ... | msgtype=result
"BUY AAPL 10 don't chase above 152"                     -> ignored      ... | msgtype=commentary
'BUY AAPL 10 @ 150, exit by Friday'                     -> ambiguous    detail='more than one trade instruction in a single message' | msgtype=unknown
"I'm long AAPL"                                         -> parsed       side=buy   sym=AAPL       ac=equity ...
'Buy AAPL 10 @ 150 (stop 145)'                          -> parsed       side=buy   sym=AAPL       ac=equity qty=10.0 px=150.0 sl=None ...
'SELL AAPL 10 (closing long)'                           -> ambiguous    detail='more than one trade instruction in a single message' | msgtype=unknown
'Buy AAPL 10 @150 and sell half at 155'                 -> ambiguous    ...
'BUY 10 AAPL'                                    -> parsed       side=buy   sym=10         ac=crypto qty=None ...
'BUY TO OPEN AAPL 150C'                          -> parsed       side=buy   sym=TO         ac=equity qty=None ...
'BUY AAPL 150-152'                               -> parsed       side=buy   sym=AAPL       ac=equity qty=150.0 px=None ...
'BUY AAPL @ 150-152'                             -> parsed       side=buy   sym=AAPL       ac=equity qty=None px=150.0 ...
'Sold AAPL'                                      -> no_match
'Stopped out of AAPL'                            -> no_match
'Closed AAPL +5%'                                -> no_match
'Sell to close AAPL'                             -> ambiguous
'SELL AAPL at market'                            -> parsed       side=sell  sym=AAPL       ac=equity qty=None ...
'BUY ESZ6 1 @ 5000'                              -> parsed       side=buy   sym=ESZ6       ac=crypto qty=1.0 px=5000.0 ...
'BUY MES 2'                                      -> parsed       side=buy   sym=MES        ac=equity qty=2.0 ...
'going long AAPL'                                -> parsed       side=buy   sym=AAPL       ac=equity ...
'BUY AAPL 10 TP1 155 TP2 160 TP3 170 SL 145'     -> parsed       side=buy   sym=AAPL       ac=equity qty=10.0 sl=145.0 tp=155.0 tgts=[155.0, 160.0, 170.0]
```

Engine probe (`SignalCopierEngine` + `PaperBroker` + fresh `SignalStore`, one plain account `a1`,
`exclusive_writer_qualified=True`; `calls` = `(side, quantity, symbol)` passed to `place_order`):
```
EDIT probe: place_order calls = [('buy', 1.0, 'BTCUSDT'), ('buy', 1.0, 'BTCUSDT')] | tracked position = 2.0
EDIT-STOP probe: calls = [] | results: ["rejected:broker 'paper' cannot embed stop_loss/take_profit into the entry order (no native bracket) and this account is not managed_lifecycle — refusing to submit an ent", "rejected:..."] | pos = 0.0
SELL-no-qty probe: calls = [('buy', 10.0, 'AAPL'), ('sell', 1.0, 'AAPL')] | statuses: ['filled'] | pos after = 9.0
SELL-flat probe: calls = [('sell', 5.0, 'AAPL')] | statuses: ['filled:filled by paper broker'] | pos after = -5.0
SELL-fixedqty probe: calls = [('buy', 5.0, 'AAPL'), ('sell', 5.0, 'AAPL')] | pos after = 0.0
CLOSE probe: calls = [('buy', 10.0, 'AAPL'), ('sell', 10.0, 'AAPL')] | pos after = 0.0
NO-QTY BUY probe (multiplier 2): [('buy', 2.0, 'BTCUSDT')] ['filled']
2-transport no-price probe: calls = [('buy', 1.0, 'BTCUSDT'), ('buy', 1.0, 'BTCUSDT')]
2-transport with-price probe: calls = [('buy', 1.0, 'BTCUSDT')]
2-analysts same channel probe: calls = [('buy', 1.0, 'BTCUSDT'), ('buy', 1.0, 'BTCUSDT')] pos= 2.0
re-post 20min later probe: calls = [('buy', 1.0, 'BTCUSDT'), ('buy', 1.0, 'BTCUSDT')]
```
Probe scripts: `scratchpad/probe_parser.py`, `scratchpad/probe_parser2.py`, `scratchpad/probe_engine.py` (same directory as this file).
