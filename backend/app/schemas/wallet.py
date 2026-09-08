"""
Request/response shapes for routers/wallet.py.

Covers the brief's custodial-wallet display requirements: available RLUSD
balance, incoming transfers, outgoing/cash-out transactions, transaction
status, transaction date, and the XRP Ledger transaction hash.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class LedgerBalanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    currency: str
    amount: Decimal


class WalletBalanceRead(BaseModel):
    # `rlusd_balance` is redundant with the RLUSD entry in `balances`, and is
    # kept because it is the one figure the brief names explicitly and the one
    # the recipient UI leads with. `balances` is the full multi-currency view
    # the internal ledger actually holds (RLUSD now, fiat after a cash-out).
    rlusd_balance: Decimal
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
    created_at: datetime
