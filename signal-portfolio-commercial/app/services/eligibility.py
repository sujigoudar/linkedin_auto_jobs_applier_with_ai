"""ID-04 "Service eligibility onboarding" -- the real save/evaluate
service backing F-ELIGIBILITY. See dashboard_spec/screens/ID-04.md for
the full screen contract this implements a bounded slice of.

`evaluate_eligibility` is always computed live from the saved facts
plus the real published-product catalog (`list_published_products`),
never a stored verdict column -- "Residence change invalidates
affected service eligibility and triggers review" holds automatically,
since there is no stale decision to invalidate in the first place.

Deliberately bounded jurisdiction policy: only "US" is a supported
residence, matching every other real fixture/test in this build that
touches jurisdiction (app/services/product_admin.py's own rights
check, RightsGrant test fixtures). Entity onboarding is a real, named
UNSUPPORTED reason, never silently treated like an individual -- "
Entities need their actual approved onboarding flow" (ID-04's own field
help text).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.eligibility import CustomerType, EligibilityAssessment
from app.services.product_admin import list_published_products

_SUPPORTED_JURISDICTIONS: frozenset[str] = frozenset({"US"})
_VALID_SERVICE_MODES: frozenset[str] = frozenset({"research", "alerts", "copying", "managed_program"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InvalidEligibilityFactsError(Exception):
    pass


@dataclass(frozen=True)
class EligibilityDecision:
    service: str
    decision: str  # ELIGIBLE / PENDING / UNSUPPORTED
    reason: str


def get_eligibility_assessment(session: Session, *, tenant_id: str, user_id: str) -> EligibilityAssessment | None:
    return session.get(EligibilityAssessment, (tenant_id, user_id))


def save_eligibility_facts(
    session: Session,
    *,
    tenant_id: str,
    user_id: str,
    residence_country: str,
    tax_residence: list[str],
    customer_type: str,
    requested_service_modes: list[str],
    document_versions: list[str],
    facts_confirmed: bool,
) -> EligibilityAssessment:
    if not residence_country or not residence_country.strip():
        raise InvalidEligibilityFactsError("residence_country is required")
    if not requested_service_modes:
        raise InvalidEligibilityFactsError("at least one requested service is required")
    unknown = sorted(set(requested_service_modes) - _VALID_SERVICE_MODES)
    if unknown:
        raise InvalidEligibilityFactsError(f"unknown requested service(s): {unknown}")
    try:
        customer_type_enum = CustomerType(customer_type)
    except ValueError as exc:
        raise InvalidEligibilityFactsError(f"{customer_type!r} is not a valid customer_type") from exc

    assessment = session.get(EligibilityAssessment, (tenant_id, user_id))
    if assessment is None:
        assessment = EligibilityAssessment(tenant_id=tenant_id, user_id=user_id)
        session.add(assessment)

    assessment.residence_country = residence_country
    assessment.tax_residence = list(tax_residence)
    assessment.customer_type = customer_type_enum
    assessment.requested_service_modes = list(requested_service_modes)
    assessment.document_versions = list(document_versions)
    assessment.facts_confirmed = facts_confirmed
    assessment.updated_at = _now()
    session.flush()
    return assessment


def evaluate_eligibility(session: Session, assessment: EligibilityAssessment) -> list[EligibilityDecision]:
    published = list_published_products(session)
    decisions: list[EligibilityDecision] = []
    for service in assessment.requested_service_modes:
        if assessment.residence_country not in _SUPPORTED_JURISDICTIONS:
            decisions.append(EligibilityDecision(service, "UNSUPPORTED", "JURISDICTION_NOT_SUPPORTED"))
            continue
        if assessment.customer_type == CustomerType.ENTITY:
            decisions.append(EligibilityDecision(service, "UNSUPPORTED", "ENTITY_ONBOARDING_NOT_IMPLEMENTED"))
            continue
        if not assessment.facts_confirmed:
            decisions.append(EligibilityDecision(service, "PENDING", "FACTS_NOT_CONFIRMED"))
            continue
        if service != "research" and not any(service in product.service_modes for product in published):
            decisions.append(EligibilityDecision(service, "UNSUPPORTED", "NO_PUBLISHED_PRODUCT_FOR_SERVICE"))
            continue
        decisions.append(EligibilityDecision(service, "ELIGIBLE", "POLICY_SATISFIED"))
    return decisions
