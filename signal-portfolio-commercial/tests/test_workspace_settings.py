"""AD-20 "Workspace customization and configuration" --
app/services/workspace_settings.py's own tests. Real Postgres, real
tenant-scoped session."""
import pytest

from app.models.workspace_settings import WorkspaceDensity, WorkspaceTheme
from app.services.workspace_settings import (
    MANDATORY_PANEL_IDS,
    InvalidWorkspaceSettingsError,
    get_workspace_settings,
    save_workspace_settings,
)


def test_get_workspace_settings_is_none_before_any_are_saved(db_session):
    assert get_workspace_settings(db_session, tenant_id="tenant-a") is None


def test_save_workspace_settings_creates_the_row(db_session):
    settings = save_workspace_settings(
        db_session,
        tenant_id="tenant-a",
        workspace_name="Ops desk",
        theme="dark",
        density="compact",
        visible_panel_ids=["release_blockers", "business_indicators", "research_queue"],
        column_order=["name", "state"],
        notification_route_id=None,
    )
    assert settings.workspace_name == "Ops desk"
    assert settings.theme == WorkspaceTheme.DARK
    assert settings.density == WorkspaceDensity.COMPACT


def test_save_then_reload_persists_the_real_row(db_session):
    save_workspace_settings(
        db_session,
        tenant_id="tenant-a",
        workspace_name="Ops desk",
        theme="system",
        density="comfortable",
        visible_panel_ids=["release_blockers", "business_indicators"],
        column_order=[],
        notification_route_id=None,
    )
    db_session.commit()

    settings = get_workspace_settings(db_session, tenant_id="tenant-a")
    assert settings is not None
    assert settings.workspace_name == "Ops desk"


def test_save_workspace_settings_updates_the_existing_row_not_a_new_one(db_session):
    save_workspace_settings(
        db_session,
        tenant_id="tenant-a",
        workspace_name="First name",
        theme="system",
        density="comfortable",
        visible_panel_ids=["release_blockers", "business_indicators"],
        column_order=[],
        notification_route_id=None,
    )
    db_session.commit()

    save_workspace_settings(
        db_session,
        tenant_id="tenant-a",
        workspace_name="Second name",
        theme="dark",
        density="compact",
        visible_panel_ids=["release_blockers", "business_indicators"],
        column_order=[],
        notification_route_id=None,
    )
    db_session.commit()

    settings = get_workspace_settings(db_session, tenant_id="tenant-a")
    assert settings.workspace_name == "Second name"
    assert settings.theme == WorkspaceTheme.DARK


def test_get_workspace_settings_is_none_for_a_different_tenant(db_session):
    save_workspace_settings(
        db_session,
        tenant_id="tenant-a",
        workspace_name="Ops desk",
        theme="system",
        density="comfortable",
        visible_panel_ids=["release_blockers", "business_indicators"],
        column_order=[],
        notification_route_id=None,
    )
    db_session.commit()

    assert get_workspace_settings(db_session, tenant_id="tenant-b") is None


def test_save_workspace_settings_rejects_an_unknown_theme(db_session):
    with pytest.raises(InvalidWorkspaceSettingsError, match="theme"):
        save_workspace_settings(
            db_session,
            tenant_id="tenant-a",
            workspace_name="Ops desk",
            theme="neon",
            density="comfortable",
            visible_panel_ids=["release_blockers", "business_indicators"],
            column_order=[],
            notification_route_id=None,
        )


def test_save_workspace_settings_rejects_an_unknown_density(db_session):
    with pytest.raises(InvalidWorkspaceSettingsError, match="density"):
        save_workspace_settings(
            db_session,
            tenant_id="tenant-a",
            workspace_name="Ops desk",
            theme="system",
            density="ultra",
            visible_panel_ids=["release_blockers", "business_indicators"],
            column_order=[],
            notification_route_id=None,
        )


def test_save_workspace_settings_rejects_hiding_a_mandatory_panel(db_session):
    assert MANDATORY_PANEL_IDS == frozenset({"release_blockers", "business_indicators"})
    with pytest.raises(InvalidWorkspaceSettingsError, match="mandatory panels cannot be hidden"):
        save_workspace_settings(
            db_session,
            tenant_id="tenant-a",
            workspace_name="Ops desk",
            theme="system",
            density="comfortable",
            visible_panel_ids=["research_queue"],
            column_order=[],
            notification_route_id=None,
        )


def test_save_workspace_settings_rejects_an_empty_workspace_name(db_session):
    with pytest.raises(InvalidWorkspaceSettingsError, match="workspace_name"):
        save_workspace_settings(
            db_session,
            tenant_id="tenant-a",
            workspace_name="",
            theme="system",
            density="comfortable",
            visible_panel_ids=["release_blockers", "business_indicators"],
            column_order=[],
            notification_route_id=None,
        )
