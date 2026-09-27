"""AD-08 "Release and change approvals" -- the real query/command
service backing the release-review queue. See
dashboard_spec/screens/AD-08.md for the full screen contract this
implements a bounded slice of.

Completes the half of AD-07 deliberately left unbuilt: "Confirm: Submit
version for review; not directly publish." Approving a review moves a
Product from DRAFT to APPROVED -- never to PUBLISHED, which stays a
wholly separate, later admission decision this module has no authority
over.

Both required gates from the spec are enforced for real, not merely
recorded as checkbox text:

- "Cannot approve own proposal where independence required" -- the
  reviewer's user_id must differ from the review's own proposer_user_id,
  checked at decision time, not just assumed from role.
- "Approval expires/invalidates on material changes" -- the product's
  CURRENT revision must still equal the revision recorded at proposal
  time, or the decision is refused with StaleReviewTargetError, forcing
  a fresh review request rather than silently approving a version the
  reviewer never actually saw.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.product import Product, ProductLifecycleState
from app.models.release_review import ReleaseReview, ReleaseReviewState
from app.services.product_admin import compute_publication_blockers


class ReviewNotEligibleError(Exception):
    """Raised when a product with outstanding publication blockers is
    submitted for review -- "A version with no sleeves/evidence may be
    saved but cannot pass release" (AD-07's own acceptance text) applies
    with equal force to requesting review, not only to publication."""


class SelfReviewNotAllowedError(Exception):
    pass


class StaleReviewTargetError(Exception):
    """Raised when the product has been edited since this review was
    requested -- the reviewer would otherwise be approving a version
    they never actually saw."""


class InvalidReviewDecisionError(Exception):
    pass


_VALID_DECISIONS = {"approve", "reject", "request_changes"}


def list_release_reviews(session: Session, *, tenant_id: str) -> list[ReleaseReview]:
    """Correctly returns an empty list before any review has ever been
    requested -- AD-08's own empty state: "No release reviews are
    queued."."""
    return list(
        session.scalars(
            select(ReleaseReview).where(ReleaseReview.tenant_id == tenant_id).order_by(ReleaseReview.created_at.desc())
        ).all()
    )


def get_release_review(session: Session, release_review_id: str, *, tenant_id: str) -> ReleaseReview | None:
    """Scoped not-found: a cross-tenant ID and a nonexistent one are
    indistinguishable, matching product_admin.get_product's own
    pattern."""
    review = session.get(ReleaseReview, release_review_id)
    if review is None or review.tenant_id != tenant_id:
        return None
    return review


def request_release_review(
    session: Session,
    *,
    tenant_id: str,
    product: Product,
    proposer_user_id: str,
    evidence_manifest_id: str,
    audience_policy_id: str,
    scheduled_at: datetime | None = None,
) -> ReleaseReview:
    if not evidence_manifest_id.strip() or not audience_policy_id.strip():
        raise InvalidReviewDecisionError("evidence_manifest_id and audience_policy_id are required")
    if product.lifecycle_state != ProductLifecycleState.DRAFT:
        raise ReviewNotEligibleError(
            f"product {product.product_id} is already {product.lifecycle_state.value}, not DRAFT"
        )

    blockers = compute_publication_blockers(session, product)
    if blockers:
        raise ReviewNotEligibleError(
            f"product {product.product_id} still has outstanding publication blockers: {', '.join(blockers)}"
        )

    #: Moves the product out of DRAFT so a second, concurrent review
    #: request against the same still-unreviewed draft is refused above
    #: rather than silently creating a duplicate queued review.
    product.lifecycle_state = ProductLifecycleState.VALIDATED

    review = ReleaseReview(
        tenant_id=tenant_id,
        product_id=product.product_id,
        object_revision_reviewed=product.revision,
        proposer_user_id=proposer_user_id,
        evidence_manifest_id=evidence_manifest_id.strip(),
        audience_policy_id=audience_policy_id.strip(),
        scheduled_at=scheduled_at,
    )
    session.add(review)
    session.flush()
    return review


def decide_release_review(
    session: Session,
    review: ReleaseReview,
    product: Product,
    *,
    reviewer_user_id: str,
    decision: str,
    reason: str,
) -> ReleaseReview:
    if decision not in _VALID_DECISIONS:
        raise InvalidReviewDecisionError(f"unknown decision: {decision!r}")
    if not reason.strip():
        raise InvalidReviewDecisionError("a decision reason is required")
    if reviewer_user_id == review.proposer_user_id:
        raise SelfReviewNotAllowedError("the proposer cannot also be this review's independent reviewer")
    if product.revision != review.object_revision_reviewed:
        raise StaleReviewTargetError(
            f"product is now at revision {product.revision}, but this review targeted revision "
            f"{review.object_revision_reviewed}; request a fresh review"
        )

    if decision == "approve":
        #: "Preview: Recompute all gates" -- re-check blockers now, not
        #: only at proposal time, since the product's revision-invariance
        #: check above is what already prevents a plain content change,
        #: but a fresh recompute is the honest read of "recompute all
        #: gates" rather than trusting the proposal-time result.
        blockers = compute_publication_blockers(session, product)
        if blockers:
            raise ReviewNotEligibleError(
                f"product {product.product_id} now has outstanding publication blockers: {', '.join(blockers)}"
            )
        review.state = ReleaseReviewState.APPROVED
        product.lifecycle_state = ProductLifecycleState.APPROVED
    elif decision == "reject":
        review.state = ReleaseReviewState.REJECTED
        #: Back to DRAFT -- a rejected proposal is revisable, not a dead
        #: end; the proposer can edit and submit a fresh review.
        product.lifecycle_state = ProductLifecycleState.DRAFT
    else:
        review.state = ReleaseReviewState.CHANGES_REQUESTED
        product.lifecycle_state = ProductLifecycleState.DRAFT

    review.reviewer_user_id = reviewer_user_id
    review.reason = reason.strip()
    review.decided_at = datetime.now(timezone.utc)
    session.flush()
    return review
