"""cash-out burn: PROCESSING state and the burn's transaction hash

A cash-out now ends with an on-chain burn (net UCTUSD paid back to the
issuer), done by the worker rather than inline in the approval request.

  - CashOutStatus gains PROCESSING, the state the worker claims a cash-out
    into before submitting the burn, so a redelivered message cannot burn
    twice.
  - cash_outs.xrpl_tx_hash records the burn's transaction hash.

Revision ID: d8e2a5c71b39
Revises: c4b81f70d2ae
Create Date: 2026-09-26 12:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd8e2a5c71b39'
down_revision: Union[str, None] = 'c4b81f70d2ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# SQLAlchemy persists the member NAME, not the value.
_STATUSES_AFTER = ('REQUESTED', 'APPROVED', 'PROCESSING', 'COMPLETED', 'FAILED')
_STATUSES_BEFORE = tuple(s for s in _STATUSES_AFTER if s != 'PROCESSING')


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == 'postgresql'


def upgrade() -> None:
    postgres = _is_postgres()
    if postgres:
        op.execute(
            "ALTER TYPE cashoutstatus ADD VALUE IF NOT EXISTS 'PROCESSING'"
        )
    with op.batch_alter_table('cash_outs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('xrpl_tx_hash', sa.String(), nullable=True))
        if not postgres:
            # On SQLite the enum is a VARCHAR sized to its longest member,
            # and PROCESSING is longer than APPROVED/COMPLETED, so widen it.
            batch_op.alter_column(
                'status',
                existing_type=sa.Enum(*_STATUSES_BEFORE, name='cashoutstatus'),
                type_=sa.Enum(*_STATUSES_AFTER, name='cashoutstatus'),
                existing_nullable=False,
            )


def downgrade() -> None:
    # A cash-out mid-burn goes back to APPROVED, which the old code treats
    # as a completed state; the old type cannot hold PROCESSING.
    op.execute(
        "UPDATE cash_outs SET status = 'APPROVED' WHERE status = 'PROCESSING'"
    )
    with op.batch_alter_table('cash_outs', schema=None) as batch_op:
        batch_op.drop_column('xrpl_tx_hash')
        if not _is_postgres():
            batch_op.alter_column(
                'status',
                existing_type=sa.Enum(*_STATUSES_AFTER, name='cashoutstatus'),
                type_=sa.Enum(*_STATUSES_BEFORE, name='cashoutstatus'),
                existing_nullable=False,
            )
    if _is_postgres():
        old_values = "', '".join(_STATUSES_BEFORE)
        op.execute("ALTER TYPE cashoutstatus RENAME TO cashoutstatus_old")
        op.execute(f"CREATE TYPE cashoutstatus AS ENUM ('{old_values}')")
        op.execute(
            "ALTER TABLE cash_outs ALTER COLUMN status TYPE cashoutstatus "
            "USING status::text::cashoutstatus"
        )
        op.execute("DROP TYPE cashoutstatus_old")
