"""
Beneficiary add/list/manage, scoped to the authenticated sender.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.beneficiary import Beneficiary
from app.models.user import User
from app.schemas.beneficiary import BeneficiaryCreate, BeneficiaryRead

router = APIRouter()


def _get_owned_beneficiary(db: Session, beneficiary_id: uuid.UUID, sender_id: uuid.UUID) -> Beneficiary:
    beneficiary = (
        db.query(Beneficiary)
        .filter(Beneficiary.id == beneficiary_id, Beneficiary.sender_id == sender_id)
        .first()
    )
    if beneficiary is None:
        # 404, not 403: a 403 would confirm the id exists but belongs to someone
        # else, leaking information about other users' data.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Beneficiary not found")
    return beneficiary


@router.post("/", response_model=BeneficiaryRead, status_code=status.HTTP_201_CREATED)
def create_beneficiary(
    payload: BeneficiaryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    beneficiary = Beneficiary(sender_id=current_user.id, **payload.model_dump())
    db.add(beneficiary)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A beneficiary with this contact already exists",
        )
    db.refresh(beneficiary)
    return beneficiary


@router.get("/", response_model=list[BeneficiaryRead])
def list_beneficiaries(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    return db.query(Beneficiary).filter(Beneficiary.sender_id == current_user.id).all()


@router.get("/{beneficiary_id}", response_model=BeneficiaryRead)
def get_beneficiary(
    beneficiary_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_owned_beneficiary(db, beneficiary_id, current_user.id)


@router.delete("/{beneficiary_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_beneficiary(
    beneficiary_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    beneficiary = _get_owned_beneficiary(db, beneficiary_id, current_user.id)
    db.delete(beneficiary)
    db.commit()
