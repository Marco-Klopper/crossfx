"""audit: refund state, cash-out idempotency, admin trail and hot-path indexes

Four related gaps the audit turned up, all in one revision because they
are all schema:

  - RemittanceStatus gains REFUNDED. FAILED was an absorbing state: the
    sender's rand had been taken (a settlement can only fail after
    cash-in is confirmed) and no route through the API returned it or
    retried the transfer. REFUNDED is the terminal state that says the
    money is genuinely back, and it is what stops the row consuming the
    sender's limit headroom.
  - cash_outs.idempotency_key. POST /wallet/cash-out had no dedupe of any
    kind, so a double-clicked button debited a balance twice and opened
    two payouts. NULLs are distinct under a unique index in both SQLite
    and Postgres, so requests that send no Idempotency-Key header behave
    exactly as before.
  - cash_outs.reviewed_by_admin_id. KYC has recorded its reviewer since
    Track 1; releasing money recorded nobody.
  - Three indexes on queries that had none. limits_service.sender_totals
    filters remittances on (sender_id, created_at) on every single quote,
    which is the first thing that would degrade under the load tests.

Revision ID: c4b81f70d2ae
Revises: b7f4c9e21d08
Create Date: 2026-09-24 14:07:32.118094

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4b81f70d2ae'
down_revision: Union[str, None] = 'b7f4c9e21d08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The full set after this revision, for the Postgres type rebuild in
# downgrade(). SQLAlchemy persists the member NAME, not the value.
_STATUSES_AFTER = (
    'QUOTED',
    'CASH_IN_CONFIRMED',
    'QUEUED',
    'SETTLING',
    'SETTLED',
    'FAILED',
    'REFUNDED',
)
_STATUSES_BEFORE = _STATUSES_AFTER[:-1]


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == 'postgresql'


def upgrade() -> None:
    # 1. The new enum member.
    #
    # On SQLite there is nothing to do: sa.Enum defaults to
    # create_constraint=False, so the column is a plain VARCHAR with no
    # CHECK to widen (verified against the live database). On Postgres
    # the standalone type does need altering. ADD VALUE is transactional
    # from PG 12 onwards, which is every version this project targets.
    if _is_postgres():
        op.execute(
            "ALTER TYPE remittancestatus ADD VALUE IF NOT EXISTS 'REFUNDED'"
        )

    # 2. Cash-out idempotency and the admin trail.
    #
    # batch_alter_table because reviewed_by_admin_id carries a foreign
    # key, and SQLite cannot add one to an existing table without
    # rebuilding it.
    with op.batch_alter_table('cash_outs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('idempotency_key', sa.String(), nullable=True)
        )
        batch_op.add_column(
            sa.Column('reviewed_by_admin_id', sa.Uuid(), nullable=True)
        )
        batch_op.create_foreign_key(
            'fk_cash_outs_reviewed_by_admin_id_users',
            'users',
            ['reviewed_by_admin_id'],
            ['id'],
        )

    op.create_index(
        'uq_cash_outs_idempotency_key',
        'cash_outs',
        ['idempotency_key'],
        unique=True,
    )

    # 3. The hot-path indexes.
    op.create_index('ix_cash_outs_user_id', 'cash_outs', ['user_id'])
    op.create_index(
        'ix_wallet_transactions_wallet_id', 'wallet_transactions', ['wallet_id']
    )
    op.create_index(
        'ix_remittances_sender_id_created_at',
        'remittances',
        ['sender_id', 'created_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_remittances_sender_id_created_at', table_name='remittances')
    op.drop_index(
        'ix_wallet_transactions_wallet_id', table_name='wallet_transactions'
    )
    op.drop_index('ix_cash_outs_user_id', table_name='cash_outs')
    op.drop_index('uq_cash_outs_idempotency_key', table_name='cash_outs')

    with op.batch_alter_table('cash_outs', schema=None) as batch_op:
        batch_op.drop_constraint(
            'fk_cash_outs_reviewed_by_admin_id_users', type_='foreignkey'
        )
        batch_op.drop_column('reviewed_by_admin_id')
        batch_op.drop_column('idempotency_key')

    # Any row that reached the new state has to go back to one the old
    # enum knows about before the type is narrowed. FAILED is where it
    # came from; the refund's ledger entry survives either way, so the
    # audit trail is not what is being discarded here.
    op.execute(
        "UPDATE remittances SET status = 'FAILED' WHERE status = 'REFUNDED'"
    )

    # Postgres cannot drop a value from an enum type, so the type is
    # rebuilt: rename the old one aside, create the narrowed one, move
    # the column across, drop the old. SQLite needs none of this — the
    # column is a plain VARCHAR and the UPDATE above is the whole job.
    if _is_postgres():
        old_values = "', '".join(_STATUSES_BEFORE)
        op.execute("ALTER TYPE remittancestatus RENAME TO remittancestatus_old")
        op.execute(f"CREATE TYPE remittancestatus AS ENUM ('{old_values}')")
        op.execute(
            "ALTER TABLE remittances ALTER COLUMN status TYPE remittancestatus "
            "USING status::text::remittancestatus"
        )
        op.execute("DROP TYPE remittancestatus_old")
