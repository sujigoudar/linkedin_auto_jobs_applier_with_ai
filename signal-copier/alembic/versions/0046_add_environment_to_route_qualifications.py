"""WP-33: qualification keyed by venue environment.

Revision ID: 0046
Revises: 0037
Create Date: 2026-10-02 00:00:00.000000

A route is release-approved only for the environment it was qualified in
(paper/live/sandbox). The environment is resolved at call time from the broker
(e.g., Alpaca's base URL, ccxt's sandbox flag) and becomes part of the route
key alongside (adapter_type, route_key, asset_class, product_type).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("route_qualifications", schema=None) as batch_op:
        batch_op.add_column(sa.Column("environment", sa.Text, nullable=False, server_default="unknown"))
        # Drop the old index and recreate with environment
        batch_op.drop_index("idx_route_qualifications_route")
    op.create_index(
        "idx_route_qualifications_route",
        "route_qualifications",
        ["adapter_type", "route_key", "asset_class", "product_type", "environment"],
    )


def downgrade() -> None:
    op.drop_index("idx_route_qualifications_route", table_name="route_qualifications")
    op.create_index(
        "idx_route_qualifications_route",
        "route_qualifications",
        ["adapter_type", "route_key", "asset_class", "product_type"],
    )
    with op.batch_alter_table("route_qualifications", schema=None) as batch_op:
        batch_op.drop_column("environment")
