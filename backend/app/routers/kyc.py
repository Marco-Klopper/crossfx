"""
Mock KYC submission + status check. Review/approval lives in routers/admin.py
(applicants can't approve their own applications).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.kyc import ApplicationStatus, KYCApplication
from app.models.user import KYCStatus, User
from app.schemas.kyc import KYCApplicationCreate, KYCApplicationRead, KYCStatusResponse

router = APIRouter()


@router.post("/apply", response_model=KYCApplicationRead, status_code=status.HTTP_201_CREATED)
def submit_kyc(
    payload: KYCApplicationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = (
        db.query(KYCApplication)
        .filter(
            KYCApplication.user_id == current_user.id,
            KYCApplication.status.in_([ApplicationStatus.PENDING, ApplicationStatus.APPROVED]),
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A pending or approved KYC application already exists for this user",
        )

    # The application is what flips *this* account to APPROVED, but
    # every identity field in it was free text: nothing checked that the
    # email, name or ID number described the person submitting it. That
    # let someone KYC their own account using another person's
    # documents. Binding the email is the cheapest check that makes the
    # application about its own account.
    if payload.email.strip().lower() != current_user.email.strip().lower():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "The email on a KYC application must be the address the "
                "account is registered under"
            ),
        )

    application = KYCApplication(user_id=current_user.id, **payload.model_dump())
    db.add(application)
    current_user.kyc_status = KYCStatus.PENDING
    db.commit()
    db.refresh(application)
    return application


@router.get("/status", response_model=KYCStatusResponse)
def get_kyc_status(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    latest = (
        db.query(KYCApplication)
        .filter(KYCApplication.user_id == current_user.id)
        .order_by(KYCApplication.submitted_at.desc())
        .first()
    )
    return KYCStatusResponse(kyc_status=current_user.kyc_status, latest_application=latest)
