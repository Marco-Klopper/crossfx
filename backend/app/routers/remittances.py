"""
Core remittance flow: quote -> confirm cash-in -> queue settlement -> track status.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter()


@router.post("/quote")
def create_quote(db: Session = Depends(get_db)):
    # TODO:
    #  1. Check sender KYC == approved
    #  2. Fetch/simulate USD/ZAR rate (app.services.fx_rate_service)
    #  3. Calculate fee, fx margin, net converted amount, rlusd_amount (app.services.fee_service)
    #  4. Check remittance would not exceed daily/monthly limits
    #  5. Return quotation (not yet persisted as a committed remittance, or persisted as status=quoted)
    raise NotImplementedError


@router.post("/{remittance_id}/confirm-cash-in")
def confirm_cash_in(remittance_id: str, db: Session = Depends(get_db)):
    # TODO: mark cash-in as confirmed (mock payment service / admin confirmation),
    # then publish settlement message to queue with idempotency_key = remittance_id
    raise NotImplementedError


@router.get("/{remittance_id}")
def get_remittance_status(remittance_id: str, db: Session = Depends(get_db)):
    # TODO: return current status + xrpl_tx_hash if settled
    raise NotImplementedError


@router.get("/")
def list_remittances(db: Session = Depends(get_db)):
    # TODO: transaction history for authenticated user
    raise NotImplementedError
