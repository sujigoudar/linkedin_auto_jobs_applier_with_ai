"""Persistent, DB-backed admission control on OUTBOUND order-placement
frequency and simultaneous exposure -- a different concern from
app/rate_limit.py's C06, which throttles unauthenticated-until-checked
INGRESS traffic on the webhook/SMS routes (see that module's own
docstring). This one bounds how often this service actually PLACES new
trades, and how many it holds open at once, at five real scopes:

- global (the whole service)
- account (one destination account)
- provider (`Signal.source`)
- analyst (`Signal.analyst`, when the source identifies one)
- instrument/symbol (the account's own resolved destination symbol)

Genuinely persistent (survives a process restart): every count this
module checks is a fresh query against real, already-persisted
`orders`/`signals`/`positions` rows (see `SignalStore.count_entry_orders_since`
and `SignalStore.list_open_positions`/`get_position`) -- never an
in-memory counter a restart would silently reset to zero.

Dimensions actually implemented, and why each is real (never an invented
dimension with no backing data):

- `max_trades_per_hour_*` / `max_trades_per_day_*`: derived from
  `orders.executed_at` (an admitted, non-rejected order placement) joined
  to its originating `signals.side` (to exclude a resolved CLOSE, which
  reaches `orders` as an indistinguishable plain buy/sell -- see
  `SignalStore.count_entry_orders_since`'s own docstring). Genuinely
  derivable from data this service already persists on every real order.
- `max_open_positions_account` / `max_open_positions_global`: the count of
  DISTINCT (account, symbol) rows in `SignalStore.positions` with a
  nonzero `net_quantity` -- this service's own tracked position ledger,
  the same source `app/engine.py`'s own close resolution already treats
  as canonical (see that module's docstring on tracked-vs-live-broker
  positions). Deliberately NOT a live broker position readback the way
  `app/protection_auditor.py`'s independent audit is (that exists to
  catch this service's own bookkeeping being WRONG, which is a different
  job from an admission check that must run synchronously, in the hot
  order-placement path, without adding a broker round-trip to every
  signal): using the tracked ledger here is the same "genuinely derivable
  from existing persisted data" data source `app/capital_allocator.py`'s
  own `confirmed_open_notional` already relies on for its own admission
  gate.
- `max_same_symbol_positions_account`: the current schema tracks at most
  ONE net position per (account, symbol) -- see `app/db.py`'s `positions`
  table PRIMARY KEY -- so this is honestly a 0/1 gate (does this account
  already hold this exact symbol) rather than a genuine multi-position
  count. Still a real, useful admission check (it blocks pyramiding an
  additional independent entry into a symbol this account already holds,
  when configured to 1), just disclosed here as narrower than its name
  might suggest, the same posture this codebase already takes elsewhere
  (e.g. app/economics.py's own disclosed scope limits) rather than
  silently overclaiming what it enforces.

Every limit defaults to `None` (unset/unlimited) via
`app/config.py`'s pydantic-settings pattern (C01) -- no existing
deployment or test is affected unless an operator configures a limit, and
every REJECTED result this module produces carries a real, specific
reason (never a silent drop and never a silent admit).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app import config
from app.db import SignalStore


@dataclass
class PlacementLimits:
    max_trades_per_hour_global: float | None = None
    max_trades_per_day_global: float | None = None
    max_trades_per_hour_account: float | None = None
    max_trades_per_day_account: float | None = None
    max_trades_per_hour_provider: float | None = None
    max_trades_per_day_provider: float | None = None
    max_trades_per_hour_analyst: float | None = None
    max_trades_per_day_analyst: float | None = None
    max_trades_per_hour_symbol: float | None = None
    max_trades_per_day_symbol: float | None = None
    max_open_positions_account: int | None = None
    max_open_positions_global: int | None = None
    max_same_symbol_positions_account: int | None = None

    @classmethod
    def from_config(cls) -> "PlacementLimits":
        return cls(
            max_trades_per_hour_global=config.RATE_LIMIT_MAX_TRADES_PER_HOUR_GLOBAL,
            max_trades_per_day_global=config.RATE_LIMIT_MAX_TRADES_PER_DAY_GLOBAL,
            max_trades_per_hour_account=config.RATE_LIMIT_MAX_TRADES_PER_HOUR_ACCOUNT,
            max_trades_per_day_account=config.RATE_LIMIT_MAX_TRADES_PER_DAY_ACCOUNT,
            max_trades_per_hour_provider=config.RATE_LIMIT_MAX_TRADES_PER_HOUR_PROVIDER,
            max_trades_per_day_provider=config.RATE_LIMIT_MAX_TRADES_PER_DAY_PROVIDER,
            max_trades_per_hour_analyst=config.RATE_LIMIT_MAX_TRADES_PER_HOUR_ANALYST,
            max_trades_per_day_analyst=config.RATE_LIMIT_MAX_TRADES_PER_DAY_ANALYST,
            max_trades_per_hour_symbol=config.RATE_LIMIT_MAX_TRADES_PER_HOUR_SYMBOL,
            max_trades_per_day_symbol=config.RATE_LIMIT_MAX_TRADES_PER_DAY_SYMBOL,
            max_open_positions_account=config.RATE_LIMIT_MAX_OPEN_POSITIONS_ACCOUNT,
            max_open_positions_global=config.RATE_LIMIT_MAX_OPEN_POSITIONS_GLOBAL,
            max_same_symbol_positions_account=config.RATE_LIMIT_MAX_SAME_SYMBOL_POSITIONS_ACCOUNT,
        )


class PlacementRateLimiter:
    """One instance shared by the engine for its whole lifetime, like
    `CapitalAllocator`. Stateless beyond `self.store`/`self.limits` --
    every admission decision is recomputed fresh from the database on
    every call, which is exactly what makes it survive a restart: a new
    process constructing a fresh `PlacementRateLimiter` against the same
    store sees the exact same counts a long-running one would."""

    def __init__(self, store: SignalStore, limits: PlacementLimits | None = None):
        self.store = store
        self.limits = limits or PlacementLimits.from_config()

    def check_admission(
        self,
        *,
        account_id: str,
        provider: str,
        analyst: str | None,
        symbol: str,
        now: datetime | None = None,
    ) -> str | None:
        """Returns `None` if this NEW entry is admitted, or a specific,
        human-readable rejection reason string naming the exact scope and
        limit it would exceed. Never silently drops or silently allows
        through -- callers (see `app/engine.py`) must reject with this
        exact message when it isn't `None`."""
        now = now or datetime.now(timezone.utc)

        scoped_checks: list[tuple[str, dict, float | None, float | None]] = [
            ("global", {}, self.limits.max_trades_per_hour_global, self.limits.max_trades_per_day_global),
            (
                f"account '{account_id}'",
                {"account_id": account_id},
                self.limits.max_trades_per_hour_account,
                self.limits.max_trades_per_day_account,
            ),
            (
                f"provider '{provider}'",
                {"source": provider},
                self.limits.max_trades_per_hour_provider,
                self.limits.max_trades_per_day_provider,
            ),
            (
                f"symbol '{symbol}'",
                {"symbol": symbol},
                self.limits.max_trades_per_hour_symbol,
                self.limits.max_trades_per_day_symbol,
            ),
        ]
        if analyst is not None:
            scoped_checks.append(
                (
                    f"analyst '{analyst}'",
                    {"analyst": analyst},
                    self.limits.max_trades_per_hour_analyst,
                    self.limits.max_trades_per_day_analyst,
                )
            )

        for scope_label, filters, hour_limit, day_limit in scoped_checks:
            if hour_limit is not None:
                count = self.store.count_entry_orders_since(now - timedelta(hours=1), **filters)
                if count >= hour_limit:
                    return (
                        f"placement rate limit exceeded: {scope_label} max {hour_limit}/hour "
                        f"(already placed {count} in the last hour) -- refusing this new entry"
                    )
            if day_limit is not None:
                count = self.store.count_entry_orders_since(now - timedelta(days=1), **filters)
                if count >= day_limit:
                    return (
                        f"placement rate limit exceeded: {scope_label} max {day_limit}/day "
                        f"(already placed {count} in the last day) -- refusing this new entry"
                    )

        any_position_limit = (
            self.limits.max_open_positions_account is not None
            or self.limits.max_open_positions_global is not None
            or self.limits.max_same_symbol_positions_account is not None
        )
        if not any_position_limit:
            return None

        existing_position = self.store.get_position(account_id, symbol)
        opens_new_position = existing_position == 0

        if opens_new_position and (
            self.limits.max_open_positions_account is not None or self.limits.max_open_positions_global is not None
        ):
            open_positions = self.store.list_open_positions()
            if self.limits.max_open_positions_account is not None:
                account_open_count = sum(1 for p in open_positions if p["account_id"] == account_id)
                if account_open_count >= self.limits.max_open_positions_account:
                    return (
                        f"placement rate limit exceeded: account '{account_id}' max "
                        f"{self.limits.max_open_positions_account} simultaneous open positions "
                        f"(currently {account_open_count}) -- refusing this new position"
                    )
            if self.limits.max_open_positions_global is not None:
                global_open_count = len(open_positions)
                if global_open_count >= self.limits.max_open_positions_global:
                    return (
                        f"placement rate limit exceeded: global max {self.limits.max_open_positions_global} "
                        f"simultaneous open positions (currently {global_open_count}) -- refusing this new position"
                    )

        if self.limits.max_same_symbol_positions_account is not None:
            current_same_symbol = 0 if opens_new_position else 1
            if current_same_symbol >= self.limits.max_same_symbol_positions_account:
                return (
                    f"placement rate limit exceeded: account '{account_id}' max "
                    f"{self.limits.max_same_symbol_positions_account} simultaneous position(s) in symbol "
                    f"'{symbol}' (already holds one) -- refusing this new entry"
                )

        return None
