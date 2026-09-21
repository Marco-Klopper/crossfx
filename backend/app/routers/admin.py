"""
Admin-only endpoints: KYC review, mock cash-in confirmation, cash-out approval.

All routes are gated behind app.dependencies.require_admin.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.models.kyc import ApplicationStatus, KYCApplication
from app.models.remittance import CashOut, CashOutStatus, Remittance
from app.models.user import KYCStatus, User
from app.schemas.kyc import AdminKYCApplicationRead, KYCApplicationRead, KYCReviewRequest
from app.schemas.remittance import (
    CashInConfirmRequest,
    CashInConfirmResponse,
    CashOutRead,
    RemittanceRead,
)
from app.services import cashin_cashout_service

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


@router.post(
    "/remittances/{remittance_id}/confirm-payment",
    response_model=CashInConfirmResponse,
)
def confirm_zar_payment(
    remittance_id: uuid.UUID,
    payload: CashInConfirmRequest | None = Body(default=None),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """
    Mock payment-service confirmation that ZAR cash-in was received
    (spec §8), and the manual retry for a settlement whose message never
    reached the queue.

    Confirming is idempotent: a remittance that is already confirmed is
    simply (re)published, which is safe because the worker claims each
    remittance exactly once (spec §9.5). The cash-in method is optional —
    an admin confirming a bank deposit on the sender's behalf usually
    knows it as a bank transfer, which is the default.
    """
    remittance = db.get(Remittance, remittance_id)
    if remittance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Remittance not found"
        )

    method = (
        payload.cash_in_method
        if payload is not None
        else (remittance.cash_in_method or "bank_transfer")
    )

    try:
        cashin_cashout_service.simulate_cash_in(db, remittance, method)
    except (
        cashin_cashout_service.QuoteExpiredError,
        cashin_cashout_service.CashInStateError,
        cashin_cashout_service.RecipientNotRegisteredError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except cashin_cashout_service.InvalidCashInMethodError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    db.commit()

    try:
        cashin_cashout_service.queue_for_settlement(db, remittance)
        db.commit()
    except cashin_cashout_service.SettlementPublishError as exc:
        db.rollback()
        db.refresh(remittance)
        return CashInConfirmResponse(
            remittance=RemittanceRead.model_validate(remittance),
            queued=False,
            detail=(
                f"Cash-in is confirmed but the settlement queue is "
                f"unreachable ({exc}). Call this endpoint again once it "
                f"recovers."
            ),
        )

    db.refresh(remittance)
    return CashInConfirmResponse(
        remittance=RemittanceRead.model_validate(remittance),
        queued=True,
        detail="Cash-in confirmed and queued for settlement.",
    )


def _get_cash_out(db: Session, cash_out_id: uuid.UUID) -> CashOut:
    cash_out = db.get(CashOut, cash_out_id)
    if cash_out is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cash-out not found"
        )
    return cash_out


@router.get("/cash-outs", response_model=list[CashOutRead])
def list_cash_outs(
    status_filter: CashOutStatus | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """The payout queue, oldest first — the order an admin should work it."""
    query = db.query(CashOut)
    if status_filter is not None:
        query = query.filter(CashOut.status == status_filter)
    return query.order_by(CashOut.requested_at.asc()).all()


@router.post("/cash-outs/{cash_out_id}/approve", response_model=CashOutRead)
def approve_cash_out(
    cash_out_id: uuid.UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """
    Releases a requested cash-out: the simulated payout rail runs and the
    fiat leg is credited to the recipient's ledger balance (spec §10).

    The UCTUSD was already debited when the cash-out was requested, so
    approving moves no settlement token — it only completes the other
    half of a swap that is already half-done.
    """
    cash_out = _get_cash_out(db, cash_out_id)
    try:
        cashin_cashout_service.simulate_cash_out(db, cash_out)
    except cashin_cashout_service.CashOutStateError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(cash_out)
    return cash_out


@router.post("/cash-outs/{cash_out_id}/reject", response_model=CashOutRead)
def reject_cash_out(
    cash_out_id: uuid.UUID,
    payload: KYCReviewRequest = KYCReviewRequest(),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """
    Refuses a payout — the payout partner bounced it, the details did not
    check out — and refunds the reserved UCTUSD.

    The refund is a new incoming ledger entry rather than an undo:
    wallet_transactions is an immutable audit trail (spec §9.4), so the
    reversal has to be visible in its own right.
    """
    cash_out = _get_cash_out(db, cash_out_id)
    try:
        cashin_cashout_service.fail_cash_out(
            db, cash_out, payload.reason or "Rejected by administrator"
        )
    except cashin_cashout_service.CashOutStateError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(cash_out)
    return cash_out
