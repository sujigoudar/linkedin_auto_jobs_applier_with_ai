"""ReleaseReview: AD-08 "Release and change approvals" -- the durable
record of "propose this Product revision for release, then have an
independent reviewer decide" that AD-07 itself deliberately left
unbuilt ("Confirm: Submit version for review; not directly publish").

Approving a review moves a Product from DRAFT to APPROVED -- it never
moves it to PUBLISHED. Publication remains a wholly separate, later
admission decision (app/services/publication_admission.py) this model
has no authority over, matching AD-08's own "release is not
publication."

Tenant-scoped and RLS-protected like the other tenant tables (added to
app/db.py's `_TENANT_SCOPED_TABLES`). Deliberately mutable, like
`Product` -- a review's `state`/`reason`/`reviewer_user_id` genuinely
change as the decision is recorded; only the identity of what was
reviewed (`object_revision_reviewed`) is fixed at proposal time, so a
later material change to the product can be detected and the review
invalidated rather than silently re-approved against a moving target.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ReleaseReviewState(str, enum.Enum):
    QUEUED = "QUEUED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"


class ReleaseReview(Base):
    __tablename__ = "release_reviews"

    release_review_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    product_id: Mapped[str] = mapped_column(ForeignKey("products.product_id"), nullable=False, index=True)
    #: The Product's own `revision` at the moment this review was
    #: requested -- compared against the product's CURRENT revision at
    #: decision time so an approval can never silently apply to a
    #: version the reviewer never actually saw.
    object_revision_reviewed: Mapped[int] = mapped_column(Integer, nullable=False)

    proposer_user_id: Mapped[str] = mapped_column(String, nullable=False)
    #: Required at proposal time -- "complete mapped tests/rights/data/
    #: report" (F-RELEASE's own field help). An id reference, not a raw
    #: document: the actual gate check this build can perform for real is
    #: `compute_publication_blockers` being empty, which is checked at
    #: both proposal and decision time (see app/services/release_review.py).
    evidence_manifest_id: Mapped[str] = mapped_column(String, nullable=False)
    audience_policy_id: Mapped[str] = mapped_column(String, nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    state: Mapped[ReleaseReviewState] = mapped_column(
        Enum(ReleaseReviewState, native_enum=False), nullable=False, default=ReleaseReviewState.QUEUED
    )
    reviewer_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
