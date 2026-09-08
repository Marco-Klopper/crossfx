"""rename rlusd to uctusd

The class settles in UCTUSD, a lecturer-issued Testnet IOU, not Ripple's
RLUSD (course announcement, 2026-09-08). This aligns the schema with the
asset actually used:

  - remittances.rlusd_amount -> uctusd_amount
  - existing ledger and transaction rows relabelled RLUSD -> UCTUSD
  - beneficiaries electing an "RLUSD" payout relabelled

The two earlier migrations are left untouched on purpose: they describe
the schema as it was when they ran, and rewriting applied history would
desynchronise any database already at those revisions.

Revision ID: a3c81f4e57d2
Revises: f20b2cb74a0f
Create Date: 2026-09-09 00:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3c81f4e57d2'
down_revision: Union[str, None] = 'f20b2cb74a0f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Currency labels live in plain String columns rather than an enum, so
# relabelling is a data update rather than a type change.
_RELABEL = (
    ("ledger_balances", "currency"),
    ("wallet_transactions", "currency"),
    ("beneficiaries", "preferred_payout_currency"),
)


def upgrade() -> None:
    with op.batch_alter_table('remittances', schema=None) as batch_op:
        batch_op.alter_column(
            'rlusd_amount',
            new_column_name='uctusd_amount',
            existing_type=sa.Numeric(precision=18, scale=6),
            existing_nullable=False,
        )

    for table, column in _RELABEL:
        op.execute(
            f"UPDATE {table} SET {column} = 'UCTUSD' WHERE {column} = 'RLUSD'"
        )


def downgrade() -> None:
    for table, column in _RELABEL:
        op.execute(
            f"UPDATE {table} SET {column} = 'RLUSD' WHERE {column} = 'UCTUSD'"
        )

    with op.batch_alter_table('remittances', schema=None) as batch_op:
        batch_op.alter_column(
            'uctusd_amount',
            new_column_name='rlusd_amount',
            existing_type=sa.Numeric(precision=18, scale=6),
            existing_nullable=False,
        )
