"""
Recipient-facing custodial wallet: balances, history, cash-out.

There is no on-chain account behind these endpoints. Under pooled
custody (spec §9.1) a user's holdings are rows in the internal
multi-currency ledger, so everything here reads app.services.ledger
rather than XRPL.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.models.wallet import WalletTransaction
from app.schemas.wallet import WalletBalanceRead, WalletTransactionRead
from app.services.ledger import Ledger

router = APIRouter()


@router.get("/balance", response_model=WalletBalanceRead)
def get_balance(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    The wallet is created on first read rather than at registration, so
    a user who has never received anything sees a zero balance instead
    of a 404.
    """
    ledger = Ledger(db)
    wallet = ledger.wallet_for(current_user)
    db.commit()
    return {
        "uctusd_balance": ledger.balance(wallet, "UCTUSD"),
        "balances": ledger.balances(wallet),
    }


@router.get("/transactions", response_model=list[WalletTransactionRead])
def get_wallet_transactions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    wallet = Ledger(db).wallet_for(current_user)
    db.commit()
    return (
        db.query(WalletTransaction)
        .filter(WalletTransaction.wallet_id == wallet.id)
        .order_by(WalletTransaction.created_at.desc())
        .all()
    )


@router.post("/cash-out")
def request_cash_out(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Still Track 3's to finish, but the ledger half is in place: a
    cash-out is `ledger.debit(wallet, "UCTUSD", ...)` followed by
    `ledger.credit(wallet, <payout currency>, ...)`, and
    InsufficientFundsError already covers the "validate sufficient
    balance" step.

    What is still missing is Track 3's: the fiat payout figure
    (app.services.fee_service, cashout_fee_bps) and the cash-out
    sub-record carrying requested/approved/completed/failed — see the
    TODO at the bottom of app.models.remittance.
    """
    raise NotImplementedError
