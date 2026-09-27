"""AD-20 "Workspace customization and configuration" -- the real
save/read service backing F-WORKSPACE. See
dashboard_spec/screens/AD-20.md for the full screen contract this
implements a bounded slice of.

`MANDATORY_PANEL_IDS` enforces "Mandatory safety/fees/origin cannot
hide" (AD-20's own field help text) literally: a submitted
`visible_panel_ids` list containing one of these ids is always
refused, never silently dropped or silently accepted -- the panel ids
are AD-01's own two real panel headings ("release_blockers",
"business_indicators"), not invented placeholders.

There is deliberately no field here that could touch a permission,
policy, or financial value -- only display/notification-routing
references -- so "Cosmetic configuration never changes policy,
financial permissions or required disclosure visibility" (AD-20's own
acceptance text) is true by construction, not by a runtime check.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.workspace_settings import WorkspaceDensity, WorkspaceSettings, WorkspaceTheme

_MAX_WORKSPACE_NAME_LENGTH = 80

#: AD-01's own two real panel headings ("Release blockers",
#: "Business indicators") -- these can never be hidden via this
#: screen's cosmetic panel-visibility control.
MANDATORY_PANEL_IDS: frozenset[str] = frozenset({"release_blockers", "business_indicators"})


class InvalidWorkspaceSettingsError(Exception):
    pass


def get_workspace_settings(session: Session, *, tenant_id: str) -> WorkspaceSettings | None:
    return session.get(WorkspaceSettings, tenant_id)


def save_workspace_settings(
    session: Session,
    *,
    tenant_id: str,
    workspace_name: str,
    theme: str,
    density: str,
    visible_panel_ids: list[str],
    column_order: list[str],
    notification_route_id: str | None,
) -> WorkspaceSettings:
    if not workspace_name or not (1 <= len(workspace_name) <= _MAX_WORKSPACE_NAME_LENGTH):
        raise InvalidWorkspaceSettingsError(f"workspace_name must be 1..{_MAX_WORKSPACE_NAME_LENGTH} characters")
    try:
        theme_enum = WorkspaceTheme(theme)
    except ValueError as exc:
        raise InvalidWorkspaceSettingsError(f"{theme!r} is not a known theme") from exc
    try:
        density_enum = WorkspaceDensity(density)
    except ValueError as exc:
        raise InvalidWorkspaceSettingsError(f"{density!r} is not a known density") from exc

    hidden_mandatory = MANDATORY_PANEL_IDS - set(visible_panel_ids)
    if hidden_mandatory:
        raise InvalidWorkspaceSettingsError(
            f"mandatory panels cannot be hidden: {sorted(hidden_mandatory)!r}"
        )

    settings = session.get(WorkspaceSettings, tenant_id)
    if settings is None:
        settings = WorkspaceSettings(tenant_id=tenant_id)
        session.add(settings)

    settings.workspace_name = workspace_name
    settings.theme = theme_enum
    settings.density = density_enum
    settings.visible_panel_ids = list(visible_panel_ids)
    settings.column_order = list(column_order)
    settings.notification_route_id = notification_route_id
    settings.updated_at = datetime.now(timezone.utc)
    session.flush()
    return settings
