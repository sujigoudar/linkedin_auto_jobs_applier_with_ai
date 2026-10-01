"""Track 34: the release-maturity ladder `/system/readiness` reports as
`release_status` -- the honest replacement for that field's prior
placeholder (`None`/`"not_tracked"`, because this build never computed
it for real).

This is NOT a new state machine bolted on next to `Product.lifecycle_state`
(app/models/product.py) and `ReleaseReview` (app/models/release_review.py,
AD-08's own "release is not publication" decision record) -- those two
are already this codebase's real, load-bearing release pipeline:

    DRAFT --(request_release_review)--> VALIDATED --(decide_release_review)--> APPROVED/DRAFT
    APPROVED --(a future, separate publication/admission step; see
               app/services/release_review.py's own docstring: "Approving
               a review moves a Product from DRAFT to APPROVED -- it never
               moves it to PUBLISHED")--> PUBLISHED

`ReleaseStage` is a pure, total, 1:1 renaming of that existing ladder onto
the vocabulary this track's brief asked for -- it introduces no new
column, no new table, and no new transition. Mapping:

- RESEARCH_ONLY -- `DRAFT`. Still being drafted/edited; no independent
  reviewer has ever evaluated it (or a prior review was REJECTED/sent
  back CHANGES_REQUESTED, which `decide_release_review` already returns
  the product to DRAFT for).
- SHADOW -- `VALIDATED`. Submitted for independent review
  (`request_release_review`'s own gate: zero outstanding
  `compute_publication_blockers`) and awaiting a real reviewer decision.
  Evaluated against this build's real release gates, but with zero
  customer-facing exposure -- the same "run the real decision pipeline
  without any live effect" shape signal-copier's own Track 17 shadow
  mode uses for a different object (a single signal), just at this
  object's own (a Product's) grain.
- LIMITED_LIVE -- `APPROVED`. An independent reviewer has approved this
  exact revision (`ReleaseReview.state == APPROVED`,
  `object_revision_reviewed == product.revision` -- a later edit makes
  this stale, see `release_review.py`'s `StaleReviewTargetError`) -- but
  publication/admission (the public catalog, `list_published_products`)
  is a wholly separate, later decision this stage has not reached yet.
  "Limited" names exactly what is real at this stage: internal gates
  cleared, no external distribution yet.
- FULLY_RELEASED -- `PUBLISHED`. Admitted to the public catalog
  (`app.services.product_admin.list_published_products`). Note this
  state is reachable in the data model but, as of this track, no
  command in this codebase ever sets it -- `product_admin.py` and
  `release_review.py` both document this ("Publication remains a wholly
  separate, later admission decision ... this model has no authority
  over"). `compute_release_stage` reports it honestly if it is ever
  reached; it does not claim a product has reached it when it has not.

Deliberately does NOT read `app/services/trading_authority.py` or fold
its answer in here -- AD-22's own panel contract is explicit: "Do not
collapse payment, connection, rights and trading authority into one
active badge." Release maturity (can this be shown to anyone) and
trading authority (can this route a real order) are reported as two
independent fields for exactly that reason; a product can be
FULLY_RELEASED with `trading_authority.qualified=False` (e.g. alerts-
only, or copying with no activated mandate pipeline -- see that
module's own docstring).
"""
from __future__ import annotations

import enum

from app.models.product import Product, ProductLifecycleState


class ReleaseStage(str, enum.Enum):
    RESEARCH_ONLY = "RESEARCH_ONLY"
    SHADOW = "SHADOW"
    LIMITED_LIVE = "LIMITED_LIVE"
    FULLY_RELEASED = "FULLY_RELEASED"


_STAGE_BY_LIFECYCLE_STATE: dict[ProductLifecycleState, ReleaseStage] = {
    ProductLifecycleState.DRAFT: ReleaseStage.RESEARCH_ONLY,
    ProductLifecycleState.VALIDATED: ReleaseStage.SHADOW,
    ProductLifecycleState.APPROVED: ReleaseStage.LIMITED_LIVE,
    ProductLifecycleState.PUBLISHED: ReleaseStage.FULLY_RELEASED,
}


def compute_release_stage(product: Product) -> ReleaseStage:
    """Total over every `ProductLifecycleState` value that exists today
    -- a `Product` row always has a real `lifecycle_state`, so this never
    needs to return `None`. (`/system/readiness` itself still reports
    `release_status: None` at the tenant-rollup level when the tenant has
    no products at all to assess -- see app/main.py -- but that is a
    "nothing to assess" case this function, which is always handed a
    real product, never needs to express.)
    """
    return _STAGE_BY_LIFECYCLE_STATE[product.lifecycle_state]
