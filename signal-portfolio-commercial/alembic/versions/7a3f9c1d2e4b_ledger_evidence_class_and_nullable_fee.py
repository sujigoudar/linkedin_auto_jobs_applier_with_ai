"""ledger_entries gets evidence_class and fee becomes nullable

Revision ID: 7a3f9c1d2e4b
Revises: 29c061b04be5
Create Date: 2026-09-28 07:00:00.000000

Signal Platform Integration Correction Pack's own INTEGRATION_DECISION.md
S7: "Add explicit ... evidence_class: synthetic fixture, internal paper,
external demo, hypothetical backtest, observed owner live, platform-
reported model or observed follower live" and "Importing a zero default
is not proof of a verified zero fee."

Purely additive: `evidence_class` is added NULLABLE (a pre-existing row
predates this column and genuinely has no evidence class recorded --
NULL means exactly that, never "no evidence class applies"), and `fee`'s
NOT NULL constraint is dropped so "fee not yet known" can be a real NULL
distinct from a verified $0.00, rather than every unknown fee silently
defaulting to zero. No existing row's values change.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7a3f9c1d2e4b'
down_revision: str | None = '29c061b04be5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# SQLAlchemy's Enum(native_enum=False) stores a Python Enum member's own
# NAME (e.g. "SYNTHETIC_FIXTURE"), never its `.value` (e.g.
# "synthetic_fixture") -- matching this codebase's own existing
# convention for every other enum column (see Book/Side/ReconciliationState
# in f1cbfcc4819c's own initial-schema migration, all stored by name).
_EVIDENCE_CLASS_VALUES = (
    'SYNTHETIC_FIXTURE', 'INTERNAL_PAPER', 'EXTERNAL_DEMO', 'HYPOTHETICAL_BACKTEST',
    'OBSERVED_OWNER_LIVE', 'PLATFORM_REPORTED_MODEL', 'OBSERVED_FOLLOWER_LIVE',
)


def upgrade() -> None:
    op.add_column(
        'ledger_entries',
        sa.Column(
            'evidence_class',
            sa.Enum(*_EVIDENCE_CLASS_VALUES, name='evidenceclass', native_enum=False),
            nullable=True,
        ),
    )
    op.alter_column('ledger_entries', 'fee', existing_type=sa.Numeric(precision=28, scale=10), nullable=True)


def downgrade() -> None:
    op.drop_column('ledger_entries', 'evidence_class')
    op.alter_column('ledger_entries', 'fee', existing_type=sa.Numeric(precision=28, scale=10), nullable=False)
