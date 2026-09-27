"""CU-13 "Profile, security and display preferences" -- the real
save/read service backing F-PREFERENCES's Profile/Display steps. See
dashboard_spec/screens/CU-13.md for the full screen contract this
implements a bounded slice of.

`_FORBIDDEN_MARKUP_CHARS` reuses AD-19's own "No raw HTML/script
editor" discipline for `display_name` -- a customer's own profile name
is stored plain text only, never sanitized/stripped and silently
accepted, since it may be rendered elsewhere (e.g. an eventual staff
customer-support view) without re-escaping being guaranteed.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.customer_display_preferences import (
    CustomerDisplayPreferences,
    DisplayDensity,
    DisplayTheme,
    ReduceMotion,
)

_MAX_DISPLAY_NAME_LENGTH = 80
_FORBIDDEN_MARKUP_CHARS = frozenset({"<", ">"})
_LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class InvalidDisplayPreferencesError(Exception):
    pass


def get_display_preferences(session: Session, *, tenant_id: str, user_id: str) -> CustomerDisplayPreferences | None:
    return session.get(CustomerDisplayPreferences, (tenant_id, user_id))


def save_display_preferences(
    session: Session,
    *,
    tenant_id: str,
    user_id: str,
    display_name: str | None,
    timezone_name: str,
    theme: str,
    density: str,
    number_locale: str,
    view_currency: str | None,
    reduce_motion: str,
) -> CustomerDisplayPreferences:
    if display_name is not None:
        if len(display_name) > _MAX_DISPLAY_NAME_LENGTH:
            raise InvalidDisplayPreferencesError(f"display_name must be at most {_MAX_DISPLAY_NAME_LENGTH} characters")
        if any(char in display_name for char in _FORBIDDEN_MARKUP_CHARS):
            raise InvalidDisplayPreferencesError("display_name must be plain text -- no raw HTML/script markup is permitted")
    if not timezone_name or not timezone_name.strip():
        raise InvalidDisplayPreferencesError("timezone_name is required")
    try:
        theme_enum = DisplayTheme(theme)
    except ValueError as exc:
        raise InvalidDisplayPreferencesError(f"{theme!r} is not a known theme") from exc
    try:
        density_enum = DisplayDensity(density)
    except ValueError as exc:
        raise InvalidDisplayPreferencesError(f"{density!r} is not a known density") from exc
    try:
        reduce_motion_enum = ReduceMotion(reduce_motion)
    except ValueError as exc:
        raise InvalidDisplayPreferencesError(f"{reduce_motion!r} is not a known reduce_motion value") from exc
    if not _LOCALE_RE.match(number_locale):
        raise InvalidDisplayPreferencesError(f"{number_locale!r} is not a supported locale (expected e.g. 'en-US')")
    if view_currency is not None and not _CURRENCY_RE.match(view_currency):
        raise InvalidDisplayPreferencesError(f"{view_currency!r} is not a supported currency code (expected e.g. 'USD')")

    preferences = session.get(CustomerDisplayPreferences, (tenant_id, user_id))
    if preferences is None:
        preferences = CustomerDisplayPreferences(tenant_id=tenant_id, user_id=user_id)
        session.add(preferences)

    preferences.display_name = display_name
    preferences.timezone_name = timezone_name
    preferences.theme = theme_enum
    preferences.density = density_enum
    preferences.number_locale = number_locale
    preferences.view_currency = view_currency
    preferences.reduce_motion = reduce_motion_enum
    preferences.updated_at = datetime.now(timezone.utc)
    session.flush()
    return preferences
