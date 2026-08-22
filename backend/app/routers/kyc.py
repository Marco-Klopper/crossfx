"""
Mock KYC submission + admin approve/reject.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter()


@router.post("/apply")
def submit_kyc(db: Session = Depends(get_db)):
    # TODO: collect full_name, dob, nationality, id_number, address, mobile,
    # email, source_of_funds -> create KYCApplication with status=pending
    raise NotImplementedError


@router.get("/status")
def get_kyc_status(db: Session = Depends(get_db)):
    # TODO: return current user's KYC status
    raise NotImplementedError
