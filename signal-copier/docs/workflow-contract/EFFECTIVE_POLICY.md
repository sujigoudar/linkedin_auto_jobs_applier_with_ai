# Effective Policy: Configuration Precedence & Provenance

**Date:** October 2, 2026  
**Scope:** Actual configuration precedence chain for the deployed application.

This document specifies the precedence order when policy settings conflict, and traces the provenance (source) of each configuration value.

---

## Configuration Hierarchy

Configuration is resolved in this order, with later entries overriding earlier:

### 1. Hardcoded Defaults in Code

**Precedence Level:** Lowest

Location: `app/models.py`, `app/config.py`, adapter implementations

Examples:
- `DestinationAccount.multiplier` default: `1.0` (app/models.py:702)
- `DestinationAccount.managed_lifecycle` default: `False` (app/models.py:713)
- `Signal.asset_class` default: `AssetClass.CRYPTO` (app/models.py:139)
- `Signal.entry_order_type` default: `None` (app/models.py:203)

### 2. Environment Variables (app/config.py)

**Precedence Level:** Medium-Low

Location: `app/config.py` (read from `.env` and `os.environ`)

Key variables:
- `PAPER_MODE` — Force paper broker for all accounts (overrides accounts.yaml broker field)
- `STANDBY_MODE` — Read-only server; no signal ingestion or trading
- `RELAY_INGRESS_URL` — Commercial platform export endpoint; if unset, relay scheduler does not start
- `ALLOCATION_INTENT_STALE_SECONDS` — Startup sweep timeout for crashed allocation intents (app/main.py:561)
- `RELAY_EVIDENCE_CLASS` — Default evidence tier for exported events (unless overridden per account)
- Broker API keys, webhooks secrets, auth tokens (never in repo)

### 3. Static Configuration Files (accounts.yaml, routing rules)

**Precedence Level:** Medium

Location: `accounts.yaml` (parsed at startup) → `SignalStore` → `config_accounts` table

Fields:
- `account_id` — Canonical identifier
- `broker` — Broker adapter name (e.g., "alpaca", "paper", "ccxt_binance_spot")
- `enabled` — Account enabled for new entries/exits
- `multiplier`, `fixed_quantity`, `symbol_map` — Sizing configuration
- `managed_lifecycle`, `management_recipe`, `qualification_level` — Lifecycle mode
- `max_notional_exposure`, `risk_percent_of_equity`, `daily_loss_limit_percent`, `min_equity_threshold`, `max_gross_leverage` — Capital/risk limits
- `allow_short` — Short-position permission
- `currency` — ISO 4217 base currency
- `evidence_class` — Export evidence tier
- `exclusive_writer_qualified` — Operator assertion (exclusive writer to this account's position)

Routing rules (app/routing.py):
- `(account_id, symbol)` → `entry_enabled`, `exit_enabled` flags

### 4. Database Configuration (runtime state)

**Precedence Level:** Medium-High

Location: `app/db.py` — SignalStore tables read/written at runtime

Tables:
- `config_accounts` — Runtime mirror of `accounts.yaml` (can be edited via `PATCH /accounts` API)
- `routing_rules` — Per-symbol entry/exit enable flags (can be edited via routing configuration API)
- `qualifications` — Release gate state per route (recorded via `POST /qualifications`)
- `source_events` — Ingested signal envelope/lineage
- `capital_reservations` — Active capital holds (app/capital_allocator.py)
- `allocation_intents` — Pending/claimed allocation state (WP-34)

### 5. Provider-Specific Overrides

**Precedence Level:** Highest (within their scope)

Location: `app/providers.py` (signal.analyst → analyst-level config)

Scope: Provider + analyst (when `signal.analyst` is set)

Overrides:
- Per-analyst risk limits (if explicitly configured)
- Per-analyst strategy preferences
- Per-analyst product restrictions

Example: A signal from source "discord" analyst "trader_a" can have different `max_notional_exposure` than the same account's default.

---

## Conflict Resolution Rules

### Capital/Risk Limits (Intersection, Never Permissive Default)

When multiple levels set a limit:
- **Owner-wide limit** ∩ **Account limit** ∩ **Portfolio limit** ∩ **Strategy limit** ∩ **Analyst limit** = **Effective limit**
- Missing/`None` at any level ≠ unbounded; must be explicitly set
- Example: If account sets `max_notional_exposure=10000` but portfolio sets `100000`, effective limit is `10000`

### Lifecycle Mode (Explicit > Default)

- `DestinationAccount.management_recipe` (if explicitly set) overrides inference from `managed_lifecycle` bool
- A disagreement (e.g., `managed_lifecycle=False` but `management_recipe=FULL_MANAGED_LIFECYCLE`) is NOT silently corrected; it's a misconfiguration worth auditing

### Symbol Mapping (Account > Default)

- If `DestinationAccount.symbol_map` contains the signal's symbol, use the mapped value
- Otherwise use signal symbol verbatim

### Currency (Account-Explicit Required)

- `DestinationAccount.currency` must be explicitly set for any account trading multiple currencies or non-USD base
- `None` is an honest gap, never auto-detected from symbol syntax

### Evidence Class (Account > Global)

- `DestinationAccount.evidence_class` (if set) overrides `config.RELAY_EVIDENCE_CLASS` for this account's events

### Broker Selection (Identity > Precedence)

- Broker adapter chosen by: `DestinationAccount.broker` value
- Not by: source preference, analyst preference, or "fastest" ranking
- If multiple accounts are configured with different brokers for the same symbol, the router picks by account eligibility, not broker

---

## Effective Policy Invariants

1. **No Amplification:** Child analyst/strategy overrides can only restrict, never relax, parent (owner/account) limits
2. **Fail Closed:** Missing required input (currency, market data, position readback capability) rejects rather than guesses
3. **Explicit > Default:** Every configuration value visible in the effective state is either explicitly set or honestly `None`/unknown
4. **Immutable After Entry:** Once an entry signal is accepted, its account binding, risk limits, and lifecycle recipe cannot change for that position until close

---

## Migration Path (September v3.4 → October 2026)

The v3.4 workbook numeric recommendations (risk %, position size, Kelly fraction, etc.) are reference only.

Authority order (per WORKFLOW_SPECIFICATION.md §0):
1. Owner authorization (account credentials, broker selection)
2. Released financial policy (limits, gates, manager approval)
3. This implementation contract (WORKFLOW_SPECIFICATION.md)
4. v3.4 workbook (backward-compatible reference, not binding)

The October 2 application represents the current authoritative state. Older v3.4 configurations are honored if still present in `accounts.yaml` / database, but are not promoted or auto-upgraded.

---

**Last Updated:** 2026-10-02  
**Applies To:** Signal Copier baseline + WC-01 (no workflow-contract changes to policy yet)
