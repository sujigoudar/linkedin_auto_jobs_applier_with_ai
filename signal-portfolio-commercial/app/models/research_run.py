"""ResearchRun: AD-04 "Portfolio Lab builder" -- the persisted
declaration of a candidate universe, constraints and walk-forward
design. See dashboard_spec/screens/AD-04.md for the full screen
contract this implements a bounded slice of.

Deliberately a plain, mutable draft (like `Product`, not append-only
like `PortfolioVersion`) -- "Save run draft" is explicitly iterative,
edited before "Confirm: Enqueue research job only." Enqueueing/running
the actual research job needs a job queue and real authorized
historical sleeve data this environment doesn't have (see
app/services/portfolio_research.py's own docstring on what recipes are
and are not implemented) -- this model only ever gets as far as a
saved, previewed declaration; nothing here starts a job.

Tenant-scoped and RLS-protected like the other tenant tables.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import ARRAY, DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ResearchRun(Base):
    __tablename__ = "research_runs"

    research_run_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    #: The declared candidate universe -- real Sleeve rows this tenant
    #: already owns (app/models/sleeve.py), not a separately versioned
    #: "universe" object (no such model exists yet).
    sleeve_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    #: Only "equal_capital" is actually implemented
    #: (app/services/portfolio_research.py's `equal_weight_recipe`) --
    #: recording any other recipe name here is allowed at save time (a
    #: draft can be incomplete) but is always flagged as a real blocker
    #: by compute_research_run_preview, never silently accepted as done.
    recipes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)

    subset_min: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    subset_max: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    cash_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=1500)
    max_sleeve_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=3500)
    max_cluster_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=5000)

    train_sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=252)
    test_sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=63)
    holdout_fraction: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=Decimal("0.20"))

    cost_scenario_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    resource_profile_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
