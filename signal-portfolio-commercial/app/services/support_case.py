"""CU-14 "Support and incident case" -- the real save/list service
backing F-SUPPORT. See dashboard_spec/screens/CU-14.md for the full
screen contract this implements a bounded slice of.

`related_object_id`, when given, is validated against a real
Subscription in the caller's OWN tenant -- "No cross-tenant IDs" (CU-14's
own field help text) is enforced here, not just documented: a customer
can never reference another tenant's subscription id, even one that
genuinely exists.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billing import Subscription
from app.models.support_case import SupportCase, SupportCaseCategory

_VALID_CATEGORIES: frozenset[str] = frozenset({category.value for category in SupportCaseCategory})
_MAX_SUBJECT_LENGTH = 120
_MAX_DESCRIPTION_LENGTH = 8000


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InvalidSupportCaseError(Exception):
    pass


def list_support_cases(session: Session, *, tenant_id: str, user_id: str) -> list[SupportCase]:
    return list(
        session.scalars(
            select(SupportCase)
            .where(SupportCase.tenant_id == tenant_id, SupportCase.user_id == user_id)
            .order_by(SupportCase.created_at.desc())
        ).all()
    )


def create_support_case(
    session: Session,
    *,
    tenant_id: str,
    user_id: str,
    category: str,
    related_object_id: str | None,
    subject: str,
    description: str,
    attachment_ids: list[str],
) -> SupportCase:
    if category not in _VALID_CATEGORIES:
        raise InvalidSupportCaseError(f"{category!r} is not a known support case category")
    if not subject or not (1 <= len(subject) <= _MAX_SUBJECT_LENGTH):
        raise InvalidSupportCaseError(f"subject must be 1..{_MAX_SUBJECT_LENGTH} characters")
    if not description or not (1 <= len(description) <= _MAX_DESCRIPTION_LENGTH):
        raise InvalidSupportCaseError(f"description must be 1..{_MAX_DESCRIPTION_LENGTH} characters")

    if related_object_id:
        subscription = session.get(Subscription, related_object_id)
        if subscription is None or subscription.tenant_id != tenant_id:
            raise InvalidSupportCaseError("related_object_id does not reference a record you can access")

    case = SupportCase(
        tenant_id=tenant_id,
        user_id=user_id,
        category=SupportCaseCategory(category),
        related_object_id=related_object_id or None,
        subject=subject,
        description=description,
        attachment_ids=list(attachment_ids),
    )
    session.add(case)
    session.flush()
    return case
