"""PortfolioVersion: the immutable, versioned capital-weight allocation
across sleeves plus cash, per spec/docs/03_portfolio_research_and_selection.md:
"A portfolio version contains an ordered eligible-universe snapshot,
selected sleeves, basis-point capital weights plus cash, risk-policy
references, asset/channel allowlists, portfolio/cluster concentration
limits, target volatility/stress constraints, maximum subscriber
capacity, research cutoff, report IDs, consent/disclosure version,
deployment artifact and released selector envelope. Every component
membership or weight change creates a new immutable version. Historical
membership is never overwritten."

Deliberately bounded to what's buildable without real licensed sleeve
history: the immutable versioned record itself, its sleeve/weight
membership, and admission gating (app/services/portfolio_rights.py).
Concentration/volatility/stress CONSTRAINT CHECKING against real
candidate data (the rest of Phase 04) still needs real authorized
sleeve history this environment doesn't have, and remains out of scope
here -- this model exists so that work has somewhere real to persist
its output once that data exists, not so this build can claim the
research pipeline is complete.

Append-only like ledger_entries and publication_intents: a portfolio
version is never edited or deleted once created (app/db.py's
`enforce_append_only`) -- "Historical membership is never overwritten"
is a database-level guarantee here, not just an application convention.
Tenant-scoped and RLS-protected like the other tenant tables.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PortfolioVersion(Base):
    __tablename__ = "portfolio_versions"
    #: Enforced at the database level (see
    #: alembic/versions/a2c7e4f91b35_portfolio_versions_version_number_unique.py)
    #: so two concurrent draft-creation calls for the same
    #: (tenant_id, portfolio_id) can never both insert the same
    #: version_number -- "Historical membership is never overwritten"
    #: (this module's own docstring above) is a real DB guarantee, not
    #: just an application-level max()+1 convention.
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "portfolio_id", "version_number",
            name="uq_portfolio_versions_tenant_portfolio_version_number",
        ),
    )

    portfolio_version_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    #: A stable identity across versions (e.g. "P02-equity-swing") --
    #: version_number increases within it; the pair is how "a new
    #: version of the same portfolio" is expressed, never by editing an
    #: existing row.
    portfolio_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    cash_weight: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    research_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_subscriber_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    consent_disclosure_version: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class PortfolioVersionSleeve(Base):
    """One sleeve's membership and weight within a specific
    PortfolioVersion -- a row here is as immutable as its parent; a
    weight change creates new rows under a new `PortfolioVersion`, never
    an update to an existing one."""

    __tablename__ = "portfolio_version_sleeves"

    portfolio_version_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_versions.portfolio_version_id"), primary_key=True
    )
    sleeve_id: Mapped[str] = mapped_column(ForeignKey("sleeves.sleeve_id"), primary_key=True)
    weight: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    #: A database-level RLS backstop (ADR-0001), not this table's own
    #: scoping mechanism -- app/services/portfolio_rights.py already
    #: scopes correctly via an inner join through `PortfolioVersion`
    #: (which IS tenant-scoped). Backfilled from that same join path by
    #: alembic/versions/85f9e0e6c123_publication_intent_and_sleeve_tenant_id.py.
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
