"""
Recipient-facing custodial wallet: balances, history, cash-out.

There is no on-chain account behind these endpoints. Under pooled
custody (spec §9.1) a user's holdings are rows in the internal
multi-currency ledger, so everything here reads app.services.ledger
rather than XRPL.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.remittance import CashOut
from app.models.user import User
from app.models.wallet import WalletTransaction
from app.schemas.remittance import CashOutRead, CashOutRequest
from app.schemas.wallet import WalletBalanceRead, WalletTransactionRead
from app.services import cashin_cashout_service, fee_service, fx_rate_service
from app.services.ledger import InsufficientFundsError, Ledger

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


@router.post(
    "/cash-out", response_model=CashOutRead, status_code=status.HTTP_201_CREATED
)
def request_cash_out(
    payload: CashOutRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Converts held UCTUSD into a fiat balance (spec §10).

    The UCTUSD leaves the balance now; the fiat arrives when an admin
    approves the payout (POST /admin/cash-outs/{id}/approve). Reserving
    up front is what stops three cash-outs being opened against one
    balance and all three being approved.

    Unlike the sender's endpoints this is not KYC-gated: a recipient's
    identity checks are the payout partner's in a real corridor, and
    gating here would make a recipient unable to touch money that is
    already theirs. Spec §14 records that as a deliberate prototype
    simplification.
    """
    try:
        rate = fx_rate_service.get_usd_zar_rate(db)
    except fx_rate_service.FxRateUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Exchange rate unavailable: {exc}",
        ) from exc

    try:
        cash_out = cashin_cashout_service.request_cash_out(
            db,
            current_user,
            payload.uctusd_amount,
            payload.payout_currency,
            rate,
        )
    except InsufficientFundsError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except (
        fee_service.AmountTooSmallError,
        fee_service.UnsupportedPayoutCurrencyError,
    ) as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    db.commit()
    db.refresh(cash_out)
    return cash_out


@router.get("/cash-outs", response_model=list[CashOutRead])
def list_cash_outs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The caller's own cash-outs, newest first."""
    return (
        db.query(CashOut)
        .filter(CashOut.user_id == current_user.id)
        .order_by(CashOut.requested_at.desc())
        .all()
    )


@router.get("/cash-outs/{cash_out_id}", response_model=CashOutRead)
def get_cash_out(
    cash_out_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cash_out = (
        db.query(CashOut)
        .filter(CashOut.id == cash_out_id, CashOut.user_id == current_user.id)
        .first()
    )
    if cash_out is None:
        # 404, not 403, for someone else's cash-out (spec §13).
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cash-out not found"
        )
    return cash_out
