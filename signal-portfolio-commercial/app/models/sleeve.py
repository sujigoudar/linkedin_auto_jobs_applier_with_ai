"""A Sleeve: one qualified provider/analyst/strategy/horizon/parser/policy
combination -- the "unit of combination" spec/docs/03_portfolio_research_and_selection.md
requires instead of merging raw signals into one undifferentiated feed:
"Normalize each licensed component into a sleeve with provider, analyst,
strategy/horizon, asset/product, parser version, execution policy, cost
model, capacity, risk unit and history origin. Maintain its independent
virtual book even when the account holds the same instrument through
several sleeves."

Tenant-scoped like everything else customer-facing (app/models/tenancy.py)
-- a sleeve definition belongs to whichever tenant qualified/admitted it,
and is subject to the same row-level security as customer_profiles and
ledger_entries (see app/db.py's `_TENANT_SCOPED_TABLES`).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Sleeve(Base):
    __tablename__ = "sleeves"

    sleeve_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    provider: Mapped[str] = mapped_column(String, nullable=False)
    analyst: Mapped[str] = mapped_column(String, nullable=False)
    strategy_horizon: Mapped[str] = mapped_column(String, nullable=False)
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
    parser_version: Mapped[str] = mapped_column(String, nullable=False)
    execution_policy_id: Mapped[str] = mapped_column(String, nullable=False)
    cost_model_id: Mapped[str] = mapped_column(String, nullable=False)
    #: A capacity ceiling (e.g. maximum notional this sleeve's own
    #: liquidity/turnover profile can absorb) -- Numeric-precision string
    #: identifier of a capacity policy, not a bare float; the actual
    #: capacity-stress computation is Phase 04 work not yet built.
    capacity_policy_id: Mapped[str] = mapped_column(String, nullable=False)
    risk_unit_id: Mapped[str] = mapped_column(String, nullable=False)
    history_origin: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
