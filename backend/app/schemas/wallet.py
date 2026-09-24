"""
Request/response shapes for routers/wallet.py.

Covers the brief's custodial-wallet display requirements: available
settlement-token balance, incoming transfers, outgoing and cash-out
transactions, transaction status, transaction date, and the XRP Ledger
transaction hash.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.schemas.common import UtcDatetime


class LedgerBalanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    currency: str
    amount: Decimal


class WalletBalanceRead(BaseModel):
    # Redundant with the UCTUSD entry in `balances`, and kept because
    # the available settlement-token balance is the figure the brief
    # names explicitly and the one the recipient UI leads with.
    # `balances` is the full multi-currency view the ledger holds:
    # UCTUSD now, fiat after a cash-out.
    uctusd_balance: Decimal
    balances: list[LedgerBalanceRead]


class WalletTransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    remittance_id: uuid.UUID | None
    direction: str
    currency: str
    amount: Decimal
    xrpl_tx_hash: str | None
    status: str
    failure_reason: str | None
    created_at: UtcDatetime
