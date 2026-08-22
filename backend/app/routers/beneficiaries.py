from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter()


@router.post("/")
def create_beneficiary(db: Session = Depends(get_db)):
    # TODO: create Beneficiary linked to authenticated sender
    raise NotImplementedError


@router.get("/")
def list_beneficiaries(db: Session = Depends(get_db)):
    # TODO: list beneficiaries for authenticated sender
    raise NotImplementedError
