"""AD-11 "Customers and scoped support record" -- the real, read-only
staff view over a tenant's own customer memberships. See
dashboard_spec/screens/AD-11.md for the full screen contract this
implements a bounded slice of.

Deliberately built entirely from data other screens already made real:
Membership (role=customer), the customer's own EligibilityAssessment
(ID-04) and SupportCase rows (CU-14). No new model. `CustomerProfile`
(app/models/tenancy.py) is NOT used here -- its own docstring makes
clear it represents the tenant's own relationship as a customer OF THE
PLATFORM (one row per tenant), a completely different concept from an
individual retail customer of THIS tenant's own products.

`get_customer_support_record` returns None both when the user_id never
existed AND when it belongs to another tenant or isn't a CUSTOMER
membership at all -- callers turn None into a 404, never a 403 (the
same "scoped not-found" pattern as PU-03's own precedent), so support
staff cannot even confirm a cross-tenant user_id exists.

"Support cannot list secrets" (AD-11's own acceptance text) -- this
module never reads a broker credential, API key hash, or anything from
app/models/api_key.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.support_case import SupportCase
from app.models.tenancy import Membership, MembershipRole
from app.services.eligibility import EligibilityDecision, evaluate_eligibility, get_eligibility_assessment


@dataclass(frozen=True)
class CustomerSummary:
    user_id: str
    open_cases_count: int


@dataclass(frozen=True)
class CustomerSupportRecord:
    user_id: str
    eligibility_decisions: list[EligibilityDecision]
    cases: list[SupportCase]


def list_customers(session: Session, *, tenant_id: str) -> list[CustomerSummary]:
    memberships = session.scalars(
        select(Membership)
        .where(Membership.tenant_id == tenant_id, Membership.role == MembershipRole.CUSTOMER)
        .order_by(Membership.created_at.desc())
    ).all()

    summaries = []
    for membership in memberships:
        open_cases_count = session.scalar(
            select(func.count())
            .select_from(SupportCase)
            .where(SupportCase.tenant_id == tenant_id, SupportCase.user_id == membership.user_id)
        )
        summaries.append(CustomerSummary(user_id=membership.user_id, open_cases_count=open_cases_count or 0))
    return summaries


def get_customer_support_record(session: Session, *, tenant_id: str, user_id: str) -> CustomerSupportRecord | None:
    membership = session.get(Membership, (tenant_id, user_id))
    if membership is None or membership.role != MembershipRole.CUSTOMER:
        return None

    assessment = get_eligibility_assessment(session, tenant_id=tenant_id, user_id=user_id)
    decisions = evaluate_eligibility(session, assessment) if assessment is not None else []
    cases = list(
        session.scalars(
            select(SupportCase)
            .where(SupportCase.tenant_id == tenant_id, SupportCase.user_id == user_id)
            .order_by(SupportCase.created_at.desc())
        ).all()
    )
    return CustomerSupportRecord(user_id=user_id, eligibility_decisions=decisions, cases=cases)
