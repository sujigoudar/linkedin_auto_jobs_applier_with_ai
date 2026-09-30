"""OperatingCost: Track 11 "Cost of subscribed services + infrastructure
metrics" -- the platform's own (PLATFORM book) cost-side record, sitting
alongside `app/models/billing.py`'s already-real revenue side of the
same P&L (docs/adr/0004-four-book-economic-ledger.md). AD-12 "Business
economics and royalties"'s "Cost attribution" panel previously read
`UNSUPPORTED -- no platform/data/cloud cost model exists in this build`;
this table is that model.

Deliberately NOT a live billing-API integration: this build has no real
AWS/OCI/Stripe/Telegram/OpenAI/Anthropic credentials to pull a real bill
from, and fabricating one would be exactly the kind of "computed from
admittedly incomplete inputs" the rest of this codebase refuses to do
(see app/services/business_economics.py's own docstring on the Margin
panel). What IS real here: a clean, tenant-scoped, owner-gated place to
RECORD a cost a human already knows (from an actual invoice/statement),
either one row at a time (`entry_source=manual`) or via a bounded CSV
import (`entry_source=csv_import`) -- see app/services/operating_cost.py.
Live billing-API pulls are a documented, explicitly scoped-out follow-up
(same module's docstring), never something this pass fakes.

Never touches trading P&L: this table has no FK to `app/models/
ledger.py`, and nothing in app/services/operating_cost.py or
app/services/business_economics.py reads a LedgerEntry to compute a
cost or a margin figure. See tests/test_books_separation.py.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OperatingCostCategory(str, enum.Enum):
    SUBSCRIPTION = "subscription"
    INFRASTRUCTURE = "infrastructure"
    API_USAGE = "api_usage"
    LLM_USAGE = "llm_usage"
    DATA_FEED = "data_feed"
    OTHER = "other"


class OperatingCostCadence(str, enum.Enum):
    ONE_TIME = "one_time"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class OperatingCostSource(str, enum.Enum):
    #: A human entered this row directly (AD-12's own cost form), from a
    #: real invoice/statement/receipt they are looking at.
    MANUAL = "manual"
    #: A human uploaded a CSV export from a real vendor billing page/
    #: invoice -- still ultimately a human-supplied fact, just entered in
    #: bulk. Never a live API pull (no such integration exists here).
    CSV_IMPORT = "csv_import"


class OperatingCost(Base):
    __tablename__ = "operating_costs"

    cost_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    category: Mapped[OperatingCostCategory] = mapped_column(
        Enum(OperatingCostCategory, native_enum=False), nullable=False
    )
    #: The vendor/service this cost is for -- "Telegram", "Slack",
    #: "Twitter API", "OpenAI", "Anthropic", "AWS", "Interactive Brokers",
    #: etc. Free text: this build has no fixed vendor catalog and adding
    #: one is not this pass's job.
    vendor: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)

    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False, default="usd")

    cadence: Mapped[OperatingCostCadence] = mapped_column(
        Enum(OperatingCostCadence, native_enum=False), nullable=False, default=OperatingCostCadence.MONTHLY
    )
    #: The real billing period this row covers. Required (never "assume
    #: this month") so margin-by-period (app/services/business_economics.py)
    #: has an honest period to join revenue against.
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: Which product/sleeve/account this cost supports -- nullable: many
    #: real costs (base hosting, a platform-wide Telegram bot token) are
    #: not attributable to one thing, and forcing a guess here would be
    #: exactly the "computed from admittedly incomplete inputs" this
    #: codebase refuses to do elsewhere. A plain id/name reference, not a
    #: FK -- same "no cross-tenant IDs, validated at the service layer"
    #: precedent as app/models/incident.py's own affected_object_id.
    cost_center: Mapped[str | None] = mapped_column(String, nullable=True)

    entry_source: Mapped[OperatingCostSource] = mapped_column(
        Enum(OperatingCostSource, native_enum=False), nullable=False, default=OperatingCostSource.MANUAL
    )
    #: True only for a cost this pass could derive from real, already-
    #: logged usage (see app/services/operating_cost.py's own docstring
    #: on LLM usage) rather than a human-entered invoice figure. Always
    #: rendered with an "estimated from logged usage, not billing-
    #: reconciled" label -- never presented as an actual bill.
    is_usage_estimate: Mapped[bool] = mapped_column(nullable=False, default=False)

    created_by_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
