"""Core domain models shared by every source and broker adapter.

Every adapter — however different its wire format (TradingView JSON, a
Telegram message, an MT4 EA callback) — must produce or consume a `Signal`.
That's the one contract the rest of the engine depends on.
"""
from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


class Side(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"


class AssetClass(str, enum.Enum):
    CRYPTO = "crypto"
    FOREX = "forex"
    EQUITY = "equity"
    OPTION = "option"
    FUTURE = "future"


@dataclass
class Signal:
    """A normalized trade instruction, independent of where it came from."""

    source: str
    symbol: str
    side: Side
    asset_class: AssetClass = AssetClass.CRYPTO
    #: Who *within* `source` posted this (e.g. one trader in a shared
    #: Discord server) — optional. Used by app/providers.py to resolve
    #: per-analyst settings overrides; a source parser that can identify
    #: the poster sets this, others leave it None and only provider-level
    #: (not analyst-level) overrides apply.
    analyst: Optional[str] = None
    quantity: Optional[float] = None
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict[str, Any] = field(default_factory=dict)
    #: E02 (bounded, history-import workflow): `None` for every signal that
    #: arrived through a live transport (webhook/bot) -- this codebase's
    #: existing, only signal-creation path (`app/db.py`'s `save_signal`) --
    #: set to an owner-chosen/auto-generated batch label ONLY when a signal
    #: was instead created by the owner-gated batch-classify-and-import
    #: review workflow (`POST /sources/{source}/import-signals`) out of a
    #: pasted historical message that `classify_batch` resolved to PARSED.
    #: This is the one, cheap, honest distinguishing field between a
    #: backfilled and a live-received Signal row -- an additive column
    #: (see `app/db.py`'s `_COLUMN_MIGRATIONS`), not a second schema.
    import_batch: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.side, str):
            self.side = Side(self.side.lower())
        if isinstance(self.asset_class, str):
            self.asset_class = AssetClass(self.asset_class.lower())


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    FILLED = "filled"
    REJECTED = "rejected"
    ERROR = "error"


@dataclass
class OrderResult:
    """What a broker adapter hands back after trying to execute a signal."""

    account_id: str
    status: OrderStatus
    signal_id: str
    broker_order_id: Optional[str] = None
    filled_quantity: Optional[float] = None
    filled_price: Optional[float] = None
    message: str = ""
    executed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AccountBalance:
    """A broker's own real-time answer to "what does this account actually
    have" -- see app/brokers/base.py's `get_account_balance` for what this
    is and isn't a substitute for (in particular: `app/capital_allocator.py`
    deliberately does NOT read this; it enforces a ceiling against this
    service's own confirmed-fill replay, never a live broker balance read).

    Every field is `None`, not 0.0, when this broker/account genuinely
    didn't report it -- a spot/cash account with no margin concept at all
    (e.g. Alpaca's non-margin accounts) reports `maintenance_margin=None`,
    never an invented 0.0 that would look identical to "no margin used
    right now" for one that DOES have margin.
    """

    account_id: str
    #: Free cash, in the account's base currency.
    cash: Optional[float] = None
    #: Total account value (cash + market value of open positions).
    equity: Optional[float] = None
    #: What Alpaca calls "buying power" -- how much more could be
    #: deployed right now, already netting out any margin in use. Not a
    #: universal concept (e.g. ccxt spot has no single account-wide
    #: figure like this — see CCXTBroker's docstring on why it doesn't
    #: implement this method at all).
    buying_power: Optional[float] = None
    #: Margin currently held against open positions, if this is a margin
    #: account and the broker reports it.
    maintenance_margin: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "cash": self.cash,
            "equity": self.equity,
            "buying_power": self.buying_power,
            "maintenance_margin": self.maintenance_margin,
        }


class ManagementRecipe(str, enum.Enum):
    """P0-5: an account's explicit, PERSISTED declaration of which safety
    product it actually is -- managed and unmanaged positions are
    fundamentally different products (MAE/MFE, protection coverage,
    transfer logic, and the rest of app/lifecycle/manager.py's machinery
    exist for one of them and not the other), and that distinction must
    be a real, auditable field on the account, not something a screen or
    a report has to re-derive by checking `managed_lifecycle` itself
    every time.

    This is deliberately a two-value declaration today (matching this
    codebase's own existing `managed_lifecycle` boolean terminology, not
    inventing new tiers): a broker/route-CAPABILITY qualification
    taxonomy (what a broker adapter can actually verify/do) is a related
    but distinct concept that a sibling effort (P0-7) is building for
    broker capabilities specifically -- this field is the ACCOUNT's own
    declared management contract, and P0-7's taxonomy may reference it,
    not replace it.
    """

    #: Entries/exits for this account route through
    #: PositionLifecycleManager -- protect-first, logical targets/
    #: trailing, MAE/MFE tracking, a serialized close arbiter. The full
    #: managed-lifecycle safety product.
    FULL_MANAGED_LIFECYCLE = "full_managed_lifecycle"
    #: Entries/exits for this account are plain BUY/SELL/CLOSE orders
    #: against this service's own tracked position -- no MAE/MFE, no
    #: protection coverage, no transfer logic. See
    #: app/engine.py's module docstring ("Close signals") and
    #: `DestinationAccount.exclusive_writer_qualified` for what a plain
    #: account's CLOSE additionally now requires before it's allowed to
    #: proceed.
    PLAIN_UNMANAGED = "plain_unmanaged"


