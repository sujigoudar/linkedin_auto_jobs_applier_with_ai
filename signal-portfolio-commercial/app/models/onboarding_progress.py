"""OnboardingProgress: the durable, per-customer high-water mark for
`app/services/onboarding.py`'s own `OnboardingStage` sequence.

Before this table existed, `OnboardingStage`/`advance()` enforced
correct ordering in isolation but were never actually consulted by any
call site -- a request that went straight to
`app/services/copy_mandate.py`'s mandate-draft creation was not stopped
by them at all. This table is what lets a real call site ask "how far
has this customer actually gotten", persistently, across requests.

One row per (tenant_id, user_id) -- same shape as
`EligibilityAssessment`/`NotificationPreferences`: a customer's own
onboarding progress is a single mutable row, not an append-only ledger,
since only the CURRENT high-water mark is ever read.

`stage` only ever moves forward (`app/services/onboarding_progress.py`'s
`sync_onboarding_progress` uses `advance()` itself to enforce this) --
never rewritten backwards just because a later signal regresses (e.g. a
subscription lapsing after entitlement was once verified). "Every
intermediate state is resumable" (spec/docs/09's own onboarding text)
means the customer's furthest-reached milestone is never forgotten;
whether they may take a NEW action *right now* is a separate, live
check (see `app/services/onboarding_progress.py`'s own docstring).

Tenant-scoped and RLS-protected like every other tenant-scoped table
(`app/db.py`'s `_TENANT_SCOPED_TABLES`), and compound-FK'd to
`memberships` like `EligibilityAssessment`/`NotificationPreferences`'s
own precedent.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.services.onboarding import OnboardingStage


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OnboardingProgress(Base):
    __tablename__ = "onboarding_progress"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_onboarding_progress_membership",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, primary_key=True)

    stage: Mapped[OnboardingStage] = mapped_column(
        Enum(OnboardingStage, native_enum=False), nullable=False, default=OnboardingStage.SIGNED_UP
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
