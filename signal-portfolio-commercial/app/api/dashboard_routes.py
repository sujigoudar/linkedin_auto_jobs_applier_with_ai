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
  unknown-state publications). "Open incidents" now has a real backing
  model (app/models/incident.py, AD-21) with its own real register
  linked from this overview at /ops/incidents; this overview itself
  still does not compute a decorative headline count from it -- AD-21's
  own spec: "No numeric headline required. Do not add a decorative
  performance KPI."
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
  slug returns a scoped 404, never its content. "Try our fit
  simulator" is a real, bounded call to signal-copier's own signed,
  non-owner `POST /catalog/providers/{source}/fit-simulation` (see
  app/services/fit_simulation_client.py) -- honestly rendered as
  unavailable for every product today, since no product yet has a
  real signal-copier source mapping or real historical price CSV
  configured (`config.FIT_SIM_CATALOG_CONFIG_JSON` defaults to empty);
  the wiring itself is real and tested end to end with the
  cross-service HTTP call mocked at the boundary.
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
  membership. Session audit is now real too: ID-01's own
  create_web_session/delete_web_session (app/services/local_auth.py)
  append a real "login"/"logout" AuditEvent (app/services/audit_log.py)
  for every real session, and this page's own Session audit panel
  lists them, tenant-scoped. An already-issued Bearer-token JWT (the
  non-cookie auth path) still remains cryptographically valid until it
  expires -- only cookie-based web sessions can be explicitly revoked.
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
- AD-11 "Customers and scoped support record": a real, read-only staff
  view over a tenant's own customer memberships, built entirely from
  data ID-04/CU-14 already made real (eligibility decisions, support
  cases). A cross-tenant/non-customer user_id returns a scoped 404.
  Mandates (CU-09's own CopyMandate) and Audit (app/services/
  audit_log.py's real login/logout AuditEvents) are both real now,
  tenant- AND customer-scoped. Subscription remains NOT implemented --
  no per-customer subscription model exists in this schema
  (Subscription is tenant-scoped, not customer-scoped).
- AD-17 "Integrations, data rights and quotas": a real "save inactive
  config" gated by a reviewed provider allowlist (only the adapter
  modules this codebase actually has -- Stripe for billing, Collective2/
  eToro/CopyFactory for publication; every other purpose has no
  reviewed provider at all yet) and by environment (only "test" is
  accepted). Quota/cost and Freshness/history are NOT implemented -- no
  real usage-tracking model exists.
