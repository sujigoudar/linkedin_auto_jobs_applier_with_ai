"""WP-30: Loss limits end-to-end (B-08 + F-02).

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-02 00:00:00.000000

Wire the daily loss limit and minimum equity threshold circuit breakers
end-to-end: load the fields from YAML/DB/API, pass them through the engine
and loss limiter, and fail closed when P&L data cannot be computed.

Schema changes (daily_loss_limit_percent and min_equity_threshold columns
on config_accounts) were added in 0036; this migration ensures they are
loaded and used throughout the codebase.

Changes:
- app/db.py: updated list_config_accounts and upsert_config_account to
  include daily_loss_limit_percent and min_equity_threshold
- app/routing.py: updated load_routing_config and load_routing_config_from_store
  to load these fields from YAML and database
- app/daily_loss_limiter.py: simplified to fail closed when P&L data unavailable
- tests/test_alloc09_loss_limit_fails_closed.py: new test verifying fail-closed behavior

See docs/design/REMEDIATION_PLAN.md section B-08 and F-02 for the full audit
and rationale.
"""
from __future__ import annotations


revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No schema changes; all columns were added in 0036.

    This migration documents the code changes that wire the columns into
    the routing, engine, and loss limiter layers.
    """
    pass


def downgrade() -> None:
    """No schema changes to revert."""
    pass
