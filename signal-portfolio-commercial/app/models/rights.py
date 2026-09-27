"""RightsGrant: the source-rights registry record. Field names, types and
enum values are taken directly from `spec/contracts/RightsGrant.schema.json`
(the package's own contract) -- not redesigned -- so a grant recorded here
validates against that schema unchanged.

See spec/docs/01_rights_legal_and_launch_gates.md: a grant's `uses` are
independent (private research, derived-product research, public
performance display, commercial alerts, automated third-party
publication, discretionary management, model training) -- granting one
use never implies another. A personal-use subscription is not a
commercial grant. Provider names in `source_id` can remain a private
identifier internally; this table is never itself exposed to a customer.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import ARRAY, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RightsStatus(str, enum.Enum):
    UNKNOWN = "UNKNOWN"
    GRANTED = "GRANTED"
    DENIED = "DENIED"
    REVOKED = "REVOKED"


class RightsUse(str, enum.Enum):
    PRIVATE_RESEARCH = "PRIVATE_RESEARCH"
    DERIVED_RESEARCH = "DERIVED_RESEARCH"
    PUBLIC_METRICS = "PUBLIC_METRICS"
    COMMERCIAL_ALERTS = "COMMERCIAL_ALERTS"
    AUTOMATED_PUBLICATION = "AUTOMATED_PUBLICATION"
    MANAGED_ACCOUNTS = "MANAGED_ACCOUNTS"
    MODEL_PROCESSING = "MODEL_PROCESSING"


class RightsGrant(Base):
    __tablename__ = "rights_grants"

    grant_id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    grantee_entity: Mapped[str] = mapped_column(String, nullable=False)
    contract_hash: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[RightsStatus] = mapped_column(Enum(RightsStatus, native_enum=False), nullable=False)

    # ARRAY(String) is a real Postgres column type (not a JSON blob) --
    # this table only ever runs against Postgres (see app/db.py), so
    # there's no SQLite portability constraint pulling this toward JSON.
    uses: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    channels: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    jurisdictions: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    assets: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)

    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    attribution_policy_id: Mapped[str] = mapped_column(String, nullable=False)
    wind_down_policy_id: Mapped[str] = mapped_column(String, nullable=False)
    review_id: Mapped[str] = mapped_column(String, nullable=False)
