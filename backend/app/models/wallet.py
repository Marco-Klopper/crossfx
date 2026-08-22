"""
Custodial RLUSD wallet.

Two supported approaches per the brief — pick one and document the choice
in docs/technical-specification.md:
  1. One XRPL Testnet account per user (xrpl_address/xrpl_encrypted_seed populated per user)
  2. One platform wallet, with balances tracked in this internal ledger table
     (xrpl_address/xrpl_encrypted_seed left null, balance is authoritative here)
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True)

    xrpl_address = Column(String, nullable=True)
    xrpl_encrypted_seed = Column(String, nullable=True)  # encrypted via app.security.encryption; never exposed via API

    rlusd_balance = Column(Numeric(18, 6), default=0, nullable=False)
    trustline_established = Column(DateTime, nullable=True)  # timestamp of successful TrustSet to RLUSD issuer

    created_at = Column(DateTime, default=datetime.utcnow)


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wallet_id = Column(UUID(as_uuid=True), ForeignKey("wallets.id"), nullable=False)
    remittance_id = Column(UUID(as_uuid=True), ForeignKey("remittances.id"), nullable=True)

    direction = Column(String, nullable=False)  # incoming | outgoing
    amount_rlusd = Column(Numeric(18, 6), nullable=False)
    xrpl_tx_hash = Column(String, nullable=True)
    status = Column(String, default="pending", nullable=False)  # pending | success | failed

    created_at = Column(DateTime, default=datetime.utcnow)
