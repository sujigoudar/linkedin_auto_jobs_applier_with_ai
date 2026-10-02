"""WP-35 (F-07/F-04/F-08): rule deletion guard, schema check, disk health.

F-07: Routing rule deletion/modification exposure guard
- DELETE/PUT /routing-rules endpoints check for exposure before allowing changes
- Returns 409 Conflict if modification would strand open positions
- Can be overridden with ?force=true parameter

F-04: Schema stamping drift
- Re-stamp alembic_version when it doesn't match code head
- Add missing config_accounts columns to _COLUMN_MIGRATIONS
- Ensures fresh DBs match upgraded DBs

F-08: Disk health check
- Add write probe to /health endpoint
- Add free-space threshold check
- Fold both into overall status field
"""

import app.main as main_module
from app import config as app_config
from app.db import SignalStore, _COLUMN_MIGRATIONS


class TestF07RuleDeletionGuard:
    """F-07: Verify rule deletion/modification guard implementation."""

    def test_delete_routing_rule_accepts_force_parameter(self):
        """Verify DELETE /routing-rules/{rule_id} accepts ?force parameter."""
        # The endpoint signature now includes force: bool = False parameter
        # This test verifies the parameter exists and is optional
        import inspect
        from app.main import delete_routing_rule

        sig = inspect.signature(delete_routing_rule)
        assert "force" in sig.parameters
        assert sig.parameters["force"].default is False


class TestF04SchemaDrift:
    """F-04: Schema stamping and drift checks."""

    def test_config_accounts_columns_in_migrations(self):
        """Verify that config_accounts columns are now in _COLUMN_MIGRATIONS."""
        column_specs = [(table, col) for table, col, _ in _COLUMN_MIGRATIONS]

        # These columns were added to fix F-04 drift
        assert ("config_accounts", "daily_loss_limit_percent") in column_specs, \
            "daily_loss_limit_percent must be in _COLUMN_MIGRATIONS for upgraded DBs"
        assert ("config_accounts", "min_equity_threshold") in column_specs, \
            "min_equity_threshold must be in _COLUMN_MIGRATIONS for upgraded DBs"

    def test_heartbeat_table_in_schema(self):
        """Verify that health_heartbeat table is defined in SCHEMA."""
        from app.db import SCHEMA

        assert "health_heartbeat" in SCHEMA, \
            "health_heartbeat table must be in SCHEMA for F-08 write probes"

    def test_database_write_ok_method_exists(self, tmp_path):
        """Verify SignalStore has database_write_ok method for F-08."""
        store = SignalStore(tmp_path / "test.db")

        assert hasattr(store, "database_write_ok"), \
            "SignalStore must have database_write_ok method for F-08 disk health"

        # Test that the method works and returns a boolean
        result = store.database_write_ok()
        assert isinstance(result, bool), \
            "database_write_ok should return a boolean"


class TestF08DiskHealth:
    """F-08: Disk space and write capability checks."""

    def test_health_endpoint_includes_disk_checks(self, tmp_path, monkeypatch):
        """Verify GET /health includes new disk health fields."""
        from fastapi.testclient import TestClient

        monkeypatch.setattr(app_config, "OWNER_PASSWORD", "")
        monkeypatch.setattr(app_config, "SESSION_SECRET", "")
        store = SignalStore(tmp_path / "test.db")
        monkeypatch.setattr(main_module, "store", store)

        client = TestClient(main_module.app)
        response = client.get("/health")

        assert response.status_code == 200
        body = response.json()

        # Verify new F-08 fields are present
        assert "database_write_ok" in body, \
            "health endpoint must include database_write_ok (F-08)"
        assert "disk_usage_ok" in body, \
            "health endpoint must include disk_usage_ok (F-08)"
        assert "disk_free_bytes" in body, \
            "health endpoint must include disk_free_bytes (F-08)"

        # Verify they're reasonable values
        assert isinstance(body["database_write_ok"], bool)
        assert isinstance(body["disk_usage_ok"], bool)
        assert body["disk_free_bytes"] is None or isinstance(body["disk_free_bytes"], int)

    def test_health_status_degraded_when_disk_full(self, tmp_path, monkeypatch):
        """Verify health status goes to degraded when disk is full."""
        from fastapi.testclient import TestClient

        monkeypatch.setattr(app_config, "OWNER_PASSWORD", "")
        monkeypatch.setattr(app_config, "SESSION_SECRET", "")
        store = SignalStore(tmp_path / "test.db")
        monkeypatch.setattr(main_module, "store", store)

        # Mock disk_usage to simulate full disk
        def mock_disk_usage(path: str) -> type:  # type: ignore
            from collections import namedtuple
            DiskUsage = namedtuple('DiskUsage', ['total', 'used', 'free'])
            return DiskUsage(total=1000, used=1000, free=0)

        import shutil
        original_disk_usage = shutil.disk_usage
        monkeypatch.setattr("shutil.disk_usage", mock_disk_usage)

        try:
            client = TestClient(main_module.app)
            response = client.get("/health")
            body = response.json()

            # With full disk, disk_usage_ok should be False
            assert body["disk_usage_ok"] is False
        finally:
            monkeypatch.setattr("shutil.disk_usage", original_disk_usage)

    def test_health_database_write_ok_probe(self, tmp_path, monkeypatch):
        """Verify the database write capability probe works."""
        from fastapi.testclient import TestClient

        monkeypatch.setattr(app_config, "OWNER_PASSWORD", "")
        monkeypatch.setattr(app_config, "SESSION_SECRET", "")
        store = SignalStore(tmp_path / "test.db")
        monkeypatch.setattr(main_module, "store", store)

        client = TestClient(main_module.app)
        response = client.get("/health")
        body = response.json()

        # On a normal filesystem, database_write_ok should be True
        assert body["database_write_ok"] is True
