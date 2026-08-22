"""
Admin-only endpoints: KYC review, mock cash-in confirmation, cash-out approval.

All routes are gated behind app.dependencies.require_admin.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.models.kyc import ApplicationStatus, KYCApplication
from app.models.user import KYCStatus, User
from app.schemas.kyc import AdminKYCApplicationRead, KYCApplicationRead, KYCReviewRequest

router = APIRouter()


def _get_application(db: Session, application_id: uuid.UUID) -> KYCApplication:
    application = db.get(KYCApplication, application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="KYC application not found")
    return application


def _require_pending(application: KYCApplication) -> None:
    if application.status != ApplicationStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Application is already {application.status.value}, not pending",
        )


@router.get("/kyc/applications", response_model=list[AdminKYCApplicationRead])
def list_kyc_applications(
    status_filter: ApplicationStatus | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    query = db.query(KYCApplication)
    if status_filter is not None:
        query = query.filter(KYCApplication.status == status_filter)
    return query.order_by(KYCApplication.submitted_at.asc()).all()


@router.post("/kyc/{application_id}/approve", response_model=KYCApplicationRead)
def approve_kyc(
    application_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    application = _get_application(db, application_id)
    _require_pending(application)

    application.status = ApplicationStatus.APPROVED
    application.reviewed_by_admin_id = admin.id
    application.reviewed_at = datetime.now(timezone.utc)

    user = db.get(User, application.user_id)
    user.kyc_status = KYCStatus.APPROVED

    db.commit()
    db.refresh(application)
    return application


@router.post("/kyc/{application_id}/reject", response_model=KYCApplicationRead)
def reject_kyc(
    application_id: uuid.UUID,
    payload: KYCReviewRequest = KYCReviewRequest(),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    application = _get_application(db, application_id)
    _require_pending(application)

    application.status = ApplicationStatus.REJECTED
    application.reviewed_by_admin_id = admin.id
    application.reviewed_at = datetime.now(timezone.utc)
    application.rejection_reason = payload.reason

    user = db.get(User, application.user_id)
    user.kyc_status = KYCStatus.REJECTED

    db.commit()
    db.refresh(application)
    return application


@router.post("/remittances/{remittance_id}/confirm-payment")
def confirm_zar_payment(
    remittance_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    """
    Mock payment-service confirmation that ZAR cash-in was received.

    Gated here (Track 1 owns admin.py) but the body is Track 3's to implement —
    it needs app.models.remittance and app.services.cashin_cashout_service, which
    Track 3 owns. Contract: mark the Remittance CASH_IN_CONFIRMED, then publish
    the settlement message to the queue (see routers/remittances.py's own TODO).
    """
    raise NotImplementedError


@router.post("/cash-outs/{cash_out_id}/approve")
def approve_cash_out(
    cash_out_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    """
    Gated here (Track 1 owns admin.py) but the body is Track 3's to implement —
    the cash-out sub-record it needs doesn't exist yet (see the TODO at the
    bottom of app.models.remittance). Contract: validate then progress the
    cash-out status toward completed per app.services.fee_service's payout calc.
    """
    raise NotImplementedError
