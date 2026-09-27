"""Real HTTP routes + Jinja2 templates for the dashboard build, per
dashboard_spec/screens/*.md. Each screen here is a bounded, honestly
scoped slice of its full spec -- see each route's own docstring for
what is and is not implemented yet.

- AD-07 "Products and portfolio versions" (admin) + PU-02 "Portfolio
  catalog" (public): create/save/reload a Product draft, see its exact
  publication blockers, and a truthfully empty public catalog until
  something is really published. Submitting a zero-blocker draft for
  review (AD-07's own "Confirm: Submit version for review; not directly
  publish") is now real too -- see AD-08 below. Actual publication
  remains a distinct, separately reviewed admission decision
  (app/services/publication_admission.py) neither screen has authority
  over.
- AD-02 "Rights and service approvals": a read-only grant register.
  Create/attach-evidence/approve are NOT implemented -- they need
  private document upload/malware-scan infrastructure and an audit-
  logged approval workflow that don't exist yet; building an empty
  create form around them would be exactly the kind of scaffolding
  around nothing this build avoids.
- AD-03 "Research universe and sleeves": create/list real sleeve
  lineage records (reusing the Phase 04 `Sleeve` model as-is). No
  distinct DRAFT/QUALIFIED lifecycle or "Confirm: qualified status"
  step -- that needs real coverage/overlap statistics against actual
  historical data this environment doesn't have.
- AD-01 "Commercial operations overview": a real cross-subsystem
  summary (release blockers per product, active subscriptions,
  unknown-state publications). "Open incidents" has no backing model
  at all yet, so it's shown as unsupported, never a fabricated zero.
- AD-08 "Release and change approvals": a real release-review queue.
  Requesting review (from AD-07) is refused unless the product's
  publication blockers are genuinely empty; deciding enforces a real
  independent-reviewer gate (the reviewer cannot be the proposer) and
  refuses a stale review (the product changed since it was requested).
  Approving moves a Product to APPROVED, never to PUBLISHED -- release
  is not publication.
- AD-04 "Portfolio Lab builder": create/save a real research-run
  declaration (candidate universe drawn from real Sleeve rows) and a
  real preview that computes the exact candidate denominator (reusing
  app/services/portfolio_research.py's own combinatorics). "Confirm:
  Enqueue research job only" is NOT implemented -- there is no job
  queue, and only the "equal_capital" recipe actually exists; any other
  requested recipe is a real, named preview blocker, never silently
  accepted.
- PU-01 "Public home": the anonymous landing page. Shares PU-02's own
  `list_published_products` query; a real, computed "Service status"
  checklist (environment tag, whether billing is genuinely connected)
  rather than a decorative "all systems operational" banner.
- PU-08 "Help and compatibility guide": a real compatibility directory
  grounded in the already-audited state of the publisher adapters.
- PU-03 "Portfolio detail": one published product's real identity,
  risk/capacity facts and methodology/research document references.
  No NAV/marks-history model exists for a standard portfolio product,
  so the performance/drawdown panel always honestly reports the track
  record as unavailable, never a guessed curve. A draft/unpublished
  slug returns a scoped 404, never its content.
- PU-05 "Pricing and service compatibility": real plan-cards data
  (app/models/billing.py's TEST_MODE_MONTHLY_PRICE_CENTS), but ONLY
  once billing is genuinely connected -- those fixture prices are
  documented as "not user-approved", so this deployment (billing
  unconfigured) truthfully renders PU-05's own empty state instead.
- ID-04 "Service eligibility onboarding": a customer saves real
  residence/service facts; eligibility is always computed live from
  those facts plus the real published-product catalog, never a stored
  verdict. Only "US" residence is a supported jurisdiction and entity
  onboarding is a real, named UNSUPPORTED reason -- neither is silently
  approved.
- AD-16 "Staff roles and access reviews": real invite/revoke of a
  colleague's operator role (reuses the existing `Membership` model --
  no new table). OWNER can never be granted through this form nor
  revoked through it, and a caller can never revoke their own
  membership. Session audit is NOT implemented -- there is no session/
  audit-log store in this build.
- CU-14 "Support and incident case": a customer's own real support
  cases. `related_object_id` is validated against a real Subscription in
  the caller's own tenant -- never a cross-tenant id, even one that
  genuinely exists. Attachments are plain id references, not real
  uploaded/scanned files -- no malware-scan/upload pipeline exists yet.
- AD-09 "Publisher channels and strategies": a real "save inactive
  destination" (never sends a signal -- nothing in the publication
  pipeline reads this table). Reuses the existing `claim_writer`
  mechanism for real "one approved path per external account/strategy".
  Only `local_simulation` is accepted; every other mode is a named
  EXTERNAL_ENVIRONMENT_NOT_AUTHORIZED blocker, since no real platform
  credentials or sandbox access exist in this environment. "Verify
  read-only identity" and "Prepare qualification" are NOT implemented --
  both need real external connectivity this build doesn't have.
- CU-16 "API delivery, keys and exports": a customer's own real scoped
  API keys. Only a fixed, safe scope allowlist (no trading/admin scope)
  and a future expires_at are accepted; only a SHA-256 hash of the
  generated secret is ever persisted -- the raw secret is returned once,
  in the create response, and never again. Revocation is idempotent.
- AD-12 "Business economics and royalties": real booked revenue per
  currency, computed only from Subscription rows in a genuinely
  payment-recognized state (never LedgerEntry -- investment profits are
  not revenue). Refunds/royalties/cost attribution/margin are NOT
  implemented -- no such model exists, so each is rendered as an
  explicit UNSUPPORTED, never a fabricated zero or an incomplete margin.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_scope, get_db_session
from app.db import set_tenant_scope
from app.models.product import ServiceMode
from app.models.tenancy import MembershipRole
from app.services.auth import TenantScope
from app.services.eligibility import (
    InvalidEligibilityFactsError,
    evaluate_eligibility,
    get_eligibility_assessment,
    save_eligibility_facts,
)
from app.services.permissions import PermissionDenied, require_permission
from app.services.staff_access import (
    GRANTABLE_ROLES,
    CannotRevokeOwnerError,
    CannotRevokeSelfError,
    InvalidStaffGrantError,
    MembershipNotFoundError,
    invite_staff_member,
    list_staff_memberships,
    revoke_staff_member,
)
from app.services.api_key import (
    ApiKeyNotFoundError,
    InvalidApiKeyRequestError,
    generate_scoped_key,
    list_api_keys,
    revoke_api_key,
)
from app.services.business_economics import get_business_economics
from app.services.publisher_destination import (
    InvalidPublisherDestinationError,
    create_publisher_destination,
    list_publisher_destinations,
)
from app.services.support_case import InvalidSupportCaseError, create_support_case, list_support_cases
from app.services.product_admin import (
    InvalidProductDraftError,
    SlugAlreadyExistsError,
    StaleRevisionError,
    compute_publication_blockers,
    create_draft_product,
    get_product,
    list_products,
    list_published_products,
    update_product_draft,
)
from app.services.operations_overview import get_operations_overview
from app.services.release_review import (
    InvalidReviewDecisionError,
    ReviewNotEligibleError,
    SelfReviewNotAllowedError,
    StaleReviewTargetError,
    decide_release_review,
    get_release_review,
    list_release_reviews,
    request_release_review,
)
from app.services.public_site import (
    get_channel_compatibility,
    get_pricing_plans,
    get_published_portfolio_detail,
    get_service_status,
)
from app.services.research_run import (
    InvalidResearchRunError,
    compute_research_run_preview,
    create_research_run,
    get_research_run,
    list_research_runs,
)
from app.services.rights_registry import list_rights_grants
from app.services.sleeve_admin import InvalidSleeveDraftError, create_sleeve, list_sleeves

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_ALL_SERVICE_MODES = [mode.value for mode in ServiceMode]
_GRANTABLE_ROLE_VALUES = [role.value for role in GRANTABLE_ROLES]


def _require_product_admin(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_product_draft")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops")
def operations_overview_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """AD-01 "Commercial operations overview" -- see this route module's
    own docstring above for what's deliberately not built (Open
    incidents has no backing model, so it's rendered as unsupported)."""
    try:
        require_permission(scope.role, "view_operations_overview")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    set_tenant_scope(session, scope.tenant_id)
    overview = get_operations_overview(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(request, "ad01_overview.html", {"overview": overview})


@router.get("/ops/products")
def list_products_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_product_admin(scope)
    set_tenant_scope(session, scope.tenant_id)
    products = list_products(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(
        request,
        "ad07_products.html",
        {"products": products, "tenant_id": scope.tenant_id, "role": scope.role.value},
    )


@router.post("/ops/products")
def create_product_draft(
    request: Request,
    product_name: str = Form(...),
    slug: str = Form(...),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_product_admin(scope)
    set_tenant_scope(session, scope.tenant_id)
    try:
        product = create_draft_product(session, tenant_id=scope.tenant_id, product_name=product_name, slug=slug)
        session.commit()
    except (InvalidProductDraftError, SlugAlreadyExistsError) as exc:
        session.rollback()
        set_tenant_scope(session, scope.tenant_id)
        products = list_products(session, tenant_id=scope.tenant_id)
        return templates.TemplateResponse(
            request,
            "ad07_products.html",
            {
                "products": products,
                "tenant_id": scope.tenant_id,
                "role": scope.role.value,
                "error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse(url=f"/ops/products/{product.product_id}", status_code=303)


@router.get("/ops/products/{product_id}")
def product_detail_page(
    product_id: str,
    request: Request,
    conflict: int = 0,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_product_admin(scope)
    set_tenant_scope(session, scope.tenant_id)
    product = get_product(session, product_id, tenant_id=scope.tenant_id)
    if product is None:
        raise HTTPException(status_code=404, detail="not found")

    blockers = compute_publication_blockers(session, product)
    return templates.TemplateResponse(
        request,
        "ad07_product_detail.html",
        {
            "product": product,
            "blockers": blockers,
            "all_service_modes": _ALL_SERVICE_MODES,
            "conflict": bool(conflict),
        },
    )


@router.post("/ops/products/{product_id}")
def update_product_draft_route(
    product_id: str,
    request: Request,
    expected_revision: int = Form(...),
    product_name: str = Form(...),
    portfolio_version_id: str = Form(""),
    cash_bps: int = Form(...),
    service_modes: list[str] = Form([]),
    audience_policy_id: str = Form(""),
    research_report_id: str = Form(""),
    methodology_document_id: str = Form(""),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_product_admin(scope)
    set_tenant_scope(session, scope.tenant_id)
    product = get_product(session, product_id, tenant_id=scope.tenant_id)
    if product is None:
        raise HTTPException(status_code=404, detail="not found")

    try:
        update_product_draft(
            session,
            product,
            expected_revision=expected_revision,
            product_name=product_name,
            portfolio_version_id=portfolio_version_id or None,
            cash_bps=cash_bps,
            service_modes=service_modes,
            audience_policy_id=audience_policy_id or None,
            research_report_id=research_report_id or None,
            methodology_document_id=methodology_document_id or None,
        )
        session.commit()
    except StaleRevisionError:
        session.rollback()
        return RedirectResponse(url=f"/ops/products/{product_id}?conflict=1", status_code=303)
    except InvalidProductDraftError as exc:
        session.rollback()
        set_tenant_scope(session, scope.tenant_id)
        product = get_product(session, product_id, tenant_id=scope.tenant_id)
        blockers = compute_publication_blockers(session, product) if product is not None else []
        return templates.TemplateResponse(
            request,
            "ad07_product_detail.html",
            {
                "product": product,
                "blockers": blockers,
                "all_service_modes": _ALL_SERVICE_MODES,
                "conflict": False,
                "error": str(exc),
            },
            status_code=400,
        )

    return RedirectResponse(url=f"/ops/products/{product_id}", status_code=303)


@router.post("/ops/products/{product_id}/request-review")
def request_release_review_route(
    product_id: str,
    request: Request,
    evidence_manifest_id: str = Form(...),
    audience_policy_id: str = Form(...),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """AD-07's own "Confirm: Submit version for review" -- creates a
    real AD-08 release review. Reuses `manage_product_draft`: proposing
    a review is a research/release decision, same trio of roles as
    drafting the product itself."""
    _require_product_admin(scope)
    set_tenant_scope(session, scope.tenant_id)
    product = get_product(session, product_id, tenant_id=scope.tenant_id)
    if product is None:
        raise HTTPException(status_code=404, detail="not found")

    try:
        review = request_release_review(
            session,
            tenant_id=scope.tenant_id,
            product=product,
            proposer_user_id=scope.user_id,
            evidence_manifest_id=evidence_manifest_id,
            audience_policy_id=audience_policy_id,
        )
        session.commit()
    except ReviewNotEligibleError as exc:
        session.rollback()
        set_tenant_scope(session, scope.tenant_id)
        product = get_product(session, product_id, tenant_id=scope.tenant_id)
        blockers = compute_publication_blockers(session, product) if product is not None else []
        return templates.TemplateResponse(
            request,
            "ad07_product_detail.html",
            {
                "product": product,
                "blockers": blockers,
                "all_service_modes": _ALL_SERVICE_MODES,
                "conflict": False,
                "error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse(url=f"/ops/reviews/{review.release_review_id}", status_code=303)


def _require_release_reviewer(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "release_strategy")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops/reviews")
def release_review_queue_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_release_reviewer(scope)
    set_tenant_scope(session, scope.tenant_id)
    reviews = list_release_reviews(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(request, "ad08_reviews.html", {"reviews": reviews})


@router.get("/ops/reviews/{release_review_id}")
def release_review_detail_page(
    release_review_id: str,
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_release_reviewer(scope)
    set_tenant_scope(session, scope.tenant_id)
    review = get_release_review(session, release_review_id, tenant_id=scope.tenant_id)
    if review is None:
        raise HTTPException(status_code=404, detail="not found")
    product = get_product(session, review.product_id, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(
        request, "ad08_review_detail.html", {"review": review, "product": product}
    )


@router.post("/ops/reviews/{release_review_id}")
def decide_release_review_route(
    release_review_id: str,
    request: Request,
    decision: str = Form(...),
    reason: str = Form(...),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_release_reviewer(scope)
    set_tenant_scope(session, scope.tenant_id)
    review = get_release_review(session, release_review_id, tenant_id=scope.tenant_id)
    if review is None:
        raise HTTPException(status_code=404, detail="not found")
    product = get_product(session, review.product_id, tenant_id=scope.tenant_id)
    if product is None:
        raise HTTPException(status_code=404, detail="not found")

    try:
        decide_release_review(
            session, review, product, reviewer_user_id=scope.user_id, decision=decision, reason=reason
        )
        session.commit()
    except (SelfReviewNotAllowedError, StaleReviewTargetError, ReviewNotEligibleError, InvalidReviewDecisionError) as exc:
        session.rollback()
        set_tenant_scope(session, scope.tenant_id)
        review = get_release_review(session, release_review_id, tenant_id=scope.tenant_id)
        product = get_product(session, review.product_id, tenant_id=scope.tenant_id) if review is not None else None
        return templates.TemplateResponse(
            request,
            "ad08_review_detail.html",
            {"review": review, "product": product, "error": str(exc)},
            status_code=400,
        )

    return RedirectResponse(url=f"/ops/reviews/{release_review_id}", status_code=303)


def _require_research_run_admin(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "run_research_job")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops/research/new")
def research_run_list_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_research_run_admin(scope)
    set_tenant_scope(session, scope.tenant_id)
    runs = list_research_runs(session, tenant_id=scope.tenant_id)
    sleeves = list_sleeves(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(request, "ad04_research_runs.html", {"runs": runs, "sleeves": sleeves})


@router.post("/ops/research/new")
def create_research_run_route(
    request: Request,
    sleeve_ids: list[str] = Form([]),
    recipes: list[str] = Form([]),
    subset_min: int = Form(2),
    subset_max: int = Form(5),
    cash_bps: int = Form(1500),
    max_sleeve_bps: int = Form(3500),
    max_cluster_bps: int = Form(5000),
    train_sessions: int = Form(252),
    test_sessions: int = Form(63),
    holdout_fraction: str = Form("0.20"),
    cost_scenario_ids: str = Form(""),
    resource_profile_id: str = Form(""),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_research_run_admin(scope)
    set_tenant_scope(session, scope.tenant_id)
    try:
        try:
            holdout_fraction_decimal = Decimal(holdout_fraction)
        except InvalidOperation as exc:
            raise InvalidResearchRunError(f"invalid holdout_fraction: {holdout_fraction!r}") from exc

        run = create_research_run(
            session,
            tenant_id=scope.tenant_id,
            sleeve_ids=sleeve_ids,
            recipes=recipes,
            subset_min=subset_min,
            subset_max=subset_max,
            cash_bps=cash_bps,
            max_sleeve_bps=max_sleeve_bps,
            max_cluster_bps=max_cluster_bps,
            train_sessions=train_sessions,
            test_sessions=test_sessions,
            holdout_fraction=holdout_fraction_decimal,
            cost_scenario_ids=[s.strip() for s in cost_scenario_ids.split(",") if s.strip()],
            resource_profile_id=resource_profile_id or None,
        )
        session.commit()
    except InvalidResearchRunError as exc:
        session.rollback()
        set_tenant_scope(session, scope.tenant_id)
        runs = list_research_runs(session, tenant_id=scope.tenant_id)
        sleeves = list_sleeves(session, tenant_id=scope.tenant_id)
        return templates.TemplateResponse(
            request,
            "ad04_research_runs.html",
            {"runs": runs, "sleeves": sleeves, "error": str(exc)},
            status_code=400,
        )
    return RedirectResponse(url=f"/ops/research/new/{run.research_run_id}", status_code=303)


@router.get("/ops/research/new/{research_run_id}")
def research_run_detail_page(
    research_run_id: str,
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_research_run_admin(scope)
    set_tenant_scope(session, scope.tenant_id)
    run = get_research_run(session, research_run_id, tenant_id=scope.tenant_id)
    if run is None:
        raise HTTPException(status_code=404, detail="not found")
    preview = compute_research_run_preview(session, run, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(request, "ad04_research_run_detail.html", {"run": run, "preview": preview})


@router.get("/ops/rights")
def rights_register_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """AD-02 "Rights and service approvals" -- read-only grant register
    (this route's own docstring above explains what's deliberately not
    built yet: create/attach-evidence/approve). `RightsGrant` carries no
    tenant_id, so there is no `set_tenant_scope` call here -- visibility
    is gated purely by role (owner/reviewer), not by tenant."""
    try:
        require_permission(scope.role, "view_rights_register")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    grants = list_rights_grants(session)
    return templates.TemplateResponse(request, "ad02_rights.html", {"grants": grants})


def _require_sleeve_admin(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_sleeve_draft")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops/research/universe")
def sleeve_catalog_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_sleeve_admin(scope)
    set_tenant_scope(session, scope.tenant_id)
    sleeves = list_sleeves(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(
        request,
        "ad03_sleeves.html",
        {"sleeves": sleeves, "tenant_id": scope.tenant_id, "role": scope.role.value},
    )


@router.post("/ops/research/universe")
def create_sleeve_route(
    request: Request,
    provider: str = Form(...),
    analyst: str = Form(...),
    strategy_horizon: str = Form(...),
    asset_class: str = Form(...),
    parser_version: str = Form(...),
    execution_policy_id: str = Form(...),
    cost_model_id: str = Form(...),
    capacity_policy_id: str = Form(...),
    risk_unit_id: str = Form(...),
    history_origin: str = Form(...),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_sleeve_admin(scope)
    set_tenant_scope(session, scope.tenant_id)
    try:
        create_sleeve(
            session,
            tenant_id=scope.tenant_id,
            provider=provider,
            analyst=analyst,
            strategy_horizon=strategy_horizon,
            asset_class=asset_class,
            parser_version=parser_version,
            execution_policy_id=execution_policy_id,
            cost_model_id=cost_model_id,
            capacity_policy_id=capacity_policy_id,
            risk_unit_id=risk_unit_id,
            history_origin=history_origin,
        )
        session.commit()
    except InvalidSleeveDraftError as exc:
        session.rollback()
        set_tenant_scope(session, scope.tenant_id)
        sleeves = list_sleeves(session, tenant_id=scope.tenant_id)
        return templates.TemplateResponse(
            request,
            "ad03_sleeves.html",
            {
                "sleeves": sleeves,
                "tenant_id": scope.tenant_id,
                "role": scope.role.value,
                "error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse(url="/ops/research/universe", status_code=303)


@router.get("/portfolios")
def public_portfolio_catalog(request: Request, session: Session = Depends(get_db_session)):
    """Anonymous, no tenant scope set -- exactly what a real unauthenticated
    visitor looks like. `list_published_products` (and the underlying
    `product_visibility` RLS policy) is what keeps this truthfully empty
    until a product is genuinely PUBLISHED, never an application-level
    "if logged in" check standing in for it."""
    products = list_published_products(session)
    return templates.TemplateResponse(request, "pu02_catalog.html", {"products": products})


@router.get("/")
def public_home_page(request: Request, session: Session = Depends(get_db_session)):
    """PU-01 "Public home" -- anonymous, no tenant scope. Shares
    PU-02's own `list_published_products` query rather than a separate
    "featured products" concept that doesn't exist. Service status is
    computed from real config state (app/services/public_site.py), never
    a decorative "all systems operational" banner."""
    products = list_published_products(session)
    service_status = get_service_status()
    return templates.TemplateResponse(request, "pu01_home.html", {"products": products, "service_status": service_status})


@router.get("/help")
def public_help_page(request: Request):
    """PU-08 "Help and compatibility guide" -- anonymous, no database
    query at all: the compatibility directory is grounded in the actual
    state of the publisher adapter modules (see
    app/services/public_site.py's own docstring), not live data."""
    channels = get_channel_compatibility()
    return templates.TemplateResponse(request, "pu08_help.html", {"channels": channels})


@router.get("/portfolios/{slug}")
def public_portfolio_detail_page(slug: str, request: Request, session: Session = Depends(get_db_session)):
    """PU-03 "Portfolio detail" -- anonymous, no tenant scope. A slug
    belonging to a DRAFT/VALIDATED/APPROVED (not yet PUBLISHED) product,
    or no product at all, is indistinguishable here -- both return a
    scoped 404, per PU-03's own "draft/retired restricted slugs return
    scoped not-found" (never a 403, which would confirm the slug
    exists). No NAV/marks-history model exists for a standard portfolio
    product in this build, so the performance/drawdown panel always
    honestly reports the track record as unavailable rather than a
    guessed or zero-filled curve."""
    detail = get_published_portfolio_detail(session, slug)
    if detail is None:
        raise HTTPException(status_code=404, detail="not found")
    channels = get_channel_compatibility()
    return templates.TemplateResponse(request, "pu03_portfolio_detail.html", {"detail": detail, "channels": channels})


@router.get("/pricing")
def public_pricing_page(request: Request):
    """PU-05 "Pricing and service compatibility" -- anonymous, no
    database query. `get_pricing_plans` returns nothing at all until
    billing is genuinely connected, per PU-05's own "Unapproved price
    drafts are absent from public responses" -- the fixture prices in
    app/models/billing.py are explicitly not user-approved, so an
    unconfigured deployment (this one) truthfully shows PU-05's own
    empty state instead of a plan-cards table."""
    plans = get_pricing_plans()
    service_status = get_service_status()
    return templates.TemplateResponse(request, "pu05_pricing.html", {"plans": plans, "service_status": service_status})


def _require_customer(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_own_eligibility")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/onboarding/eligibility")
def eligibility_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """ID-04 "Service eligibility onboarding" -- a customer's own real
    facts and a live-computed decision, never a stored verdict (see
    app/services/eligibility.py's own docstring)."""
    _require_customer(scope)
    set_tenant_scope(session, scope.tenant_id)
    assessment = get_eligibility_assessment(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
    decisions = evaluate_eligibility(session, assessment) if assessment is not None else []
    return templates.TemplateResponse(
        request, "id04_eligibility.html", {"assessment": assessment, "decisions": decisions, "error": None}
    )


@router.post("/onboarding/eligibility")
def save_eligibility_page(
    request: Request,
    residence_country: str = Form(...),
    tax_residence: list[str] = Form([]),
    customer_type: str = Form("individual"),
    requested_service_modes: list[str] = Form([]),
    document_versions: str = Form(""),
    facts_confirmed: str = Form(""),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_customer(scope)
    set_tenant_scope(session, scope.tenant_id)
    document_version_list = [v.strip() for v in document_versions.split(",") if v.strip()]
    try:
        save_eligibility_facts(
            session,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            residence_country=residence_country,
            tax_residence=tax_residence,
            customer_type=customer_type,
            requested_service_modes=requested_service_modes,
            document_versions=document_version_list,
            facts_confirmed=bool(facts_confirmed),
        )
    except InvalidEligibilityFactsError as exc:
        session.rollback()
        return templates.TemplateResponse(
            request,
            "id04_eligibility.html",
            {"assessment": get_eligibility_assessment(session, tenant_id=scope.tenant_id, user_id=scope.user_id), "decisions": [], "error": str(exc)},
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/onboarding/eligibility", status_code=303)


def _require_staff_access(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_staff_access")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops/access")
def staff_access_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
    error: str | None = None,
):
    """AD-16 "Staff roles and access reviews" -- owner-only. Session
    audit has no backing model (no session/audit-log store exists), so
    it's rendered as an explicit unsupported note, never a fabricated
    empty table."""
    _require_staff_access(scope)
    set_tenant_scope(session, scope.tenant_id)
    memberships = list_staff_memberships(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(
        request,
        "ad16_access.html",
        {"memberships": memberships, "all_grantable_roles": _GRANTABLE_ROLE_VALUES, "error": error},
    )


@router.post("/ops/access/invite")
def invite_staff_page(
    request: Request,
    user_id: str = Form(...),
    role: str = Form(...),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_staff_access(scope)
    set_tenant_scope(session, scope.tenant_id)
    try:
        role_enum = MembershipRole(role)
        invite_staff_member(session, tenant_id=scope.tenant_id, user_id=user_id, role=role_enum)
    except (ValueError, InvalidStaffGrantError) as exc:
        session.rollback()
        memberships = list_staff_memberships(session, tenant_id=scope.tenant_id)
        return templates.TemplateResponse(
            request,
            "ad16_access.html",
            {"memberships": memberships, "all_grantable_roles": _GRANTABLE_ROLE_VALUES, "error": str(exc)},
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/ops/access", status_code=303)


@router.post("/ops/access/{user_id}/revoke")
def revoke_staff_page(
    user_id: str,
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_staff_access(scope)
    set_tenant_scope(session, scope.tenant_id)
    try:
        revoke_staff_member(session, tenant_id=scope.tenant_id, user_id=user_id, acting_user_id=scope.user_id)
    except (MembershipNotFoundError, CannotRevokeOwnerError, CannotRevokeSelfError) as exc:
        session.rollback()
        memberships = list_staff_memberships(session, tenant_id=scope.tenant_id)
        return templates.TemplateResponse(
            request,
            "ad16_access.html",
            {"memberships": memberships, "all_grantable_roles": _GRANTABLE_ROLE_VALUES, "error": str(exc)},
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/ops/access", status_code=303)


_ALL_SUPPORT_CASE_CATEGORIES = ["billing", "delivery", "connection", "performance", "safety", "access"]


def _require_own_support_case(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_own_support_case")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/app/support")
def support_case_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
    error: str | None = None,
):
    """CU-14 "Support and incident case" -- a customer's own cases
    only. Attachment scan/status is NOT implemented -- there is no
    malware-scan/upload pipeline in this build, so attachment_ids are
    shown honestly as plain unverified references, never a fabricated
    'scanned clean' status."""
    _require_own_support_case(scope)
    set_tenant_scope(session, scope.tenant_id)
    cases = list_support_cases(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
    return templates.TemplateResponse(
        request,
        "cu14_support.html",
        {"cases": cases, "all_categories": _ALL_SUPPORT_CASE_CATEGORIES, "error": error},
    )


@router.post("/app/support")
def create_support_case_page(
    request: Request,
    category: str = Form(...),
    related_object_id: str = Form(""),
    subject: str = Form(...),
    description: str = Form(...),
    attachment_ids: str = Form(""),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_own_support_case(scope)
    set_tenant_scope(session, scope.tenant_id)
    attachment_id_list = [v.strip() for v in attachment_ids.split(",") if v.strip()]
    try:
        create_support_case(
            session,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            category=category,
            related_object_id=related_object_id or None,
            subject=subject,
            description=description,
            attachment_ids=attachment_id_list,
        )
    except InvalidSupportCaseError as exc:
        session.rollback()
        cases = list_support_cases(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
        return templates.TemplateResponse(
            request,
            "cu14_support.html",
            {"cases": cases, "all_categories": _ALL_SUPPORT_CASE_CATEGORIES, "error": str(exc)},
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/app/support", status_code=303)


_ALL_PLATFORMS = ["collective2", "etoro", "copyfactory", "broker_native"]
_ALL_PUBLISHER_ENVIRONMENTS = ["local_simulation", "external_test", "demo", "live"]
_ALL_PUBLICATION_MODES = ["api_strategy_publisher", "approved_master_copy"]


def _require_publisher_destinations(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_publisher_destinations")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops/publishers")
def publisher_destinations_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
    error: str | None = None,
):
    """AD-09 "Publisher channels and strategies" -- "Verify read-only
    identity" and "Prepare qualification" are NOT implemented: both need
    real external platform connectivity this build doesn't have."""
    _require_publisher_destinations(scope)
    set_tenant_scope(session, scope.tenant_id)
    destinations = list_publisher_destinations(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(
        request,
        "ad09_publishers.html",
        {
            "destinations": destinations,
            "all_platforms": _ALL_PLATFORMS,
            "all_environments": _ALL_PUBLISHER_ENVIRONMENTS,
            "all_publication_modes": _ALL_PUBLICATION_MODES,
            "error": error,
        },
    )


@router.post("/ops/publishers")
def create_publisher_destination_page(
    request: Request,
    platform: str = Form(...),
    external_strategy_id: str = Form(...),
    environment: str = Form("local_simulation"),
    credential_ref: str = Form(""),
    capability_manifest_id: str = Form(""),
    publication_mode: str = Form("api_strategy_publisher"),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_publisher_destinations(scope)
    set_tenant_scope(session, scope.tenant_id)
    try:
        create_publisher_destination(
            session,
            tenant_id=scope.tenant_id,
            platform=platform,
            external_strategy_id=external_strategy_id,
            environment=environment,
            credential_ref=credential_ref or None,
            capability_manifest_id=capability_manifest_id or None,
            publication_mode=publication_mode,
        )
    except InvalidPublisherDestinationError as exc:
        session.rollback()
        destinations = list_publisher_destinations(session, tenant_id=scope.tenant_id)
        return templates.TemplateResponse(
            request,
            "ad09_publishers.html",
            {
                "destinations": destinations,
                "all_platforms": _ALL_PLATFORMS,
                "all_environments": _ALL_PUBLISHER_ENVIRONMENTS,
                "all_publication_modes": _ALL_PUBLICATION_MODES,
                "error": str(exc),
            },
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/ops/publishers", status_code=303)


_ALL_API_KEY_SCOPES = ["alerts_read", "reports_read", "delivery_receive"]


def _require_own_api_keys(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_own_api_keys")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/app/developer")
def api_keys_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
    error: str | None = None,
    new_secret: str | None = None,
):
    """CU-16 "API delivery, keys and exports" -- a customer's own keys
    only. Webhook destinations/export jobs are NOT implemented -- there
    is no webhook-destination or export-job infrastructure in this
    build."""
    _require_own_api_keys(scope)
    set_tenant_scope(session, scope.tenant_id)
    keys = list_api_keys(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
    return templates.TemplateResponse(
        request,
        "cu16_developer.html",
        {"keys": keys, "all_scopes": _ALL_API_KEY_SCOPES, "error": error, "new_secret": new_secret},
    )


@router.post("/app/developer")
def create_api_key_page(
    request: Request,
    label: str = Form(...),
    scopes: list[str] = Form([]),
    expires_at: str = Form(...),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """Returns 200 (never a redirect) on success -- the freshly
    generated secret exists only in THIS response and must be shown
    here, per "Generated key shown once only"."""
    _require_own_api_keys(scope)
    set_tenant_scope(session, scope.tenant_id)
    try:
        expires_at_parsed = datetime.fromisoformat(expires_at)
        if expires_at_parsed.tzinfo is None:
            expires_at_parsed = expires_at_parsed.replace(tzinfo=timezone.utc)
        _, raw_secret = generate_scoped_key(
            session,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            label=label,
            scopes=scopes,
            expires_at=expires_at_parsed,
        )
    except (ValueError, InvalidApiKeyRequestError) as exc:
        session.rollback()
        keys = list_api_keys(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
        return templates.TemplateResponse(
            request,
            "cu16_developer.html",
            {"keys": keys, "all_scopes": _ALL_API_KEY_SCOPES, "error": str(exc), "new_secret": None},
            status_code=400,
        )
    session.commit()
    keys = list_api_keys(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
    return templates.TemplateResponse(
        request,
        "cu16_developer.html",
        {"keys": keys, "all_scopes": _ALL_API_KEY_SCOPES, "error": None, "new_secret": raw_secret},
    )


@router.post("/app/developer/{key_id}/revoke")
def revoke_api_key_page(
    key_id: str,
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_own_api_keys(scope)
    set_tenant_scope(session, scope.tenant_id)
    try:
        revoke_api_key(session, tenant_id=scope.tenant_id, user_id=scope.user_id, key_id=key_id)
    except ApiKeyNotFoundError as exc:
        session.rollback()
        keys = list_api_keys(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
        return templates.TemplateResponse(
            request,
            "cu16_developer.html",
            {"keys": keys, "all_scopes": _ALL_API_KEY_SCOPES, "error": str(exc), "new_secret": None},
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/app/developer", status_code=303)


@router.get("/ops/business")
def business_economics_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """AD-12 "Business economics and royalties" -- see this route
    module's own docstring above for what is and is not implemented."""
    try:
        require_permission(scope.role, "view_business_economics")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    set_tenant_scope(session, scope.tenant_id)
    economics = get_business_economics(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(request, "ad12_business.html", {"economics": economics})
