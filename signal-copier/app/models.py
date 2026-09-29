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
    #: E03 (bounded): an opt-in notional-exposure ceiling for this account
    #: (None = no limit, the existing default behavior). See
    #: app/capital_allocator.py's module docstring for exactly what this
    #: does and doesn't enforce -- only checked when the admitting signal
    #: carries a price.
    max_notional_exposure: Optional[float] = None