@dataclass
class DestinationAccount:
    """One account a signal can be routed to, plus how to size the trade."""

    account_id: str
    broker: str
    multiplier: float = 1.0
    fixed_quantity: Optional[float] = None
    symbol_map: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    #: Route entries/exits for this account through
    #: app/lifecycle/manager.py's PositionLifecycleManager (protect-first,
    #: logical targets, a serialized close arbiter) instead of embedding
    #: stop_loss/take_profit directly into the entry order. See
    #: app/lifecycle/manager.py's module docstring for why/when this
    #: matters. Off by default — existing accounts behave exactly as
    #: before unless explicitly opted in.
    managed_lifecycle: bool = False
    #: E03: an opt-in notional-exposure ceiling for this account (None = no
    #: limit, the existing default behavior). See app/capital_allocator.py's
    #: module docstring for exactly what this does and doesn't enforce --
    #: whenever this (or any other gate below) is configured, a signal
    #: with no resolvable price now REJECTS rather than skipping the check.
    max_notional_exposure: Optional[float] = None
    #: E03 (risk-basis sizing): an opt-in ceiling on risk-to-stop as a
    #: percentage (0-1) of this account's real, freshly-fetched equity
    #: (None = not enforced, the default). A new entry whose
    #: |entry_price - stop_loss| * quantity would exceed
    #: `equity * risk_percent_of_equity` is rejected. Fails closed --
    #: rejects, never silently skips -- whenever the admitting signal has
    #: no `stop_loss`, or the account's broker can't report a real
    #: `equity` figure right now. See app/capital_allocator.py's module
    #: docstring and app/engine.py's `_check_risk_basis`.
    risk_percent_of_equity: Optional[float] = None
    #: P0-5: this account's own explicit, persisted management-recipe
    #: declaration -- see `ManagementRecipe`'s own docstring. `None` on
    #: construction means "not explicitly declared"; `__post_init__`
    #: immediately fills it from `managed_lifecycle` so every constructed
    #: `DestinationAccount` always carries a real, non-None value from
    #: here on (never left as an unauditable inference done ad hoc by
    #: whatever screen/report happens to read it). Passing an EXPLICIT
    #: value that disagrees with `managed_lifecycle` is accepted as-is
    #: (not silently overwritten) -- that disagreement is itself a real
    #: misconfiguration worth surfacing/auditing on TR-07, not something
    #: this field quietly resolves on the account's behalf.
    management_recipe: Optional[ManagementRecipe] = None
    #: P0-5: a simple, free-form qualification label for this account's
    #: declared management contract (e.g. "qualified", "unqualified",
    #: "pending_review") -- intentionally NOT an enum yet. P0-7 is
    #: building a fuller broker-capability qualification taxonomy
    #: separately; this field may end up referencing that taxonomy later,
    #: but for now it's this account's own simple, independent label.
    #: `None` (the default) means "not yet declared," never fabricated as
    #: "qualified."
    qualification_level: Optional[str] = None
    #: P0-5: an explicit, narrow, OFF-BY-DEFAULT operator assertion that
    #: NOTHING else writes to this specific broker account's position
    #: outside Signal Copier -- no manual intervention, no other
    #: automated writer, no direct dashboard/API trade at the broker, no
    #: corporate action that changes share count without an offsetting
    #: fill this service sees. See app/engine.py's
    #: `_reconcile_before_plain_close` for exactly what setting this to
    #: True allows: a plain (non-managed_lifecycle) account's CLOSE to
    #: proceed against this service's own locally tracked position alone,
    #: with no live broker-side reconciliation, WHEN the broker adapter
    #: also has no verified position-readback capability at all (see
    #: `BrokerAdapter.has_position_readback_capability`). `False` (the
    #: default) is a hard block, not a silent gap being accepted: an
    #: unreconciled, unqualified plain close is REJECTED outright rather
    #: than proceeding on a possibly-stale local projection. This is a
    #: real operational promise the operator is making about this one
    #: account, not a config convenience to flip to make a rejection go
    #: away -- see README.md's "Exclusive-writer qualification" section
    #: for what it means and its risk before setting it True.
    exclusive_writer_qualified: bool = False

    def __post_init__(self) -> None:
        if self.management_recipe is None:
            self.management_recipe = (
                ManagementRecipe.FULL_MANAGED_LIFECYCLE
                if self.managed_lifecycle
                else ManagementRecipe.PLAIN_UNMANAGED
            )
        elif isinstance(self.management_recipe, str):
            self.management_recipe = ManagementRecipe(self.management_recipe)
