"""
Pooled-custody wallet model.

CrossFX runs the second of the two custodial approaches the project
brief's custodial-wallet section permits: *one platform wallet with
customer balances maintained in an internal database ledger*, rather
than a separate XRPL Testnet account per user. See
docs/technical-specification.md §9.1 for the justification.

The consequence for this file: a user has NO on-chain identity. A
user's holdings are `LedgerBalance` rows — one per (wallet, currency) —
and the only XRPL accounts in the system are the two pooled
`PlatformWallet` rows.

  Wallet             one per user; a container, no balance, no address
  LedgerBalance      authoritative multi-currency holdings
  WalletTransaction  immutable audit trail behind those balances
  PlatformWallet     the two pooled accounts, seeds encrypted at rest
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PoolRole(str, enum.Enum):
    """
    The two pooled XRPL accounts a remittance settles between. Value
    moves SEND_POOL -> PAYOUT_POOL on-chain, once per remittance
    (spec §9.3).
    """

    # ZA-side corridor pool: holds liquidity, signs Payments.
    SEND_POOL = "send_pool"
    # Payout-side pool: receives, and backs recipients' claims.
    PAYOUT_POOL = "payout_pool"


class Wallet(Base):
    """
    A user's custodial wallet. Deliberately empty of balance and
    address columns: in the pooled model this row exists only to own
    LedgerBalance and WalletTransaction rows, so "add a currency" never
    means "alter this table".
    """

    __tablename__ = "wallets"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(
        Uuid, ForeignKey("users.id"), nullable=False, unique=True
    )

    created_at = Column(DateTime, default=_utcnow)


class LedgerBalance(Base):
    """
    What a user actually owns, per currency. This is the authoritative
    record — there is no per-user on-chain balance to reconcile
    against, only the pooled account totals (spec §9.1).

    One row per (wallet, currency): the constraint is what makes a
    concurrent double-credit impossible to represent, rather than
    merely unlikely.
    """

    __tablename__ = "ledger_balances"
    __table_args__ = (
        UniqueConstraint(
            "wallet_id", "currency", name="uq_ledger_balance_wallet_currency"
        ),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    wallet_id = Column(Uuid, ForeignKey("wallets.id"), nullable=False)

    # UCTUSD | USD | ZAR | ... (settings.supported_currencies)
    currency = Column(String, nullable=False)
    amount = Column(Numeric(18, 6), default=0, nullable=False)

    updated_at = Column(DateTime, default=_utcnow)


class WalletTransaction(Base):
    """
    Immutable ledger entry. Every LedgerBalance movement writes exactly
    one of these, so a balance is always reconstructible from history.

    Satisfies the brief's custodial-wallet display requirements:
    incoming transfers, outgoing/cash-out transactions, status, date,
    and the XRP Ledger transaction hash.
    """

    __tablename__ = "wallet_transactions"
    __table_args__ = (
        # The double-credit backstop. If the worker's status
        # compare-and-swap (spec §9.5) were ever bypassed, this makes a
        # second *successful* incoming entry for the same remittance a
        # constraint violation rather than free money. `status` is part
        # of the key so that a recorded failure followed by a manual
        # retry is still representable — only two successes collide.
        # NULL remittance_id repeats freely (SQL treats NULLs as
        # distinct), so entries such as cash-outs are unaffected.
        UniqueConstraint(
            "remittance_id",
            "direction",
            "status",
            name="uq_wallet_tx_remittance_direction_status",
        ),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    wallet_id = Column(Uuid, ForeignKey("wallets.id"), nullable=False)
    remittance_id = Column(
        Uuid, ForeignKey("remittances.id"), nullable=True
    )

    direction = Column(String, nullable=False)  # incoming | outgoing
    currency = Column(String, nullable=False)
    amount = Column(Numeric(18, 6), nullable=False)

    xrpl_tx_hash = Column(String, nullable=True)
    # pending | success | failed
    status = Column(String, default="pending", nullable=False)
    # XRPL result code or ledger error. Never a seed.
    failure_reason = Column(String, nullable=True)

    created_at = Column(DateTime, default=_utcnow)


class PlatformWallet(Base):
    """
    A pooled XRPL Testnet account. Exactly two rows exist, one per
    PoolRole, created by scripts/init_platform_wallets.py.

    `xrpl_encrypted_seed` is Fernet ciphertext produced by
    app.security.encryption. Per the brief's private-key requirements
    the encryption key lives in PRIVATE_KEY_ENCRYPTION_KEY (.env, or a
    secrets manager) and never in this database. The plaintext seed is
    never returned by any API route and never logged.
    """

    __tablename__ = "platform_wallets"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    role = Column(Enum(PoolRole), nullable=False, unique=True)

    xrpl_address = Column(String, nullable=False)
    xrpl_encrypted_seed = Column(String, nullable=False)
    # Timestamp of a successful TrustSet to the token issuer.
    trustline_established = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
