"""ManagedProgram: AD-14 "Managed-program setup" -- a tenant's own
inactive PAMM/MAM program configuration. See
dashboard_spec/screens/AD-14.md's F-MANAGED-PROGRAM form for the full
contract this implements a bounded slice of.

"UI built even when approvals absent; real cash handling remains
through broker" (AD-14's own acceptance text) -- this model never
touches money. It records configuration references only
(allocation/NAV/dealing/fee policy ids); the actual accounting math
(app/services/pamm_accounting.py, Phase 10) is a completely separate,
already-real module this config never calls.

Deliberately mutable while DRAFT, like ContentDocument/Product's own
precedent. Submitting for review is this screen's own "Confirm" action
("Submit program release review, not custody or deposit action") --
actual program activation/release is a separate, not-yet-built
admission decision.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import ARRAY, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ManagedProgramMode(str, enum.Enum):
    PAMM = "pamm"
    MAM = "mam"


class ManagedProgramState(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED_FOR_REVIEW = "SUBMITTED_FOR_REVIEW"


class ManagedProgram(Base):
    __tablename__ = "managed_programs"

    program_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    program_name: Mapped[str] = mapped_column(String, nullable=False)
    broker_program_id: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[ManagedProgramMode] = mapped_column(Enum(ManagedProgramMode, native_enum=False), nullable=False)
    allocation_policy_id: Mapped[str] = mapped_column(String, nullable=False)
    nav_policy_id: Mapped[str] = mapped_column(String, nullable=False)
    dealing_schedule_id: Mapped[str] = mapped_column(String, nullable=False)
    fee_policy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    agreement_evidence_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    state: Mapped[ManagedProgramState] = mapped_column(
        Enum(ManagedProgramState, native_enum=False), nullable=False, default=ManagedProgramState.DRAFT
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
