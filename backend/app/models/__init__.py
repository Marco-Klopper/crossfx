"""
Import every model module here so:
  1. Alembic's autogenerate (migrations/env.py) sees all tables on Base.metadata —
     a model that's never imported is invisible to it.
  2. SQLAlchemy can resolve string-based relationship() targets (e.g. "KYCApplication")
     across files, which requires every mapped class to have been loaded first.
"""
from app.models.beneficiary import Beneficiary
from app.models.fx_rate import FxRate
from app.models.kyc import KYCApplication
from app.models.remittance import CashOut, CashOutStatus, Remittance
from app.models.user import User
from app.models.wallet import (
    LedgerBalance,
    PlatformWallet,
    PoolRole,
    Wallet,
    WalletTransaction,
)

__all__ = [
    "Beneficiary",
    "CashOut",
    "CashOutStatus",
    "FxRate",
    "KYCApplication",
    "LedgerBalance",
    "PlatformWallet",
    "PoolRole",
    "Remittance",
    "User",
    "Wallet",
    "WalletTransaction",
]
