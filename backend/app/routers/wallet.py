"""
Recipient-facing wallet: balance, incoming/outgoing history, cash-out requests.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter()


@router.get("/balance")
def get_balance(db: Session = Depends(get_db)):
    # TODO: return RLUSD balance for authenticated recipient
    raise NotImplementedError


@router.get("/transactions")
def get_wallet_transactions(db: Session = Depends(get_db)):
    # TODO: incoming/outgoing tx list with status, date, xrpl tx hash
    raise NotImplementedError


@router.post("/cash-out")
def request_cash_out(db: Session = Depends(get_db)):
    # TODO:
    #  1. Validate sufficient RLUSD balance
    #  2. Calculate fiat payout using current rate minus cash-out fee (app.services.fee_service)
    #  3. Create cash-out record with status=requested
    #  4. (Simulated) progress through approved -> completed, or failed
    raise NotImplementedError