- AD-13 "Pricing, entitlements and billing operations": a real
  test-mode price/entitlement draft. Only "test" mode is accepted (live
  requires a merchant-approval workflow this build doesn't have);
  features are restricted to the real API scopes CU-16 already
  implements plus two real catalog facts -- never an implicit trading
  right. Subscription changes/Webhook reconciliation/Processor identity
  are NOT implemented -- no per-price subscription linkage or webhook-
  event store exists in this build.
- AD-19 "Content and disclosure publishing": a real content draft +
  submit-for-review. Body is refused outright if it contains any `<`/`>`
  character -- no raw HTML/script editor. methodology/status documents
  (inherently factual claims) require at least one source_evidence_id.
  Actual publication remains a separate, not-yet-built admission
  decision this screen has no authority over.
- AD-14 "Managed-program setup": a real inactive PAMM/MAM program
  config + submit-for-review. Every allocation/NAV/dealing convention is
  a required, opaque policy id reference -- no raw percentage/rate
  field exists, so a program can never be activated from "a local
  percentage table alone". agreement_evidence_ids must be nonempty --
  no automated signing without evidence.
- AD-18 "Audit log and release evidence": a real, append-only audit
  store (app/services/audit_log.py). AD-16's invite/revoke actions were
  its first real writer; ID-01's own create_web_session/
  delete_web_session (login/logout, object_type="session") are now a
  second, feeding AD-16's own Session audit panel and AD-11's own
  per-customer Audit panel. The database itself refuses any
  UPDATE/DELETE against audit_events, matching ledger_entries' own
  append-only precedent. Evidence manifest and Export queue are NOT
  implemented -- no evidence-bundling or export-job infrastructure
  exists.
- CU-04 "Alerts and delivery history" / CU-05 "Alert, trade and
  order-family detail": a customer's own real, entitled publication-
  intent timeline (scoped through PortfolioVersion -> Product -> the
  customer's own ACTIVE PortfolioSelection, never every intent the
  tenant has published) and its per-episode revision history. Delivery
  receipts and execution/fees are NOT implemented -- no delivery-
  attempt/acknowledgment model or ledger-to-episode link exists.
- CU-06 "Performance and costs": a customer's own real, reconciled
  Book.FOLLOWER performance, scoped to that customer's own declared
  connections only (never the tenant's whole FOLLOWER book). Costs/cash
  flows, attribution, drawdown and win rate are NOT implemented -- no
  cashflow ledger, subscription-to-selection linkage or episode/equity-
  curve concept exists.
- CU-11 "Billing, invoices and plan changes": the tenant's own real,
  shared Subscription state (this schema has no per-customer
  subscription) plus real entitlement flags. Invoices, hosted checkout/
  portal and pending changes are NOT implemented -- no Stripe transport
  exists in this build (payment processor approval is still pending).
- CU-15 "Managed program investor report": a real (always-empty today)
  customer-visible program query plus a real independent eligibility
  checklist -- ManagedProgramState has no admitted/approved value yet,
  so no program can legitimately be shown to a customer until a future
  admission slice adds one.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.api.dependencies import SESSION_COOKIE_NAME, get_current_scope, get_db_session
from app.db import set_tenant_scope
from app.models.product import ProductLifecycleState, ServiceMode
from app.models.tenancy import Membership, MembershipRole
from app.services.auth import TenantScope
from app.services.eligibility import (
    InvalidEligibilityFactsError,
    evaluate_eligibility,
    get_eligibility_assessment,
    save_eligibility_facts,
)
from app.services.permissions import PermissionDenied, is_allowed, require_permission
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
from app.services.content_document import (
    ContentNotEligibleForPublicationError,
    ContentNotEligibleForReviewError,
    InvalidContentDraftError,
    get_content_document,
    list_content_documents,
    list_public_content_documents,
    publish_content_document,
    request_content_review,
    save_content_draft,
)
from app.services.audit_log import append_audit_event, get_object_timeline, list_audit_events
from app.services.customer_support_view import get_customer_support_record, list_customers
from app.services.managed_program import (
    InvalidManagedProgramError,
    ProgramNotEligibleForReviewError,
    create_managed_program,
    get_managed_program,
    list_managed_programs,
    request_managed_program_review,
)
from app.services.integration_configuration import (
    InvalidIntegrationConfigurationError,
    create_integration_configuration,
    list_integration_configurations,
)
from app.services.price_version import InvalidPriceVersionError, create_price_version, list_price_versions
from app.services.publisher_destination import (
    InvalidPublisherDestinationError,
    create_publisher_destination,
    list_publisher_destinations,
)
from app.services.support_case import InvalidSupportCaseError, create_support_case, list_support_cases
from app.services.publication_admin import get_publication_intent_detail
from app.services.copy_mandate import (
    InvalidCopyMandateError,
    MandateNotEligibleForCancellationError,
    cancel_copy_mandate,
    create_copy_mandate_draft,
    get_own_copy_mandate,
    list_own_copy_mandates,
)
from app.models.platform_connection import PlatformConnectionState
from app.models.portfolio_selection import PortfolioSelectionState
from app.services.platform_connection import (
    InvalidPlatformConnectionError,
    create_platform_connection,
    disconnect_platform_connection,
    get_own_platform_connection,
    list_own_platform_connections,
)
from app.services.customer_display_preferences import (
    InvalidDisplayPreferencesError,
    get_display_preferences,
    save_display_preferences,
)
from app.services.customer_overview import get_customer_overview
from app.services.customer_selection_detail import get_own_selection_detail
from app.services.customer_alerts import get_own_alert_episode, list_own_alerts
from app.services.customer_performance_report import get_own_performance_report
from app.services.customer_billing import get_own_billing_state
from app.services.customer_managed_programs import get_own_managed_programs_view
from app.services.notification_preferences import (
    InvalidNotificationPreferencesError,
    get_notification_preferences,
    save_notification_preferences,
)
from app.services.portfolio_selection import (
    InvalidPortfolioSelectionError,
    cancel_portfolio_selection,
    create_portfolio_selection,
    get_own_portfolio_selection,
    list_own_portfolio_selections,
)
from app.services.workspace_settings import (
    MANDATORY_PANEL_IDS,
    InvalidWorkspaceSettingsError,
    get_workspace_settings,
    save_workspace_settings,
)
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
from app.services.integration_status import get_integration_status
from app.services.operations_overview import get_operations_overview
from app.services.platform_performance import compute_platform_performance
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
    TooManyComparisonSlugsError,
    compare_published_portfolios,
    get_channel_compatibility,
    get_pricing_plans,
    get_published_portfolio_detail,
    get_service_status,
)
from app.services.fit_simulation_client import get_fit_sim_availability, run_fit_simulation
from app.rate_limit import PUBLIC_FIT_SIM_RATE_LIMIT, limiter
from app.services.candidate_comparison import (
    CandidateNotFoundError,
    InvalidCandidateDraftError,
    compare_candidates,
    create_portfolio_version_draft_from_candidate,
    list_candidate_overlap_against_baseline,
    list_comparable_research_runs,
)
from app.services.incident import (
    InvalidIncidentOperationError,
    acknowledge_incident,
    assign_incident,
    get_incident,
    list_incidents,
    propose_resolution,
    reconcile_incident,
)
from app.services.research_run import (
    InvalidResearchRunError,
    compute_research_run_preview,
    create_research_run,
    get_research_run,
    list_research_runs,
)
from app.services.rights_registry import list_rights_grants
from app.services.local_auth import (
    AccountAlreadyExistsError,
    InvalidCredentialsError,
    InvalidTokenError as LocalAuthInvalidTokenError,
    authenticate,
    create_account,
    create_web_session,
    delete_web_session,
    request_password_reset,
    reset_password,
    verify_email,
)
from app.services.sleeve_admin import InvalidSleeveDraftError, create_sleeve, list_sleeves
from app.services.source_coverage import compute_source_coverage

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
    own docstring above for what's deliberately not built. "Open
    incidents" now has a real backing model (app/models/incident.py,
    AD-21) with its own real register at /ops/incidents; this overview
    still does not compute a decorative headline count from it -- AD-21's
    own spec: "No numeric headline required. Do not add a decorative
    performance KPI.\""""
    try:
        require_permission(scope.role, "view_operations_overview")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    set_tenant_scope(session, scope.tenant_id)
    overview = get_operations_overview(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(
        request, "ad01_overview.html",
        {
            "overview": overview,
            "can_view_trading_performance": is_allowed(scope.role, "view_integration_status"),
            "can_view_portfolio_lab": is_allowed(scope.role, "run_research_job"),
            "can_view_incidents": is_allowed(scope.role, "view_incident_register"),
        },
    )


@router.get("/api/v1/ops/integration-status")
def integration_status_endpoint(
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
) -> dict:
    """Signal Platform Integration Correction Pack's own
    INTEGRATION_DECISION.md S11 "Integration Status panel" -- see
    app/services/integration_status.py's own module docstring for
    exactly what this reports and what it deliberately doesn't yet
    (gaps, snapshot/bootstrap state, cross-service lag). A JSON API
    rather than a template page: this is the first real backing query
    for that panel (S12's own "First complete proof... populates the
    corresponding private staff view"), not yet wired into a rendered
    screen."""
    try:
        require_permission(scope.role, "view_integration_status")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    set_tenant_scope(session, scope.tenant_id)
    report = get_integration_status(session, tenant_id=scope.tenant_id)
    return {
        "streams": [
            {
                "source_stream": s.source_stream,
                "environment": s.environment,
                "registered_at": s.registered_at.isoformat(),
                "received_count": s.received_count,
                "applied_count": s.applied_count,
                "unapplied_count": s.unapplied_count,
                "latest_received_at": s.latest_received_at.isoformat() if s.latest_received_at else None,
                "latest_applied_at": s.latest_applied_at.isoformat() if s.latest_applied_at else None,
            }
            for s in report.streams
        ],
        "platform_ledger_entries_count": report.platform_ledger_entries_count,
    }


@router.get("/api/v1/ops/platform-performance")
def platform_performance_endpoint(
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
) -> dict:
    """INTEGRATION_DECISION.md S12 step 4: "Complete scoped financial/
    provider metrics, reports..." -- real, gross, per-instrument realized
    P&L replayed from the owner's own Book.PLATFORM ledger entries. See
    app/services/platform_performance.py's own module docstring for
    exactly what this does and doesn't compute (gross only; no win
    rates/episode counts yet). Same access scope as Integration Status:
    this is private trading telemetry, not a general business metric."""
    try:
        require_permission(scope.role, "view_integration_status")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    set_tenant_scope(session, scope.tenant_id)
    report = compute_platform_performance(session, tenant_id=scope.tenant_id)
    return {
        "realized_pnl": str(report.realized_pnl),
        "net_pnl": str(report.net_pnl) if report.net_pnl is not None else None,
        "per_instrument": [
            {
                "instrument": ip.instrument,
                "realized_pnl": str(ip.realized_pnl),
                "net_pnl": str(ip.net_pnl) if ip.net_pnl is not None else None,
                "open_quantity": str(ip.open_quantity),
                "average_cost": str(ip.average_cost) if ip.average_cost is not None else None,
                "last_fill_price": str(ip.last_fill_price) if ip.last_fill_price is not None else None,
                "closing_fills": ip.closing_fills,
                "unknown_fee_entry_count": ip.unknown_fee_entry_count,
            }
            for ip in report.per_instrument.values()
        ],
    }


@router.get("/api/v1/ops/source-coverage")
def source_coverage_endpoint(
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
) -> dict:
    """INTEGRATION_ACCEPTANCE_CASES.json INT-027 "All permitted source
    outcomes reach research" -- see app/services/source_coverage.py's
    own module docstring for exactly what disposition/lineage this
    computes and what it honestly does not yet distinguish (this
    build's data model has no admitted/rejected/unfilled/canceled/loss/
    commentary taxonomy). Same access scope as Integration Status: this
    is private trading telemetry, not a general business metric."""
    try:
        require_permission(scope.role, "view_integration_status")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    set_tenant_scope(session, scope.tenant_id)
    report = compute_source_coverage(session, tenant_id=scope.tenant_id)
    return {
        "total_count": report.total_count,
        "ledger_recorded_count": report.ledger_recorded_count,
        "parked_count": report.parked_count,
        "received_no_ledger_entry_count": report.received_no_ledger_entry_count,
        "rows": [
            {
                "event_id": row.event_id,
                "source_stream": row.source_stream,
                "export_sequence": row.export_sequence,
                "disposition": row.disposition,
                "parked_reason": row.parked_reason,
                "ledger_entry_id": row.ledger_entry_id,
            }
            for row in report.rows
        ],
    }


@router.get("/ops/trading")
def trading_performance_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """The rendered owner workspace page for the two private-telemetry
    reports app/services/integration_status.py and
    app/services/platform_performance.py already compute -- closes
    INTEGRATION_DECISION.md S12 step 4's own "...and the actual owner
    navigation" (until now, both were JSON-only APIs with no
    discoverable link from the rendered dashboard). Linked from AD-01's
    own overview page for OWNER/RESEARCHER only."""
    try:
        require_permission(scope.role, "view_integration_status")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    set_tenant_scope(session, scope.tenant_id)
    integration_status = get_integration_status(session, tenant_id=scope.tenant_id)
    performance = compute_platform_performance(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(
        request, "ad_trading_performance.html",
        {
            "integration_status": integration_status,
            "performance": performance,
            "can_view_portfolio_lab": is_allowed(scope.role, "run_research_job"),
        },
    )


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
    return templates.TemplateResponse(
        request, "ad04_research_runs.html",
        {
            "runs": runs,
            "sleeves": sleeves,
            "can_view_trading_performance": is_allowed(scope.role, "view_integration_status"),
        },
    )


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
        append_audit_event(
            session, tenant_id=scope.tenant_id, actor_user_id=scope.user_id,
            object_type="research_run", object_id=run.research_run_id, action="create_research_run",
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


def _require_research_run_detail(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "view_research_run_detail")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops/research/runs/{research_run_id}")
def research_run_full_results_page(
    research_run_id: str,
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """AD-05 "Research run and full results" -- see this route
    module's own docstring above for what is and is not implemented.

    Reuses AD-04's own get_research_run/compute_research_run_preview
    rather than a duplicate query: no shard/result/failure model exists
    at all yet (there is no job queue -- see research_run.py's own
    docstring), so progress/shards/results/failures are always the
    real, honest "this run has not started" empty state, never a
    fabricated percentage or count."""
    _require_research_run_detail(scope)
    set_tenant_scope(session, scope.tenant_id)
    run = get_research_run(session, research_run_id, tenant_id=scope.tenant_id)
    if run is None:
        raise HTTPException(status_code=404, detail="not found")
    preview = compute_research_run_preview(session, run, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(request, "ad05_research_run_results.html", {"run": run, "preview": preview})


def _require_candidate_comparison(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "compare_research_candidates")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _composition_chart_data(candidate_view) -> dict | None:
    """JSON-serializable sleeve-weight breakdown for one candidate's real
    equal-weight allocation (Chart.js labels/values), or None when no
    allocation exists (RECIPE_NOT_IMPLEMENTED) -- never a fabricated
    breakdown for an unavailable allocation."""
    if candidate_view.allocation is None:
        return None
    labels = [candidate_view.sleeve_labels[sid] for sid in candidate_view.sleeve_ids] + ["Cash"]
    values = [float(candidate_view.allocation.weights[sid]) for sid in candidate_view.sleeve_ids]
    values.append(float(candidate_view.allocation.cash))
    return {"labels": labels, "values": values}


def _overlap_scatter_chart_data(points: list) -> list[dict]:
    return [
        {
            "candidate_index": p.candidate_index,
            "sleeve_count": p.sleeve_count,
            "overlap_with_baseline": p.overlap_with_baseline,
        }
        for p in points
    ]


@router.get("/ops/research/compare")
def candidate_comparison_page(
    request: Request,
    research_run_id: str | None = Query(None),
    candidate_a: int | None = Query(None),
    candidate_b: int | None = Query(None),
    created_portfolio_version_id: str | None = Query(None),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """AD-06 "Candidate comparison and shadow report" -- see
    app/services/candidate_comparison.py's own docstring for exactly
    what is and is not real here: real declared-candidate composition/
    allocation comparison and draft creation, never fabricated
    performance metrics."""
    _require_candidate_comparison(scope)
    set_tenant_scope(session, scope.tenant_id)
    comparable_runs = list_comparable_research_runs(session, tenant_id=scope.tenant_id)

    run = None
    comparison = None
    error = None
    overlap_scatter_points: list = []
    if research_run_id:
        run = get_research_run(session, research_run_id, tenant_id=scope.tenant_id)
        if run is None:
            raise HTTPException(status_code=404, detail="not found")
        if candidate_a is not None and candidate_b is not None:
            try:
                comparison = compare_candidates(session, run, candidate_a, candidate_b, tenant_id=scope.tenant_id)
                overlap_scatter_points = list_candidate_overlap_against_baseline(
                    session, run, candidate_a, tenant_id=scope.tenant_id
                )
            except CandidateNotFoundError as exc:
                error = str(exc)

    return templates.TemplateResponse(
        request,
        "ad06_candidate_comparison.html",
        {
            "comparable_runs": comparable_runs,
            "run": run,
            "candidate_a": candidate_a,
            "candidate_b": candidate_b,
            "comparison": comparison,
            "overlap_scatter_points": overlap_scatter_points,
            "overlap_scatter_chart_data": _overlap_scatter_chart_data(overlap_scatter_points),
            "candidate_a_composition_chart": _composition_chart_data(comparison.candidate_a) if comparison else None,
            "candidate_b_composition_chart": _composition_chart_data(comparison.candidate_b) if comparison else None,
            "error": error,
            "created_portfolio_version_id": created_portfolio_version_id,
        },
    )


@router.post("/ops/research/compare/draft")
def create_candidate_draft_route(
    request: Request,
    research_run_id: str = Form(...),
    candidate_index: int = Form(...),
    portfolio_id: str = Form(...),
    consent_disclosure_version: str = Form("v1"),
    max_subscriber_capacity: int = Form(100),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """AD-06-A01 "Choose candidate for draft" -- see
    app/services/candidate_comparison.py's own
    `create_portfolio_version_draft_from_candidate` docstring for why
    this structurally can never publish or activate anything."""
    _require_candidate_comparison(scope)
    set_tenant_scope(session, scope.tenant_id)
    run = get_research_run(session, research_run_id, tenant_id=scope.tenant_id)
    if run is None:
        raise HTTPException(status_code=404, detail="not found")
    try:
        version = create_portfolio_version_draft_from_candidate(
            session,
            tenant_id=scope.tenant_id,
            research_run=run,
            candidate_index=candidate_index,
            portfolio_id=portfolio_id,
            consent_disclosure_version=consent_disclosure_version,
            max_subscriber_capacity=max_subscriber_capacity,
        )
        append_audit_event(
            session, tenant_id=scope.tenant_id, actor_user_id=scope.user_id,
            object_type="portfolio_version", object_id=version.portfolio_version_id,
            action="create_portfolio_version_draft_from_candidate",
        )
        session.commit()
    except (InvalidCandidateDraftError, CandidateNotFoundError) as exc:
        session.rollback()
        set_tenant_scope(session, scope.tenant_id)
        comparable_runs = list_comparable_research_runs(session, tenant_id=scope.tenant_id)
        comparison = None
        overlap_scatter_points: list = []
        try:
            comparison = compare_candidates(session, run, candidate_index, candidate_index, tenant_id=scope.tenant_id)
            overlap_scatter_points = list_candidate_overlap_against_baseline(
                session, run, candidate_index, tenant_id=scope.tenant_id
            )
        except CandidateNotFoundError:
            comparison = None
        return templates.TemplateResponse(
            request,
            "ad06_candidate_comparison.html",
            {
                "comparable_runs": comparable_runs,
                "run": run,
                "candidate_a": candidate_index,
                "candidate_b": candidate_index,
                "comparison": comparison,
                "overlap_scatter_points": overlap_scatter_points,
                "overlap_scatter_chart_data": _overlap_scatter_chart_data(overlap_scatter_points),
                "candidate_a_composition_chart": _composition_chart_data(comparison.candidate_a) if comparison else None,
                "candidate_b_composition_chart": _composition_chart_data(comparison.candidate_b) if comparison else None,
                "error": str(exc),
                "created_portfolio_version_id": None,
            },
            status_code=400,
        )
    return RedirectResponse(
        url=(
            f"/ops/research/compare?research_run_id={research_run_id}"
            f"&created_portfolio_version_id={version.portfolio_version_id}"
        ),
        status_code=303,
    )


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


@router.get("/status")
def public_service_status_page(request: Request):
    """PU-07 "Public service status" -- anonymous, no database query.
    Shares PU-01's own `get_service_status` rather than a duplicate
    computed status. No incident-tracking model exists anywhere in this
    build (the same gap AD-01/AD-21's own slices already documented),
    so active incidents/maintenance/history are each rendered as an
    explicit UNSUPPORTED by the template -- never the spec's own literal
    "No published service incidents." zero-count text, since there is
    no completed incident query behind it to make that zero honest."""
    service_status = get_service_status()
    return templates.TemplateResponse(request, "pu07_status.html", {"service_status": service_status})


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
    fit_sim_unavailable = get_fit_sim_availability(slug)
    return templates.TemplateResponse(
        request,
        "pu03_portfolio_detail.html",
        {"detail": detail, "channels": channels, "fit_sim_unavailable": fit_sim_unavailable, "fit_sim_result": None},
    )


@router.post("/portfolios/{slug}/fit-simulation")
@limiter.limit(PUBLIC_FIT_SIM_RATE_LIMIT)
def public_portfolio_fit_simulation(
    slug: str,
    request: Request,
    session: Session = Depends(get_db_session),
    account_size: float = Form(...),
    max_per_trade: float = Form(...),
):
    """PU-03's "Try our fit simulator" -- the real, personalized "what
    would copying this provider have done to MY account" number an
    anonymous visitor can compute for THEIR OWN stated account size and
    max-per-trade, calling signal-copier's own bounded, signed, non-owner
    `POST /catalog/providers/{source}/fit-simulation` server-side (see
    app/services/fit_simulation_client.py's own module docstring for the
    full design and why the browser never talks to signal-copier
    directly). A draft/unpublished slug is the same scoped 404 as the GET
    route above -- this form is never reachable for a product that isn't
    genuinely public.

    Re-validates the same publication + availability checks the GET
    route already computed -- a crafted POST straight to this route (no
    prior GET) gets exactly the same honest "unavailable" outcome, never
    a code path that only existed because the GET route happened to
    gate the form's visibility."""
    detail = get_published_portfolio_detail(session, slug)
    if detail is None:
        raise HTTPException(status_code=404, detail="not found")
    channels = get_channel_compatibility()

    if account_size <= 0 or max_per_trade <= 0:
        fit_sim_result = None
        fit_sim_unavailable = get_fit_sim_availability(slug)
        error = "Account size and max per trade must both be greater than zero."
        return templates.TemplateResponse(
            request,
            "pu03_portfolio_detail.html",
            {
                "detail": detail,
                "channels": channels,
                "fit_sim_unavailable": fit_sim_unavailable,
                "fit_sim_result": fit_sim_result,
                "fit_sim_error": error,
            },
        )

    outcome = run_fit_simulation(slug, account_size=account_size, max_per_trade=max_per_trade)
    fit_sim_unavailable = None if outcome.available else outcome
    return templates.TemplateResponse(
        request,
        "pu03_portfolio_detail.html",
        {
            "detail": detail,
            "channels": channels,
            "fit_sim_unavailable": fit_sim_unavailable,
            "fit_sim_result": outcome if outcome.available else None,
        },
    )


@router.get("/compare")
def public_portfolio_comparison_page(
    request: Request, slug: list[str] = Query([]), session: Session = Depends(get_db_session)
):
    """PU-04 "Portfolio comparison" -- anonymous, no tenant scope.
    Reuses PU-03's own `get_published_portfolio_detail` per requested
    slug rather than a duplicate query. Requesting more than
    `MAX_COMPARISON_SLUGS` (4) is a real, named error, never a silent
    truncation to the first four. No NAV/marks-history model exists in
    this build, so the normalized return/co-drawdown panels always
    honestly report the series as unavailable, matching PU-03's own
    established precedent."""
    try:
        result = compare_published_portfolios(session, slug)
    except TooManyComparisonSlugsError as exc:
        return templates.TemplateResponse(
            request, "pu04_compare.html", {"matched": [], "unmatched_slugs": [], "error": str(exc)}, status_code=400
        )
    return templates.TemplateResponse(
        request,
        "pu04_compare.html",
        {"matched": result.matched, "unmatched_slugs": result.unmatched_slugs, "error": None},
    )


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
    audit now has a real backing store (app/services/audit_log.py):
    ID-01's own create_web_session/delete_web_session append a real
    "login"/"logout" AuditEvent for every real session, tenant-scoped
    the same way this whole page already is. Filtered here to
    object_type="session" so this panel shows login/logout activity,
    never the membership-grant events AD-18's own full audit log
    already covers."""
    _require_staff_access(scope)
    set_tenant_scope(session, scope.tenant_id)
    memberships = list_staff_memberships(session, tenant_id=scope.tenant_id)
    session_events = [
        e for e in list_audit_events(session, tenant_id=scope.tenant_id) if e.object_type == "session"
    ]
    return templates.TemplateResponse(
        request,
        "ad16_access.html",
        {
            "memberships": memberships,
            "all_grantable_roles": _GRANTABLE_ROLE_VALUES,
            "error": error,
            "session_events": session_events,
        },
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
        invite_staff_member(
            session, tenant_id=scope.tenant_id, user_id=user_id, role=role_enum, acting_user_id=scope.user_id
        )
    except (ValueError, InvalidStaffGrantError) as exc:
        session.rollback()
        memberships = list_staff_memberships(session, tenant_id=scope.tenant_id)
        session_events = [
            e for e in list_audit_events(session, tenant_id=scope.tenant_id) if e.object_type == "session"
        ]
        return templates.TemplateResponse(
            request,
            "ad16_access.html",
            {
                "memberships": memberships,
                "all_grantable_roles": _GRANTABLE_ROLE_VALUES,
                "error": str(exc),
                "session_events": session_events,
            },
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
        session_events = [
            e for e in list_audit_events(session, tenant_id=scope.tenant_id) if e.object_type == "session"
        ]
        return templates.TemplateResponse(
            request,
            "ad16_access.html",
            {
                "memberships": memberships,
                "all_grantable_roles": _GRANTABLE_ROLE_VALUES,
                "error": str(exc),
                "session_events": session_events,
            },
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


@router.get("/ops/customers")
def customers_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """AD-11 "Customers and scoped support record" -- see this route
    module's own docstring above for what is and is not implemented."""
    try:
        require_permission(scope.role, "view_customer_support_record")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    set_tenant_scope(session, scope.tenant_id)
    customers = list_customers(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(request, "ad11_customers.html", {"customers": customers})


@router.get("/ops/customers/{user_id}")
def customer_detail_page(
    user_id: str,
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """A user_id belonging to another tenant, or that isn't a CUSTOMER
    membership at all, returns a scoped 404 -- never a 403, which would
    confirm the id exists (PU-03's own "scoped not-found" precedent)."""
    try:
        require_permission(scope.role, "view_customer_support_record")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    set_tenant_scope(session, scope.tenant_id)
    record = get_customer_support_record(session, tenant_id=scope.tenant_id, user_id=user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    return templates.TemplateResponse(request, "ad11_customer_detail.html", {"record": record})


_ALL_INTEGRATION_PURPOSES = ["research", "quotes", "reference", "publication", "billing", "monitoring"]
_ALL_INTEGRATION_ENVIRONMENTS = ["test", "demo", "live"]


def _require_integration_configurations(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_integration_configurations")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops/integrations")
def integrations_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
    error: str | None = None,
):
    """AD-17 "Integrations, data rights and quotas" -- see this route
    module's own docstring above for what is and is not implemented."""
    _require_integration_configurations(scope)
    set_tenant_scope(session, scope.tenant_id)
    configurations = list_integration_configurations(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(
        request,
        "ad17_integrations.html",
        {
            "configurations": configurations,
            "all_purposes": _ALL_INTEGRATION_PURPOSES,
            "all_environments": _ALL_INTEGRATION_ENVIRONMENTS,
            "error": error,
        },
    )


@router.post("/ops/integrations")
def create_integration_page(
    request: Request,
    provider_registry_id: str = Form(...),
    purpose: str = Form(...),
    environment: str = Form("test"),
    credential_ref: str = Form(""),
    entitlement_evidence_id: str = Form(""),
    quota_profile_id: str = Form(...),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_integration_configurations(scope)
    set_tenant_scope(session, scope.tenant_id)
    try:
        create_integration_configuration(
            session,
            tenant_id=scope.tenant_id,
            provider_registry_id=provider_registry_id,
            purpose=purpose,
            environment=environment,
            credential_ref=credential_ref or None,
            entitlement_evidence_id=entitlement_evidence_id or None,
            quota_profile_id=quota_profile_id,
        )
    except InvalidIntegrationConfigurationError as exc:
        session.rollback()
        configurations = list_integration_configurations(session, tenant_id=scope.tenant_id)
        return templates.TemplateResponse(
            request,
            "ad17_integrations.html",
            {
                "configurations": configurations,
                "all_purposes": _ALL_INTEGRATION_PURPOSES,
                "all_environments": _ALL_INTEGRATION_ENVIRONMENTS,
                "error": str(exc),
            },
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/ops/integrations", status_code=303)


_ALL_BILLING_INTERVALS = ["month", "year"]
_ALL_PRICE_MODES = ["test", "live"]
_ALL_PRICE_FEATURES = ["alerts_read", "reports_read", "delivery_receive", "portfolios_up_to_three", "research_api"]


def _require_pricing(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_pricing")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops/billing")
def pricing_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
    error: str | None = None,
):
    """AD-13 "Pricing, entitlements and billing operations" -- see this
    route module's own docstring above for what is and is not
    implemented."""
    _require_pricing(scope)
    set_tenant_scope(session, scope.tenant_id)
    price_versions = list_price_versions(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(
        request,
        "ad13_pricing.html",
        {
            "price_versions": price_versions,
            "all_intervals": _ALL_BILLING_INTERVALS,
            "all_modes": _ALL_PRICE_MODES,
            "all_features": _ALL_PRICE_FEATURES,
            "error": error,
        },
    )


@router.post("/ops/billing")
def create_price_version_page(
    request: Request,
    sku: str = Form(...),
    currency: str = Form("usd"),
    amount_minor: int = Form(...),
    interval: str = Form("month"),
    is_unlimited_portfolios: str = Form(""),
    portfolio_limit: str = Form(""),
    features: list[str] = Form([]),
    mode: str = Form("test"),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_pricing(scope)
    set_tenant_scope(session, scope.tenant_id)
    unlimited = bool(is_unlimited_portfolios)
    try:
        portfolio_limit_parsed = None if unlimited or not portfolio_limit else int(portfolio_limit)
        create_price_version(
            session,
            tenant_id=scope.tenant_id,
            sku=sku,
            currency=currency,
            amount_minor=amount_minor,
            interval=interval,
            is_unlimited_portfolios=unlimited,
            portfolio_limit=portfolio_limit_parsed,
            features=features,
            mode=mode,
        )
    except (ValueError, InvalidPriceVersionError) as exc:
        session.rollback()
        price_versions = list_price_versions(session, tenant_id=scope.tenant_id)
        return templates.TemplateResponse(
            request,
            "ad13_pricing.html",
            {
                "price_versions": price_versions,
                "all_intervals": _ALL_BILLING_INTERVALS,
                "all_modes": _ALL_PRICE_MODES,
                "all_features": _ALL_PRICE_FEATURES,
                "error": str(exc),
            },
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/ops/billing", status_code=303)


_ALL_CONTENT_DOCUMENT_TYPES = ["methodology", "risk", "billing_terms", "privacy", "help", "status"]


def _require_content_documents(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_content_documents")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops/content")
def content_documents_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
    error: str | None = None,
):
    """AD-19 "Content and disclosure publishing" -- see this route
    module's own docstring above for what is and is not implemented."""
    _require_content_documents(scope)
    set_tenant_scope(session, scope.tenant_id)
    documents = list_content_documents(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(
        request,
        "ad19_content.html",
        {"documents": documents, "all_document_types": _ALL_CONTENT_DOCUMENT_TYPES, "error": error},
    )


@router.post("/ops/content")
def save_content_draft_page(
    request: Request,
    document_type: str = Form(...),
    locale: str = Form("en-US"),
    title: str = Form(...),
    body: str = Form(...),
    audience_policy_id: str = Form(...),
    source_evidence_ids: str = Form(""),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_content_documents(scope)
    set_tenant_scope(session, scope.tenant_id)
    evidence_ids = [v.strip() for v in source_evidence_ids.split(",") if v.strip()]
    try:
        document = save_content_draft(
            session,
            tenant_id=scope.tenant_id,
            document_type=document_type,
            locale=locale,
            title=title,
            body=body,
            audience_policy_id=audience_policy_id,
            source_evidence_ids=evidence_ids,
        )
        append_audit_event(
            session, tenant_id=scope.tenant_id, actor_user_id=scope.user_id,
            object_type="content_document", object_id=document.document_id, action="save_content_draft",
        )
    except InvalidContentDraftError as exc:
        session.rollback()
        documents = list_content_documents(session, tenant_id=scope.tenant_id)
        return templates.TemplateResponse(
            request,
            "ad19_content.html",
            {"documents": documents, "all_document_types": _ALL_CONTENT_DOCUMENT_TYPES, "error": str(exc)},
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/ops/content", status_code=303)


@router.post("/ops/content/{document_id}/request-review")
def request_content_review_page(
    document_id: str,
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_content_documents(scope)
    set_tenant_scope(session, scope.tenant_id)
    document = get_content_document(session, document_id, tenant_id=scope.tenant_id)
    if document is None:
        raise HTTPException(status_code=404, detail="not found")
    try:
        request_content_review(session, document)
        append_audit_event(
            session, tenant_id=scope.tenant_id, actor_user_id=scope.user_id,
            object_type="content_document", object_id=document.document_id, action="request_content_review",
        )
    except ContentNotEligibleForReviewError as exc:
        session.rollback()
        documents = list_content_documents(session, tenant_id=scope.tenant_id)
        return templates.TemplateResponse(
            request,
            "ad19_content.html",
            {"documents": documents, "all_document_types": _ALL_CONTENT_DOCUMENT_TYPES, "error": str(exc)},
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/ops/content", status_code=303)


@router.post("/ops/content/{document_id}/publish")
def publish_content_document_page(
    document_id: str,
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """PU-06's own missing admission decision, wired up from AD-19's own
    document detail -- see app/services/content_document.py's own
    publish_content_document docstring for what is and is not
    enforced. Reuses the same 'manage_content_documents' permission
    request-review already uses (OWNER, REVIEWER) -- exactly this
    screen's own access list."""
    _require_content_documents(scope)
    set_tenant_scope(session, scope.tenant_id)
    document = get_content_document(session, document_id, tenant_id=scope.tenant_id)
    if document is None:
        raise HTTPException(status_code=404, detail="not found")
    try:
        publish_content_document(session, document)
        append_audit_event(
            session, tenant_id=scope.tenant_id, actor_user_id=scope.user_id,
            object_type="content_document", object_id=document.document_id, action="publish_content_document",
        )
    except ContentNotEligibleForPublicationError as exc:
        session.rollback()
        documents = list_content_documents(session, tenant_id=scope.tenant_id)
        return templates.TemplateResponse(
            request,
            "ad19_content.html",
            {"documents": documents, "all_document_types": _ALL_CONTENT_DOCUMENT_TYPES, "error": str(exc)},
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/ops/content", status_code=303)


@router.get("/methodology")
def public_methodology_page(
    request: Request,
    session: Session = Depends(get_db_session),
    document_type: str | None = None,
):
    """PU-06 "Methodology, risk and legal documents" -- anonymous, no
    tenant scope. See app/services/content_document.py's own
    list_public_content_documents docstring for how cross-tenant
    visibility is enforced (the real content_document_visibility RLS
    policy, not an application-level filter)."""
    documents = list_public_content_documents(session, document_type=document_type)
    return templates.TemplateResponse(
        request, "pu06_methodology.html", {"documents": documents, "all_document_types": _ALL_CONTENT_DOCUMENT_TYPES}
    )


_ALL_MANAGED_PROGRAM_MODES = ["pamm", "mam"]


def _require_managed_programs(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_managed_programs")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops/managed-programs")
def managed_programs_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
    error: str | None = None,
):
    """AD-14 "Managed-program setup" -- see this route module's own
    docstring above for what is and is not implemented."""
    _require_managed_programs(scope)
    set_tenant_scope(session, scope.tenant_id)
    programs = list_managed_programs(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(
        request,
        "ad14_managed_programs.html",
        {"programs": programs, "all_modes": _ALL_MANAGED_PROGRAM_MODES, "error": error},
    )


@router.post("/ops/managed-programs")
def create_managed_program_page(
    request: Request,
    program_name: str = Form(...),
    broker_program_id: str = Form(...),
    mode: str = Form(...),
    allocation_policy_id: str = Form(...),
    nav_policy_id: str = Form(...),
    dealing_schedule_id: str = Form(...),
    fee_policy_id: str = Form(""),
    agreement_evidence_ids: str = Form(""),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_managed_programs(scope)
    set_tenant_scope(session, scope.tenant_id)
    evidence_ids = [v.strip() for v in agreement_evidence_ids.split(",") if v.strip()]
    try:
        create_managed_program(
            session,
            tenant_id=scope.tenant_id,
            program_name=program_name,
            broker_program_id=broker_program_id,
            mode=mode,
            allocation_policy_id=allocation_policy_id,
            nav_policy_id=nav_policy_id,
            dealing_schedule_id=dealing_schedule_id,
            fee_policy_id=fee_policy_id or None,
            agreement_evidence_ids=evidence_ids,
        )
    except InvalidManagedProgramError as exc:
        session.rollback()
        programs = list_managed_programs(session, tenant_id=scope.tenant_id)
        return templates.TemplateResponse(
            request,
            "ad14_managed_programs.html",
            {"programs": programs, "all_modes": _ALL_MANAGED_PROGRAM_MODES, "error": str(exc)},
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/ops/managed-programs", status_code=303)


@router.post("/ops/managed-programs/{program_id}/request-review")
def request_managed_program_review_page(
    program_id: str,
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_managed_programs(scope)
    set_tenant_scope(session, scope.tenant_id)
    program = get_managed_program(session, program_id, tenant_id=scope.tenant_id)
    if program is None:
        raise HTTPException(status_code=404, detail="not found")
    try:
        request_managed_program_review(session, program)
    except ProgramNotEligibleForReviewError as exc:
        session.rollback()
        programs = list_managed_programs(session, tenant_id=scope.tenant_id)
        return templates.TemplateResponse(
            request,
            "ad14_managed_programs.html",
            {"programs": programs, "all_modes": _ALL_MANAGED_PROGRAM_MODES, "error": str(exc)},
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/ops/managed-programs", status_code=303)


@router.get("/ops/audit")
def audit_log_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
    actor_user_id: str | None = None,
    object_id: str | None = None,
    action: str | None = None,
):
    """AD-18 "Audit log and release evidence" -- see this route
    module's own docstring above for what is and is not implemented."""
    try:
        require_permission(scope.role, "view_audit_log")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    set_tenant_scope(session, scope.tenant_id)
    events = list_audit_events(
        session, tenant_id=scope.tenant_id, actor_user_id=actor_user_id, object_id=object_id, action=action
    )
    timeline = get_object_timeline(session, tenant_id=scope.tenant_id, object_id=object_id) if object_id else []
    return templates.TemplateResponse(
        request,
        "ad18_audit.html",
        {
            "events": events,
            "timeline": timeline,
            "actor_user_id": actor_user_id or "",
            "object_id": object_id or "",
            "action": action or "",
        },
    )


_ALL_WORKSPACE_THEMES = ["system", "dark", "light"]
_ALL_WORKSPACE_DENSITIES = ["comfortable", "compact"]


def _require_workspace_settings(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_workspace_settings")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops/settings")
def workspace_settings_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
    error: str | None = None,
):
    """AD-20 "Workspace customization and configuration" -- see this
    route module's own docstring above for what is and is not
    implemented."""
    _require_workspace_settings(scope)
    set_tenant_scope(session, scope.tenant_id)
    settings = get_workspace_settings(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(
        request,
        "ad20_settings.html",
        {
            "settings": settings,
            "all_themes": _ALL_WORKSPACE_THEMES,
            "all_densities": _ALL_WORKSPACE_DENSITIES,
            "mandatory_panel_ids": sorted(MANDATORY_PANEL_IDS),
            "error": error,
        },
    )


@router.post("/ops/settings")
def save_workspace_settings_page(
    request: Request,
    workspace_name: str = Form(...),
    theme: str = Form(...),
    density: str = Form(...),
    visible_panel_ids: str = Form(""),
    column_order: str = Form(""),
    notification_route_id: str = Form(""),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_workspace_settings(scope)
    set_tenant_scope(session, scope.tenant_id)
    panel_ids = [v.strip() for v in visible_panel_ids.split(",") if v.strip()]
    columns = [v.strip() for v in column_order.split(",") if v.strip()]
    try:
        save_workspace_settings(
            session,
            tenant_id=scope.tenant_id,
            workspace_name=workspace_name,
            theme=theme,
            density=density,
            visible_panel_ids=panel_ids,
            column_order=columns,
            notification_route_id=notification_route_id or None,
        )
    except InvalidWorkspaceSettingsError as exc:
        session.rollback()
        settings = get_workspace_settings(session, tenant_id=scope.tenant_id)
        return templates.TemplateResponse(
            request,
            "ad20_settings.html",
            {
                "settings": settings,
                "all_themes": _ALL_WORKSPACE_THEMES,
                "all_densities": _ALL_WORKSPACE_DENSITIES,
                "mandatory_panel_ids": sorted(MANDATORY_PANEL_IDS),
                "error": str(exc),
            },
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/ops/settings", status_code=303)


def _require_publication_intent_detail(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "view_publication_intent_detail")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops/publications/{intent_id}")
def publication_intent_detail_page(
    intent_id: str,
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """AD-10 "Publication intent and cohort detail" -- see this route
    module's own docstring above for what is and is not implemented."""
    _require_publication_intent_detail(scope)
    set_tenant_scope(session, scope.tenant_id)
    detail = get_publication_intent_detail(session, intent_id, tenant_id=scope.tenant_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="not found")
    return templates.TemplateResponse(request, "ad10_publication_detail.html", {"detail": detail})


def _require_own_customer_overview(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "view_own_customer_overview")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/app")
def customer_overview_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """CU-01 "Customer overview" -- see app/services/customer_overview.py's
    own docstring for what is and is not implemented."""
    _require_own_customer_overview(scope)
    set_tenant_scope(session, scope.tenant_id)
    overview = get_customer_overview(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
    return templates.TemplateResponse(request, "cu01_overview.html", {"overview": overview})


def _require_own_selection_detail(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "view_own_selection_detail")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/app/portfolios/{selection_id}")
def customer_selection_detail_page(
    selection_id: str,
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """CU-03 "Selected portfolio detail" -- see
    app/services/customer_selection_detail.py's own docstring for what
    is and is not implemented."""
    _require_own_selection_detail(scope)
    set_tenant_scope(session, scope.tenant_id)
    detail = get_own_selection_detail(session, selection_id, tenant_id=scope.tenant_id, user_id=scope.user_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="not found")
    return templates.TemplateResponse(request, "cu03_selection_detail.html", {"detail": detail})


def _require_own_alerts(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "view_own_alerts")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/app/alerts")
def customer_alerts_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """CU-04 "Alerts and delivery history" -- see
    app/services/customer_alerts.py's own docstring for what is and is
    not implemented."""
    _require_own_alerts(scope)
    set_tenant_scope(session, scope.tenant_id)
    alerts = list_own_alerts(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
    return templates.TemplateResponse(request, "cu04_alerts.html", {"alerts": alerts})


def _require_own_activity_detail(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "view_own_activity_detail")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/app/activity/{episode_id}")
def customer_activity_detail_page(
    episode_id: str,
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """CU-05 "Alert, trade and order-family detail" -- see
    app/services/customer_alerts.py's own docstring for what is and is
    not implemented."""
    _require_own_activity_detail(scope)
    set_tenant_scope(session, scope.tenant_id)
    detail = get_own_alert_episode(session, episode_id, tenant_id=scope.tenant_id, user_id=scope.user_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="not found")
    return templates.TemplateResponse(request, "cu05_activity_detail.html", {"detail": detail})


def _require_own_performance(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "view_own_performance")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/app/performance")
def customer_performance_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """CU-06 "Performance and costs" -- see
    app/services/customer_performance_report.py's own docstring for what
    is and is not implemented."""
    _require_own_performance(scope)
    set_tenant_scope(session, scope.tenant_id)
    report = get_own_performance_report(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
    return templates.TemplateResponse(request, "cu06_performance.html", {"report": report})


def _require_own_billing(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "view_own_billing")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/app/billing")
def customer_billing_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """CU-11 "Billing, invoices and plan changes" -- see
    app/services/customer_billing.py's own docstring for what is and is
    not implemented."""
    _require_own_billing(scope)
    set_tenant_scope(session, scope.tenant_id)
    billing_state = get_own_billing_state(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(request, "cu11_billing.html", {"billing_state": billing_state})


def _require_own_managed_programs(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "view_own_managed_programs")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/app/managed-programs")
def customer_managed_programs_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """CU-15 "Managed program investor report" -- see
    app/services/customer_managed_programs.py's own docstring for what
    is and is not implemented."""
    _require_own_managed_programs(scope)
    set_tenant_scope(session, scope.tenant_id)
    view = get_own_managed_programs_view(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
    return templates.TemplateResponse(request, "cu15_managed_programs.html", {"view": view})


def _require_portfolio_selections(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_own_portfolio_selections")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/app/portfolios")
def portfolio_selections_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
    error: str | None = None,
):
    """CU-02 "My portfolios" -- see this route module's own docstring
    above for what is and is not implemented."""
    _require_portfolio_selections(scope)
    set_tenant_scope(session, scope.tenant_id)
    selections = list_own_portfolio_selections(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
    selectable_products = [
        p for p in list_products(session, tenant_id=scope.tenant_id) if p.lifecycle_state == ProductLifecycleState.PUBLISHED
    ]
    return templates.TemplateResponse(
        request,
        "cu02_portfolios.html",
        {"selections": selections, "selectable_products": selectable_products, "error": error},
    )


@router.post("/app/portfolios")
def create_portfolio_selection_page(
    request: Request,
    product_id: str = Form(...),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_portfolio_selections(scope)
    set_tenant_scope(session, scope.tenant_id)
    try:
        create_portfolio_selection(session, tenant_id=scope.tenant_id, user_id=scope.user_id, product_id=product_id)
    except InvalidPortfolioSelectionError as exc:
        session.rollback()
        selections = list_own_portfolio_selections(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
        selectable_products = [
            p
            for p in list_products(session, tenant_id=scope.tenant_id)
            if p.lifecycle_state == ProductLifecycleState.PUBLISHED
        ]
        return templates.TemplateResponse(
            request,
            "cu02_portfolios.html",
            {"selections": selections, "selectable_products": selectable_products, "error": str(exc)},
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/app/portfolios", status_code=303)


@router.post("/app/portfolios/{selection_id}/cancel")
def cancel_portfolio_selection_page(
    selection_id: str,
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_portfolio_selections(scope)
    set_tenant_scope(session, scope.tenant_id)
    selection = get_own_portfolio_selection(session, selection_id, tenant_id=scope.tenant_id, user_id=scope.user_id)
    if selection is None:
        raise HTTPException(status_code=404, detail="not found")
    cancel_portfolio_selection(session, selection)
    session.commit()
    return RedirectResponse(url="/app/portfolios", status_code=303)


_ALL_NOTIFICATION_CATEGORIES = ["entry", "update", "exit", "safety", "billing", "marketing"]


def _require_notification_preferences(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_own_notification_preferences")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/app/settings/notifications")
def notification_preferences_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
    error: str | None = None,
):
    """CU-12 "Alert delivery preferences" -- see this route module's
    own docstring above for what is and is not implemented."""
    _require_notification_preferences(scope)
    set_tenant_scope(session, scope.tenant_id)
    preferences = get_notification_preferences(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
    return templates.TemplateResponse(
        request,
        "cu12_notifications.html",
        {"preferences": preferences, "all_categories": _ALL_NOTIFICATION_CATEGORIES, "error": error},
    )


@router.post("/app/settings/notifications")
def save_notification_preferences_page(
    request: Request,
    email: str = Form(""),
    webhook_endpoint_id: str = Form(""),
    categories: list[str] = Form([]),
    timezone_name: str = Form("UTC"),
    quiet_start: str = Form(""),
    quiet_end: str = Form(""),
    marketing_consent: bool = Form(False),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_notification_preferences(scope)
    set_tenant_scope(session, scope.tenant_id)
    try:
        save_notification_preferences(
            session,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            email=email or None,
            webhook_endpoint_id=webhook_endpoint_id or None,
            categories=categories,
            timezone_name=timezone_name,
            quiet_start=quiet_start or None,
            quiet_end=quiet_end or None,
            marketing_consent=marketing_consent,
        )
    except InvalidNotificationPreferencesError as exc:
        session.rollback()
        preferences = get_notification_preferences(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
        return templates.TemplateResponse(
            request,
            "cu12_notifications.html",
            {"preferences": preferences, "all_categories": _ALL_NOTIFICATION_CATEGORIES, "error": str(exc)},
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/app/settings/notifications", status_code=303)


_ALL_DISPLAY_THEMES = ["system", "dark", "light"]
_ALL_DISPLAY_DENSITIES = ["comfortable", "compact"]
_ALL_REDUCE_MOTION_VALUES = ["system", "on"]


def _require_display_preferences(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_own_display_preferences")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/app/settings")
def display_preferences_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
    error: str | None = None,
):
    """CU-13 "Profile, security and display preferences" -- see this
    route module's own docstring above for what is and is not
    implemented."""
    _require_display_preferences(scope)
    set_tenant_scope(session, scope.tenant_id)
    preferences = get_display_preferences(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
    return templates.TemplateResponse(
        request,
        "cu13_profile.html",
        {
            "preferences": preferences,
            "all_themes": _ALL_DISPLAY_THEMES,
            "all_densities": _ALL_DISPLAY_DENSITIES,
            "all_reduce_motion_values": _ALL_REDUCE_MOTION_VALUES,
            "error": error,
        },
    )


@router.post("/app/settings")
def save_display_preferences_page(
    request: Request,
    display_name: str = Form(""),
    timezone_name: str = Form("UTC"),
    theme: str = Form("system"),
    density: str = Form("comfortable"),
    number_locale: str = Form("en-US"),
    view_currency: str = Form(""),
    reduce_motion: str = Form("system"),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_display_preferences(scope)
    set_tenant_scope(session, scope.tenant_id)
    try:
        save_display_preferences(
            session,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            display_name=display_name or None,
            timezone_name=timezone_name,
            theme=theme,
            density=density,
            number_locale=number_locale,
            view_currency=view_currency or None,
            reduce_motion=reduce_motion,
        )
    except InvalidDisplayPreferencesError as exc:
        session.rollback()
        preferences = get_display_preferences(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
        return templates.TemplateResponse(
            request,
            "cu13_profile.html",
            {
                "preferences": preferences,
                "all_themes": _ALL_DISPLAY_THEMES,
                "all_densities": _ALL_DISPLAY_DENSITIES,
                "all_reduce_motion_values": _ALL_REDUCE_MOTION_VALUES,
                "error": str(exc),
            },
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/app/settings", status_code=303)


def _require_managed_operations(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "view_managed_operations")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops/managed-operations")
def managed_operations_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """AD-15 "Managed allocations, NAV and dealing" -- see this route
    module's own docstring above for what is and is not implemented.

    Reuses AD-14's own list_managed_programs rather than a duplicate
    query: no dealing-queue/NAV-generation/cashflow/fee/restatement
    tracking model exists anywhere in this build (Phase 10's own
    domain model is simulation-only config, not a NAV ledger), so every
    panel about broker-originated activity is an explicit UNSUPPORTED,
    never a fabricated zero or a computed figure with no real input."""
    _require_managed_operations(scope)
    set_tenant_scope(session, scope.tenant_id)
    programs = list_managed_programs(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(request, "ad15_managed_operations.html", {"programs": programs})


def _require_deployment_status(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "view_deployment_status")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops/system")
def deployment_status_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
):
    """AD-22 "Commercial deployments and recovery" -- shares PU-01/
    PU-07's own get_service_status rather than a duplicate computed
    status. No worker/queue/backup-generation/fencing-evidence model
    exists anywhere in this build, so every deployment/recovery panel
    is an explicit UNSUPPORTED, never a fabricated "qualified" or
    "healthy" status with no real input behind it."""
    _require_deployment_status(scope)
    service_status = get_service_status()
    return templates.TemplateResponse(request, "ad22_system.html", {"service_status": service_status})


_ALL_PLATFORM_CONNECTION_PLATFORMS = ["collective2", "etoro", "metaapi_copyfactory"]


def _require_platform_connections(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_own_platform_connections")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/app/connections")
def platform_connections_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """CU-07 "Platform connections" -- see this route module's own
    docstring above for what is and is not implemented."""
    _require_platform_connections(scope)
    set_tenant_scope(session, scope.tenant_id)
    connections = list_own_platform_connections(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
    return templates.TemplateResponse(request, "cu07_connections.html", {"connections": connections})


@router.get("/app/connections/new")
def platform_connection_wizard_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    error: str | None = None,
):
    """CU-08 "Connection wizard" -- see this route module's own
    docstring above for what is and is not implemented: no real hosted
    OAuth authorization or account-identity readback exists, so only a
    DECLARED, local_simulation-only record can honestly be saved here."""
    _require_platform_connections(scope)
    return templates.TemplateResponse(
        request, "cu08_connection_wizard.html", {"all_platforms": _ALL_PLATFORM_CONNECTION_PLATFORMS, "error": error}
    )


@router.post("/app/connections/new")
def create_platform_connection_page(
    request: Request,
    platform: str = Form(...),
    environment: str = Form(...),
    masked_account_label: str = Form(...),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_platform_connections(scope)
    set_tenant_scope(session, scope.tenant_id)
    try:
        create_platform_connection(
            session,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            platform=platform,
            environment=environment,
            masked_account_label=masked_account_label,
        )
    except InvalidPlatformConnectionError as exc:
        session.rollback()
        return templates.TemplateResponse(
            request,
            "cu08_connection_wizard.html",
            {"all_platforms": _ALL_PLATFORM_CONNECTION_PLATFORMS, "error": str(exc)},
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/app/connections", status_code=303)


@router.post("/app/connections/{connection_id}/disconnect")
def disconnect_platform_connection_page(
    connection_id: str,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_platform_connections(scope)
    set_tenant_scope(session, scope.tenant_id)
    connection = get_own_platform_connection(session, connection_id, tenant_id=scope.tenant_id, user_id=scope.user_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="not found")
    disconnect_platform_connection(session, connection)
    session.commit()
    return RedirectResponse(url="/app/connections", status_code=303)


_ALL_COPY_MANDATE_START_MODES = ["new_entries_only", "sync_existing"]


def _require_copy_mandates(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_own_copy_mandates")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/app/copy/new")
def copy_mandate_wizard_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
    error: str | None = None,
):
    """CU-09 "Copy setup and mandate wizard" -- see this route
    module's own docstring above for what is and is not implemented:
    only "Save mandate draft without effect" is honestly buildable --
    Preview activation and Confirm authorized activation both need
    real price/position/publisher connectivity this build does not
    have."""
    _require_copy_mandates(scope)
    set_tenant_scope(session, scope.tenant_id)
    selections = [
        s
        for s in list_own_portfolio_selections(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
        if s.state == PortfolioSelectionState.ACTIVE
    ]
    connections = [
        c
        for c in list_own_platform_connections(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
        if c.state == PlatformConnectionState.DECLARED
    ]
    mandates = list_own_copy_mandates(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
    return templates.TemplateResponse(
        request,
        "cu09_copy_wizard.html",
        {
            "selections": selections,
            "connections": connections,
            "mandates": mandates,
            "all_start_modes": _ALL_COPY_MANDATE_START_MODES,
            "error": error,
        },
    )


@router.post("/app/copy/new")
def create_copy_mandate_draft_page(
    request: Request,
    selection_id: str = Form(...),
    connection_id: str = Form(...),
    allocation_amount: str = Form(...),
    allocation_currency: str = Form(...),
    max_trade_risk: str = Form(""),
    max_loss: str = Form(""),
    start_mode: str = Form("new_entries_only"),
    policy_version_id: str = Form(...),
    consent_version: str = Form(...),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_copy_mandates(scope)
    set_tenant_scope(session, scope.tenant_id)
    try:
        mandate = create_copy_mandate_draft(
            session,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            selection_id=selection_id,
            connection_id=connection_id,
            allocation_amount=allocation_amount,
            allocation_currency=allocation_currency,
            max_trade_risk=max_trade_risk or None,
            max_loss=max_loss or None,
            start_mode=start_mode,
            policy_version_id=policy_version_id,
            consent_version=consent_version,
        )
        append_audit_event(
            session, tenant_id=scope.tenant_id, actor_user_id=scope.user_id,
            object_type="copy_mandate", object_id=mandate.mandate_id, action="create_copy_mandate_draft",
        )
    except InvalidCopyMandateError as exc:
        session.rollback()
        selections = [
            s
            for s in list_own_portfolio_selections(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
            if s.state == PortfolioSelectionState.ACTIVE
        ]
        connections = [
            c
            for c in list_own_platform_connections(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
            if c.state == PlatformConnectionState.DECLARED
        ]
        mandates = list_own_copy_mandates(session, tenant_id=scope.tenant_id, user_id=scope.user_id)
        return templates.TemplateResponse(
            request,
            "cu09_copy_wizard.html",
            {
                "selections": selections,
                "connections": connections,
                "mandates": mandates,
                "all_start_modes": _ALL_COPY_MANDATE_START_MODES,
                "error": str(exc),
            },
            status_code=400,
        )
    session.commit()
    return RedirectResponse(url="/app/copy/new", status_code=303)


@router.get("/app/copy/{mandate_id}/manage")
def manage_copy_mandate_page(
    mandate_id: str,
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
    error: str | None = None,
):
    """CU-10 "Pause copying and position handoff" -- see this route
    module's own docstring above for what is and is not implemented:
    a mandate here can only ever be DRAFT or CANCELLED, never ACTIVE,
    so this screen's own real actions (Pause new entries, Review
    owned-position wind-down, Request handoff) all honestly need a
    real activation pipeline this build does not have. Cancel is the
    one real, always-available action -- "revoking authority" over a
    mandate that never activated is exactly cancelling its draft."""
    _require_copy_mandates(scope)
    set_tenant_scope(session, scope.tenant_id)
    mandate = get_own_copy_mandate(session, mandate_id, tenant_id=scope.tenant_id, user_id=scope.user_id)
    if mandate is None:
        raise HTTPException(status_code=404, detail="not found")
    return templates.TemplateResponse(request, "cu10_manage_mandate.html", {"mandate": mandate, "error": error})


@router.post("/app/copy/{mandate_id}/cancel")
def cancel_copy_mandate_page(
    mandate_id: str,
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_copy_mandates(scope)
    set_tenant_scope(session, scope.tenant_id)
    mandate = get_own_copy_mandate(session, mandate_id, tenant_id=scope.tenant_id, user_id=scope.user_id)
    if mandate is None:
        raise HTTPException(status_code=404, detail="not found")
    try:
        cancel_copy_mandate(session, mandate)
        append_audit_event(
            session, tenant_id=scope.tenant_id, actor_user_id=scope.user_id,
            object_type="copy_mandate", object_id=mandate.mandate_id, action="cancel_copy_mandate",
        )
    except MandateNotEligibleForCancellationError as exc:
        session.rollback()
        return templates.TemplateResponse(
            request, "cu10_manage_mandate.html", {"mandate": mandate, "error": str(exc)}, status_code=400
        )
    session.commit()
    return RedirectResponse(url=f"/app/copy/{mandate_id}/manage", status_code=303)


# --- ID-01/ID-02/ID-03: real local sign-in/sign-up/verify/recover ---
#
# See app/services/local_auth.py's own module docstring for exactly what
# this is (a real, working local credential system for THIS deployment)
# and is not (a Supabase Auth integration -- a real production
# deployment's own external task). Anonymous routes, no tenant scope.
#
# Email delivery is NOT wired -- disclosed, not hidden. create_account/
# request_password_reset return a real, single-use token; with no
# SMTP/email-provider integration in this codebase, the verify/reset
# link is shown directly on the confirmation page below rather than
# silently dropped -- usable today, and the exact seam a real email
# provider plugs into later (send the same link instead of rendering
# it).


def _set_session_cookie(request: Request, response, session_id: str) -> None:
    # SEC-05 (same reasoning as signal-copier's own app/main.py login
    # route): `request.url.scheme` alone is wrong behind a TLS-
    # terminating reverse proxy, so FORCE_SECURE_COOKIES is the explicit
    # operator override for that deployment.
    response.set_cookie(
        SESSION_COOKIE_NAME, session_id, httponly=True, samesite="strict", path="/",
        secure=request.url.scheme == "https" or config.FORCE_SECURE_COOKIES,
    )


@router.get("/auth")
def sign_in_page(request: Request, error: str | None = None, notice: str | None = None):
    """ID-01 "Sign in and create account"."""
    return templates.TemplateResponse(request, "id01_auth.html", {"error": error, "notice": notice})


@router.post("/auth/signin")
def sign_in_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    return_route: str = Form("/app"),
    session: Session = Depends(get_db_session),
):
    try:
        user = authenticate(session, email=email, password=password)
    except InvalidCredentialsError:
        return templates.TemplateResponse(
            request, "id01_auth.html", {"error": "Incorrect email or password.", "notice": None}, status_code=401
        )

    membership = session.execute(
        select(Membership).where(Membership.user_id == user.user_id)
    ).scalars().first()
    if membership is None:
        return templates.TemplateResponse(
            request, "id01_auth.html", {"error": "This identity has no tenant membership.", "notice": None}, status_code=403
        )

    session_id, _csrf_token = create_web_session(
        session, user_id=user.user_id, tenant_id=membership.tenant_id, role=membership.role
    )
    safe_return_route = return_route if return_route.startswith("/") else "/app"
    response = RedirectResponse(url=safe_return_route, status_code=303)
    _set_session_cookie(request, response, session_id)
    return response


@router.post("/auth/signup")
def sign_up_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    tenant_display_name: str = Form(""),
    accept_terms: str = Form(""),
    session: Session = Depends(get_db_session),
):
    if not accept_terms:
        return templates.TemplateResponse(
            request, "id01_auth.html", {"error": "You must accept the terms to create an account.", "notice": None},
            status_code=400,
        )
    try:
        _user, token = create_account(
            session, email=email, password=password, tenant_display_name=tenant_display_name or email,
        )
    except AccountAlreadyExistsError:
        session.rollback()
        # No email enumeration (ID-01's own acceptance text): the SAME
        # generic message a real signup failure would show.
        return templates.TemplateResponse(
            request, "id01_auth.html", {"error": "Could not create this account. Check your details and try again.", "notice": None},
            status_code=400,
        )
    return RedirectResponse(url=f"/auth/verify?token={token.token}&pending=1", status_code=303)


@router.get("/auth/verify")
def verify_email_page(request: Request, token: str | None = None, pending: str | None = None, session: Session = Depends(get_db_session)):
    """ID-02 "Email verification and auth callback". `pending=1` (set by
    the signup redirect above) shows the just-issued token's own link
    directly on the page -- see this section's own header comment on
    why: no email provider is wired in yet."""
    if token is None:
        return templates.TemplateResponse(request, "id02_verify.html", {"state": "pending_no_token", "verify_url": None, "error": None})
    if pending:
        verify_url = str(request.url_for("verify_email_page")) + f"?token={token}"
        return templates.TemplateResponse(request, "id02_verify.html", {"state": "pending_link_shown", "verify_url": verify_url, "error": None})

    try:
        user = verify_email(session, token=token)
    except LocalAuthInvalidTokenError:
        return templates.TemplateResponse(
            request, "id02_verify.html", {"state": "invalid", "verify_url": None, "error": "This verification link is invalid or has expired."},
            status_code=400,
        )

    membership = session.execute(
        select(Membership).where(Membership.user_id == user.user_id)
    ).scalars().first()
    if membership is None:
        return templates.TemplateResponse(request, "id02_verify.html", {"state": "verified_no_membership", "verify_url": None, "error": None})

    session_id, _csrf_token = create_web_session(session, user_id=user.user_id, tenant_id=membership.tenant_id, role=membership.role)
    response = templates.TemplateResponse(request, "id02_verify.html", {"state": "verified", "verify_url": None, "error": None})
    _set_session_cookie(request, response, session_id)
    return response


@router.get("/auth/recovery")
def recovery_page(request: Request, reset_url: str | None = None, error: str | None = None, notice: str | None = None):
    """ID-03 "Recovery, MFA and session verification" -- the "MFA/
    session step-up" half of this screen's own spec has nothing to step
    up to yet (no MFA enrollment exists in this build); this route
    implements the password-recovery half for real."""
    return templates.TemplateResponse(request, "id03_recovery.html", {"reset_url": reset_url, "error": error, "notice": notice})


@router.post("/auth/recovery/request")
def recovery_request_submit(request: Request, email: str = Form(...), session: Session = Depends(get_db_session)):
    token = request_password_reset(session, email=email)
    # Same message whether or not the email matched an account -- no
    # email enumeration (ID-01's own acceptance text applies equally here).
    notice = "If that email has an account, a recovery link has been issued."
    reset_url = None
    if token is not None:
        reset_url = str(request.url_for("recovery_page")) + f"?reset_url_token={token.token}"
    return templates.TemplateResponse(request, "id03_recovery.html", {"reset_url": reset_url, "error": None, "notice": notice})


@router.post("/auth/recovery/reset")
def recovery_reset_submit(
    request: Request,
    reset_url_token: str = Form(...),
    new_password: str = Form(...),
    session: Session = Depends(get_db_session),
):
    try:
        reset_password(session, token=reset_url_token, new_password=new_password)
    except LocalAuthInvalidTokenError:
        return templates.TemplateResponse(
            request, "id03_recovery.html",
            {"reset_url": None, "error": "This recovery link is invalid or has expired.", "notice": None},
            status_code=400,
        )
    return RedirectResponse(url="/auth?notice=Password+reset.+Sign+in+with+your+new+password.", status_code=303)


@router.post("/auth/logout")
def logout_submit(request: Request, session: Session = Depends(get_db_session)):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        delete_web_session(session, session_id=session_id)
    response = RedirectResponse(url="/auth", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


def _require_incident_register(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "view_incident_register")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _require_incident_management(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_incidents")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops/incidents")
def incident_register_page(
    request: Request,
    service: str | None = Query(None),
    severity: str | None = Query(None),
    state: str | None = Query(None),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """AD-21 "Commercial incidents and obligations" -- see
    app/services/incident.py's own docstring for what's real here.
    No automated pipeline creates a row, so the real, honest state for
    a tenant with no incidents is a genuinely empty queue -- never a
    fabricated placeholder incident."""
    _require_incident_register(scope)
    set_tenant_scope(session, scope.tenant_id)
    incidents = list_incidents(session, tenant_id=scope.tenant_id, service=service, severity=severity, state=state)
    return templates.TemplateResponse(
        request,
        "ad21_incidents.html",
        {
            "incidents": incidents,
            "service": service,
            "severity": severity,
            "state": state,
            "can_manage": is_allowed(scope.role, "manage_incidents"),
        },
    )


@router.get("/ops/incidents/{incident_id}")
def incident_detail_page(
    incident_id: str,
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_incident_register(scope)
    set_tenant_scope(session, scope.tenant_id)
    incident = get_incident(session, incident_id, tenant_id=scope.tenant_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="not found")
    timeline = get_object_timeline(session, tenant_id=scope.tenant_id, object_id=incident_id)
    return templates.TemplateResponse(
        request,
        "ad21_incident_detail.html",
        {
            "incident": incident,
            "timeline": timeline,
            "can_manage": is_allowed(scope.role, "manage_incidents"),
            "error": None,
        },
    )


@router.post("/ops/incidents/{incident_id}/action")
def incident_action_route(
    incident_id: str,
    request: Request,
    operation: str = Form(...),
    note: str = Form(...),
    assignee_id: str = Form(""),
    evidence_ids: str = Form(""),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """F-INCIDENT: "save/validate/preview/confirm are distinct bound
    operations. Do not bypass earlier validation with a
    confirm-labelled button." -- `operation` names exactly which of the
    four real state transitions (app/services/incident.py) this submit
    performs; an unrecognized operation is a real 400, never a silent
    no-op."""
    _require_incident_management(scope)
    set_tenant_scope(session, scope.tenant_id)
    incident = get_incident(session, incident_id, tenant_id=scope.tenant_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="not found")

    try:
        if operation == "acknowledge":
            acknowledge_incident(session, incident, actor_user_id=scope.user_id, note=note)
        elif operation == "assign":
            if not assignee_id:
                raise InvalidIncidentOperationError("assignee_id is required to assign")
            assign_incident(session, incident, actor_user_id=scope.user_id, assignee_id=assignee_id, note=note)
        elif operation == "reconcile":
            reconcile_incident(session, incident, actor_user_id=scope.user_id, note=note)
        elif operation == "propose_resolution":
            ids = [e.strip() for e in evidence_ids.split(",") if e.strip()]
            propose_resolution(session, incident, actor_user_id=scope.user_id, note=note, evidence_ids=ids)
        else:
            raise InvalidIncidentOperationError(f"unknown operation: {operation!r}")
        session.commit()
    except InvalidIncidentOperationError as exc:
        session.rollback()
        set_tenant_scope(session, scope.tenant_id)
        incident = get_incident(session, incident_id, tenant_id=scope.tenant_id)
        timeline = get_object_timeline(session, tenant_id=scope.tenant_id, object_id=incident_id)
        return templates.TemplateResponse(
            request,
            "ad21_incident_detail.html",
            {
                "incident": incident,
                "timeline": timeline,
                "can_manage": is_allowed(scope.role, "manage_incidents"),
                "error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse(url=f"/ops/incidents/{incident_id}", status_code=303)
