"""track2 pooled custody and multi-currency ledger

Moves Track 2 onto the pooled-custody model in spec §9.1:

  - adds platform_wallets, the two pooled XRPL corridor accounts (encrypted seeds)
  - adds ledger_balances, the internal multi-currency ledger that is now the
    authoritative record of what each user owns
  - generalises wallet_transactions from RLUSD-only to any supported currency,
    and adds the dedup constraint that backs the brief's no-double-credit rule
  - strips wallets down to a container: no balance, no XRPL address, no seed,
    because under pooled custody a user has no on-chain identity

Revision ID: f20b2cb74a0f
Revises: d546f21aa491
Create Date: 2026-09-08 22:25:50.875446

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f20b2cb74a0f'
down_revision: Union[str, None] = 'd546f21aa491'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

pool_role_enum = sa.Enum('SEND_POOL', 'PAYOUT_POOL', name='poolrole')


def upgrade() -> None:
    bind = op.get_bind()
    pool_role_enum.create(bind, checkfirst=True)

    op.create_table(
        'platform_wallets',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('role', pool_role_enum, nullable=False),
        sa.Column('xrpl_address', sa.String(), nullable=False),
        sa.Column('xrpl_encrypted_seed', sa.String(), nullable=False),
        sa.Column('trustline_established', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('role'),
    )
    op.create_table(
        'ledger_balances',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('wallet_id', sa.Uuid(), nullable=False),
        sa.Column('currency', sa.String(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['wallet_id'], ['wallets.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('wallet_id', 'currency', name='uq_ledger_balance_wallet_currency'),
    )

    # wallet_transactions: amount_rlusd -> (currency, amount). Added nullable so
    # the backfill can run, then tightened — adding a NOT NULL column outright
    # would fail on any database that already has rows in this table.
    with op.batch_alter_table('wallet_transactions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('currency', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('amount', sa.Numeric(precision=18, scale=6), nullable=True))
        batch_op.add_column(sa.Column('failure_reason', sa.String(), nullable=True))

    op.execute("UPDATE wallet_transactions SET currency = 'RLUSD', amount = amount_rlusd")

    with op.batch_alter_table('wallet_transactions', schema=None) as batch_op:
        batch_op.alter_column('currency', existing_type=sa.String(), nullable=False)
        batch_op.alter_column(
            'amount', existing_type=sa.Numeric(precision=18, scale=6), nullable=False
        )
        batch_op.create_unique_constraint(
            'uq_wallet_tx_remittance_direction_status',
            ['remittance_id', 'direction', 'status'],
        )
        batch_op.drop_column('amount_rlusd')

    # wallets loses its balance and its on-chain identity. No backfill of
    # rlusd_balance into ledger_balances: the pooled model was adopted before
    # any balance was ever written (the only writer was the pre-ledger
    # /wallet/balance route). A deployment that did hold balances would need an
    # INSERT ... SELECT into ledger_balances here before these drops.
    with op.batch_alter_table('wallets', schema=None) as batch_op:
        batch_op.drop_column('rlusd_balance')
        batch_op.drop_column('xrpl_address')
        batch_op.drop_column('xrpl_encrypted_seed')
        batch_op.drop_column('trustline_established')


def downgrade() -> None:
    # server_default on the re-added NOT NULL columns so the downgrade works on
    # a table with rows; dropped again immediately, matching the original schema.
    with op.batch_alter_table('wallets', schema=None) as batch_op:
        batch_op.add_column(sa.Column('xrpl_encrypted_seed', sa.VARCHAR(), nullable=True))
        batch_op.add_column(sa.Column('trustline_established', sa.DATETIME(), nullable=True))
        batch_op.add_column(sa.Column('xrpl_address', sa.VARCHAR(), nullable=True))
        batch_op.add_column(
            sa.Column(
                'rlusd_balance',
                sa.NUMERIC(precision=18, scale=6),
                nullable=False,
                server_default='0',
            )
        )
    with op.batch_alter_table('wallets', schema=None) as batch_op:
        batch_op.alter_column('rlusd_balance', server_default=None)

    with op.batch_alter_table('wallet_transactions', schema=None) as batch_op:
        batch_op.drop_constraint('uq_wallet_tx_remittance_direction_status', type_='unique')
        batch_op.add_column(
            sa.Column(
                'amount_rlusd',
                sa.NUMERIC(precision=18, scale=6),
                nullable=False,
                server_default='0',
            )
        )

    op.execute("UPDATE wallet_transactions SET amount_rlusd = amount")

    with op.batch_alter_table('wallet_transactions', schema=None) as batch_op:
        batch_op.alter_column('amount_rlusd', server_default=None)
        batch_op.drop_column('failure_reason')
        batch_op.drop_column('amount')
        batch_op.drop_column('currency')

    op.drop_table('ledger_balances')
    op.drop_table('platform_wallets')
    pool_role_enum.drop(op.get_bind(), checkfirst=True)
