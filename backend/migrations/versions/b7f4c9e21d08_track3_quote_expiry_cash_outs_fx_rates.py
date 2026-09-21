"""track3 quote expiry, cash-outs and pinned fx rates

Everything Track 3's flow needs that the schema did not already carry:

  - remittances.quote_expires_at / cash_in_confirmed_at. A quote is now a
    persisted QUOTED row so that it holds limit headroom (spec §6); the
    expiry is what gives that headroom back if the sender never pays in,
    and the confirmation timestamp is the cash-in half of the audit trail
    the settled_at column already covers for settlement.
  - cash_outs, the sub-record spec §10 needs: one row per payout request,
    carrying its own status, the fee, the fiat figure, and the two ledger
    entries behind it. It hangs off users rather than remittances because
    by the time value is cashed out it has been pooled into one balance —
    asking which remittance a given rand came from is a question the
    ledger cannot answer and does not need to.
  - fx_rates, an append-only table of manually pinned rates, for
    EXCHANGE_RATE_SOURCE=table (spec §5).

Revision ID: b7f4c9e21d08
Revises: a3c81f4e57d2
Create Date: 2026-09-14 19:05:11.402119

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7f4c9e21d08'
down_revision: Union[str, None] = 'a3c81f4e57d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

cash_out_status_enum = sa.Enum(
    'REQUESTED', 'APPROVED', 'COMPLETED', 'FAILED', name='cashoutstatus'
)


def upgrade() -> None:
    bind = op.get_bind()
    # Postgres needs the type to exist before a column references it, and
    # will not drop it with the table — see downgrade().
    cash_out_status_enum.create(bind, checkfirst=True)

    op.create_table(
        'cash_outs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('uctusd_amount', sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column('cash_out_fee_uctusd', sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column('net_uctusd', sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column('payout_currency', sa.String(), nullable=False),
        sa.Column('payout_amount', sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column('fx_rate_used', sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column('status', cash_out_status_enum, nullable=False),
        sa.Column('failure_reason', sa.String(), nullable=True),
        sa.Column('debit_transaction_id', sa.Uuid(), nullable=True),
        sa.Column('credit_transaction_id', sa.Uuid(), nullable=True),
        sa.Column('requested_at', sa.DateTime(), nullable=False),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['debit_transaction_id'], ['wallet_transactions.id'], ),
        sa.ForeignKeyConstraint(['credit_transaction_id'], ['wallet_transactions.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'fx_rates',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('currency_pair', sa.String(), nullable=False),
        sa.Column('rate', sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column('effective_from', sa.DateTime(), nullable=False),
        sa.Column('source', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_fx_rates_currency_pair'), 'fx_rates', ['currency_pair'], unique=False
    )

    # Both nullable, so no backfill is needed: rows written before this
    # migration were quotes that never had an expiry and settlements whose
    # cash-in predates the column. Leaving them NULL is truthful — an
    # invented timestamp would not be.
    with op.batch_alter_table('remittances', schema=None) as batch_op:
        batch_op.add_column(sa.Column('quote_expires_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('cash_in_confirmed_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('remittances', schema=None) as batch_op:
        batch_op.drop_column('cash_in_confirmed_at')
        batch_op.drop_column('quote_expires_at')

    op.drop_index(op.f('ix_fx_rates_currency_pair'), table_name='fx_rates')
    op.drop_table('fx_rates')
    op.drop_table('cash_outs')
    # DROP TABLE does not take the enum type with it on Postgres.
    cash_out_status_enum.drop(op.get_bind(), checkfirst=True)
