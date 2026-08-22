"""
Admin-only endpoints: KYC review, mock cash-in confirmation, cash-out approval.
TODO: gate all endpoints behind an admin-role check.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter()


@router.post("/kyc/{application_id}/approve")
def approve_kyc(application_id: str, db: Session = Depends(get_db)):
    raise NotImplementedError


@router.post("/kyc/{application_id}/reject")
def reject_kyc(application_id: str, db: Session = Depends(get_db)):
    raise NotImplementedError


@router.post("/remittances/{remittance_id}/confirm-payment")
def confirm_zar_payment(remittance_id: str, db: Session = Depends(get_db)):
    # Mock payment-service confirmation that ZAR cash-in was received.
    raise NotImplementedError


@router.post("/cash-outs/{cash_out_id}/approve")
def approve_cash_out(cash_out_id: str, db: Session = Depends(get_db)):
    raise NotImplementedError
