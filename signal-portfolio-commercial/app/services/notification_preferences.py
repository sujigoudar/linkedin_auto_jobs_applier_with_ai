"""CU-12 "Alert delivery preferences" -- the real save/read service
backing F-DELIVERY. See dashboard_spec/screens/CU-12.md for the full
screen contract this implements a bounded slice of.

`MANDATORY_CATEGORIES` enforces "Safety notices follow agreed policy
independently of marketing preferences" (CU-12's own acceptance text)
literally: a submitted `categories` list missing "safety" is always
refused, never silently accepted -- the same discipline as AD-20's own
`MANDATORY_PANEL_IDS`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.notification_preferences import NotificationPreferences

_VALID_CATEGORIES: frozenset[str] = frozenset({"entry", "update", "exit", "safety", "billing", "marketing"})

#: "Safety follows agreed policy, not marketing toggle" (CU-12's own
#: field help text) -- a customer can never opt out of safety notices
#: through this form.
MANDATORY_CATEGORIES: frozenset[str] = frozenset({"safety"})


class InvalidNotificationPreferencesError(Exception):
    pass


def get_notification_preferences(session: Session, *, tenant_id: str, user_id: str) -> NotificationPreferences | None:
    return session.get(NotificationPreferences, (tenant_id, user_id))


def save_notification_preferences(
    session: Session,
    *,
    tenant_id: str,
    user_id: str,
    email: str | None,
    webhook_endpoint_id: str | None,
    categories: list[str],
    timezone_name: str,
    quiet_start: str | None,
    quiet_end: str | None,
    marketing_consent: bool,
) -> NotificationPreferences:
    unknown_categories = set(categories) - _VALID_CATEGORIES
    if unknown_categories:
        raise InvalidNotificationPreferencesError(f"unknown categor(y/ies): {sorted(unknown_categories)!r}")
    missing_mandatory = MANDATORY_CATEGORIES - set(categories)
    if missing_mandatory:
        raise InvalidNotificationPreferencesError(
            f"mandatory categories cannot be excluded: {sorted(missing_mandatory)!r}"
        )
    if not timezone_name or not timezone_name.strip():
        raise InvalidNotificationPreferencesError("timezone_name is required")
    if bool(quiet_start) != bool(quiet_end):
        raise InvalidNotificationPreferencesError("quiet_start and quiet_end must both be set or both be empty")

    preferences = session.get(NotificationPreferences, (tenant_id, user_id))
    if preferences is None:
        preferences = NotificationPreferences(tenant_id=tenant_id, user_id=user_id)
        session.add(preferences)

    preferences.email = email
    preferences.webhook_endpoint_id = webhook_endpoint_id
    preferences.categories = list(categories)
    preferences.timezone_name = timezone_name
    preferences.quiet_start = quiet_start
    preferences.quiet_end = quiet_end
    preferences.marketing_consent = marketing_consent
    preferences.updated_at = datetime.now(timezone.utc)
    session.flush()
    return preferences
