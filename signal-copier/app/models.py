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
