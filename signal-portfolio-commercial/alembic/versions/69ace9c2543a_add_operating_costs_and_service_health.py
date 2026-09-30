"""add operating_costs and service_health_samples tables

Revision ID: 69ace9c2543a
Revises: e9bf962d90cd
Create Date: 2026-09-30 00:00:00.000000

Track 11 "Cost of subscribed services / infrastructure uptime and
latency metrics" -- see app/models/operating_cost.py and
app/models/service_health_sample.py for what each table is and is not.

`operating_costs` carries a `tenant_id` and is tenant-scoped -- applies
row-level security in the SAME transaction this revision's own
`create_table` runs in, pinned to only the table THIS revision adds
(per `e9a3c6f1d208`'s own precedent, itself citing `04c418cbb547`'s
lesson: never the live, ever-growing `app.db._TENANT_SCOPED_TABLES`).

`service_health_samples` carries NO `tenant_id` -- it is the platform's
own infrastructure record (this deployment's own two services), not
per-tenant customer data, per app/models/service_health_sample.py's own
docstring. It deliberately gets no row-level-security policy.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.db import _apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = '69ace9c2543a'
down_revision: str | None = 'e9bf962d90cd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'operating_costs',
        sa.Column('cost_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column(
            'category',
            sa.Enum(
                'SUBSCRIPTION', 'INFRASTRUCTURE', 'API_USAGE', 'LLM_USAGE', 'DATA_FEED', 'OTHER',
                name='operatingcostcategory', native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column('vendor', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(), nullable=False),
        sa.Column(
            'cadence',
            sa.Enum('ONE_TIME', 'MONTHLY', 'QUARTERLY', 'ANNUAL', name='operatingcostcadence', native_enum=False),
            nullable=False,
        ),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('cost_center', sa.String(), nullable=True),
        sa.Column(
            'entry_source',
            sa.Enum('MANUAL', 'CSV_IMPORT', name='operatingcostsource', native_enum=False),
            nullable=False,
        ),
        sa.Column('is_usage_estimate', sa.Boolean(), nullable=False),
        sa.Column('created_by_user_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('cost_id'),
    )
    op.create_index(op.f('ix_operating_costs_tenant_id'), 'operating_costs', ['tenant_id'], unique=False)

    op.create_table(
        'service_health_samples',
        sa.Column('sample_id', sa.String(), nullable=False),
        sa.Column('service_name', sa.String(), nullable=False),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('latency_ms', sa.Float(), nullable=True),
        sa.Column('failure_reason', sa.String(), nullable=True),
        sa.Column('sampled_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('sample_id'),
    )
    op.create_index(op.f('ix_service_health_samples_service_name'), 'service_health_samples', ['service_name'], unique=False)
    op.create_index(op.f('ix_service_health_samples_sampled_at'), 'service_health_samples', ['sampled_at'], unique=False)

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: `app.db._TENANT_SCOPED_TABLES`. `service_health_samples` is
    #: deliberately excluded (see this file's own docstring).
    connection = op.get_bind()
    _apply_row_level_security(connection, ("operating_costs",))


def downgrade() -> None:
    op.drop_index(op.f('ix_service_health_samples_sampled_at'), table_name='service_health_samples')
    op.drop_index(op.f('ix_service_health_samples_service_name'), table_name='service_health_samples')
    op.drop_table('service_health_samples')
    op.drop_index(op.f('ix_operating_costs_tenant_id'), table_name='operating_costs')
    op.drop_table('operating_costs')
