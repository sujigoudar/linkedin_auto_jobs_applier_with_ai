"""Data model for managed-lifecycle position protection — the fallback path
for brokers/accounts that can't submit entry + stop + take-profit as one
atomic bracket/OCO order (see app/lifecycle/manager.py's module docstring
for the full design and why).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime

from app.models import AssetClass, Side


class ProtectionStatus(str, enum.Enum):
    UNPROTECTED = "unprotected"
    STOP_PENDING = "stop_pending"
    STOP_CONFIRMED = "stop_confirmed"


class TargetAction(str, enum.Enum):
    SELL = "sell"
    TIGHTEN_STOP = "tighten_stop"
    ACTIVATE_TRAIL = "activate_trail"


@dataclass
class Target:
    """A logical, app-managed profit action — not necessarily a standing broker order.

    `reduce_fraction` is a fraction of the *originally planned* quantity (not
    whatever happens to be owned when it fires), matching the worked example
    in the design: "Target 1: sell 25% of the original copied allocation."
    """

    trigger_price: float
    action: TargetAction = TargetAction.SELL
    reduce_fraction: float | None = None  # required when action == SELL
    fired: bool = False


@dataclass
class TrailingPolicy:
    activate_at_price: float | None = None
    trail_distance: float = 0.0
    active: bool = False
    floor_price: float | None = None  # current desired floor once active


@dataclass
class PositionPlan:
    """Computed before the entry is submitted. Sizing here is a plan, not a
    guarantee — the lifecycle tracks what actually filled separately."""

    account_id: str
    symbol: str
    side: Side  # the entry side: BUY for long, SELL for short
    planned_quantity: float
    asset_class: AssetClass = AssetClass.CRYPTO
    initial_stop: float | None = None
    targets: list[Target] = field(default_factory=list)
    trailing: TrailingPolicy | None = None
    time_exit: datetime | None = None
    max_risk: float | None = None


@dataclass
class StopRecord:
    desired_price: float | None = None
    submitted_price: float | None = None
    broker_confirmed_price: float | None = None
    broker_order_id: str | None = None
    protected_quantity: float = 0.0
    status: ProtectionStatus = ProtectionStatus.UNPROTECTED


@dataclass
class PositionLifecycle:
    plan: PositionPlan
    confirmed_owned_quantity: float = 0.0
    stop: StopRecord = field(default_factory=StopRecord)
    closed: bool = False
    # Halt state lives on CloseArbiter, not here — it's the single source of
    # truth (app/lifecycle/close_arbiter.py's is_halted()/halt_reason()), so
    # this lifecycle and the arbiter's ledger can never disagree about it.

    @property
    def exit_side(self) -> Side:
        return Side.SELL if self.plan.side == Side.BUY else Side.BUY

    @property
    def key(self) -> tuple[str, str]:
        return (self.plan.account_id, self.plan.symbol)
