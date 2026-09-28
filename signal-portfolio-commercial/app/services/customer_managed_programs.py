"""CU-15 "Managed program investor report" -- the real, tenant-scoped
read backing `/app/managed-programs`. See
dashboard_spec/screens/CU-15.md for the full screen contract this
implements a bounded slice of.

## Why the program list is always empty today, honestly

`ManagedProgram` (app/models/managed_program.py, AD-14's own service)
only ever holds DRAFT or SUBMITTED_FOR_REVIEW rows -- its own docstring
is explicit: "actual program activation/release is a separate,
not-yet-built admission decision," and `ManagedProgramState` has no
third value at all. A customer can therefore never legitimately see an
approved broker-native program in this build yet -- there is no
admitted state to query for. `list_customer_visible_managed_programs`
still runs a real, correctly-scoped query (not a hardcoded `[]`) so
that a future admission slice that adds an approved/active state makes
programs reachable here without this module changing, the same "real
query, unreachable result until a real connector exists" shape
app/services/customer_performance_state.py's own docstring already
establishes. Until then, CU-15's own documented empty state ("No
approved managed-account program is available to you") is always the
honest, correct render -- never a fabricated NAV, allocation or fee
figure standing in for a program that was never actually admitted.

## Program eligibility checklist (CU-15-P01)

Reuses ID-04's own `evaluate_eligibility` for the `managed_program`
service (never a second, parallel eligibility computation), plus this
tenant's own billing entitlement (`authorizes_new_entry`, app/services/
entitlement.py) as an independent payment condition -- "Do not collapse
payment, connection, rights and trading authority into one active
badge" (CU-15-P01's own contract) holds by construction: each condition
below is its own row with its own real source, never merged.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.eligibility import EligibilityAssessment
from app.models.managed_program import ManagedProgram, ManagedProgramState
from app.services.customer_billing import get_own_billing_state
from app.services.eligibility import EligibilityDecision, evaluate_eligibility, get_eligibility_assessment

#: No admitted/approved state exists in `ManagedProgramState` yet (see
#: this module's own docstring) -- an intentionally empty allowlist, not
#: an oversight, so a customer is never shown a DRAFT or
#: SUBMITTED_FOR_REVIEW program the tenant has not actually released to
#: it.
_CUSTOMER_VISIBLE_STATES: frozenset[ManagedProgramState] = frozenset()


@dataclass(frozen=True)
class EligibilityChecklistItem:
    condition: str
    outcome: str  # MET / NOT_MET / PENDING / UNKNOWN
    reason: str


@dataclass(frozen=True)
class CustomerManagedProgramsView:
    programs: list[ManagedProgram]
    eligibility_checklist: list[EligibilityChecklistItem]


def list_customer_visible_managed_programs(session: Session, *, tenant_id: str) -> list[ManagedProgram]:
    if not _CUSTOMER_VISIBLE_STATES:
        return []
    return list(
        session.scalars(
            select(ManagedProgram).where(
                ManagedProgram.tenant_id == tenant_id, ManagedProgram.state.in_(_CUSTOMER_VISIBLE_STATES)
            )
        ).all()
    )


def _eligibility_checklist(
    assessment: EligibilityAssessment | None, decisions: list[EligibilityDecision], *, has_new_entry_authority: bool | None
) -> list[EligibilityChecklistItem]:
    items: list[EligibilityChecklistItem] = []

    if assessment is None:
        items.append(
            EligibilityChecklistItem("Eligibility facts", "PENDING", "No eligibility facts have been submitted yet.")
        )
    else:
        managed_decision = next((d for d in decisions if d.service == "managed_program"), None)
        if managed_decision is None:
            items.append(
                EligibilityChecklistItem(
                    "Service eligibility", "PENDING", "managed_program was not requested in your eligibility facts."
                )
            )
        else:
            outcome = "MET" if managed_decision.decision == "ELIGIBLE" else managed_decision.decision
            items.append(EligibilityChecklistItem("Service eligibility", outcome, managed_decision.reason))

    if has_new_entry_authority is None:
        items.append(EligibilityChecklistItem("Payment", "UNKNOWN", "No subscription exists for this account."))
    else:
        items.append(
            EligibilityChecklistItem(
                "Payment",
                "MET" if has_new_entry_authority else "NOT_MET",
                "Subscription currently authorizes new premium actions."
                if has_new_entry_authority
                else "Subscription does not currently authorize new premium actions.",
            )
        )

    items.append(
        EligibilityChecklistItem(
            "Broker-native program admission",
            "NOT_MET",
            "No approved managed-account program admission exists in this build yet -- program activation/release "
            "is a separate, not-yet-built admission decision.",
        )
    )
    return items


def get_own_managed_programs_view(session: Session, *, tenant_id: str, user_id: str) -> CustomerManagedProgramsView:
    programs = list_customer_visible_managed_programs(session, tenant_id=tenant_id)

    assessment = get_eligibility_assessment(session, tenant_id=tenant_id, user_id=user_id)
    decisions = evaluate_eligibility(session, assessment) if assessment is not None else []
    billing_state = get_own_billing_state(session, tenant_id=tenant_id)
    checklist = _eligibility_checklist(
        assessment, decisions, has_new_entry_authority=billing_state.authorizes_new_entry
    )

    return CustomerManagedProgramsView(programs=programs, eligibility_checklist=checklist)
