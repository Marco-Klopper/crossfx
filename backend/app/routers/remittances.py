"""
Core remittance flow: quote -> confirm cash-in -> queue settlement -> track status.

This router is the sender's half of the journey (spec §4–§8). The
recipient's half — balance, history, cash-out — is routers/wallet.py, and
the on-chain leg belongs to worker/settlement_worker.py. Nothing here ever
talks to XRPL: confirming cash-in publishes a message and returns, which
is what keeps the request path off the network the brief's asynchronous
flow exists to decouple from.
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import require_kyc_approved
from app.models.beneficiary import Beneficiary
from app.models.remittance import Remittance, RemittanceStatus
from app.models.user import User
from app.schemas.remittance import (
    CashInConfirmRequest,
    CashInConfirmResponse,
    LimitHeadroom,
    QuoteRequest,
    QuoteResponse,
    RemittanceRead,
)
from app.services import cashin_cashout_service, fee_service, fx_rate_service
from app.services.limits_service import LimitExceededError, assert_within_limits

router = APIRouter()


def _owned_remittance(
    db: Session, remittance_id: uuid.UUID, sender_id: uuid.UUID
) -> Remittance:
    """
    404 rather than 403 for someone else's remittance, matching
    routers/beneficiaries.py: a 403 would confirm the id exists (spec §13).
    """
    remittance = (
        db.query(Remittance)
        .filter(
            Remittance.id == remittance_id, Remittance.sender_id == sender_id
        )
        .first()
    )
    if remittance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Remittance not found"
        )
    return remittance


def _current_rate(db: Session):
    try:
        return fx_rate_service.get_usd_zar_rate(db)
    except fx_rate_service.FxRateUnavailableError as exc:
        # 503, not 500: quoting is temporarily impossible, and a retry is
        # the right response. Quoting a guessed rate would not be.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Exchange rate unavailable: {exc}",
        ) from exc


@router.post(
    "/quote", response_model=QuoteResponse, status_code=status.HTTP_201_CREATED
)
def create_quote(
    payload: QuoteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_kyc_approved),
):
    """
    Prices a send and persists it as a QUOTED remittance (spec §4–§6).

    The quote is a row rather than an in-memory figure for two reasons:
    the id the sender confirms cash-in against has to exist, and an
    outstanding quote has to hold limit headroom — otherwise a sender
    could take out ten quotes and fund all of them. `quote_expires_at` is
    what gives that headroom back if they never pay.

    Recipient registration is deliberately NOT checked here. Quoting is
    pure pricing; the check that the beneficiary is a registered user
    belongs at cash-in, where money actually changes hands.
    """
    beneficiary = (
        db.query(Beneficiary)
        .filter(
            Beneficiary.id == payload.beneficiary_id,
            Beneficiary.sender_id == current_user.id,
        )
        .first()
    )
    if beneficiary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Beneficiary not found"
        )

    now = datetime.now(timezone.utc)
    rate = _current_rate(db)

    # Serialise the check-then-insert below against this sender's other
    # in-flight quotes. Without the lock, two concurrent POSTs both read
    # the same running totals, both pass assert_within_limits, and both
    # insert — so a sender clears a regulatory limit by firing N requests
    # in parallel. Locking the sender's own users row is the narrowest
    # thing that serialises them, and it is the same with_for_update()
    # idiom services/ledger.py uses on balance rows. Like that one it is
    # a no-op on SQLite and does real work on Postgres.
    db.query(User).filter(User.id == current_user.id).with_for_update().first()

    try:
        usage = assert_within_limits(
            db, current_user, payload.zar_send_amount, now
        )
    except LimitExceededError as exc:
        # 403: the request is well-formed and the sender is authenticated —
        # policy is what refuses it. The message carries the headroom so
        # the quote screen can say how much would go through.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc

    # The beneficiary's preferred currency drives the payout estimate where
    # the corridor can price it; otherwise the estimate falls back to USD,
    # which UCTUSD is pegged to. Either way it is only an estimate — the
    # binding figure is chosen at cash-out (spec §10).
    preferred = (beneficiary.preferred_payout_currency or "").upper()
    payout_currency = (
        preferred
        if preferred in fee_service.PRICEABLE_PAYOUT_CURRENCIES
        else "USD"
    )

    try:
        quote = fee_service.calculate_quote(
            payload.zar_send_amount, rate, payout_currency
        )
    except fee_service.AmountTooSmallError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    remittance = Remittance(
        idempotency_key=uuid.uuid4().hex,
        sender_id=current_user.id,
        beneficiary_id=beneficiary.id,
        zar_send_amount=quote.zar_send_amount,
        fx_rate_used=quote.fx_rate,
        transaction_fee_zar=quote.transaction_fee_zar,
        fx_margin_zar=quote.fx_margin_zar,
        net_converted_zar=quote.net_converted_zar,
        uctusd_amount=quote.uctusd_amount,
        status=RemittanceStatus.QUOTED,
        quote_expires_at=now + timedelta(minutes=settings.quote_ttl_minutes),
    )
    db.add(remittance)
    db.commit()
    db.refresh(remittance)

    # Headroom as it stands *with this quote counted*, which is what the
    # sender will be held to on their next request.
    return QuoteResponse(
        remittance_id=remittance.id,
        status=remittance.status,
        beneficiary_id=beneficiary.id,
        zar_send_amount=quote.zar_send_amount,
        fx_rate=quote.fx_rate,
        transaction_fee_zar=quote.transaction_fee_zar,
        fx_margin_zar=quote.fx_margin_zar,
        net_converted_zar=quote.net_converted_zar,
        uctusd_amount=quote.uctusd_amount,
        effective_rate=quote.effective_rate,
        estimated_payout_currency=quote.estimated_payout_currency,
        cash_out_fee_uctusd=quote.cash_out_fee_uctusd,
        estimated_payout_amount=quote.estimated_payout_amount,
        quote_expires_at=remittance.quote_expires_at,
        limits=LimitHeadroom(
            daily_limit_zar=usage.daily_limit,
            monthly_limit_zar=usage.monthly_limit,
            daily_remaining_zar=max(
                usage.daily_remaining - quote.zar_send_amount, 0
            ),
            monthly_remaining_zar=max(
                usage.monthly_remaining - quote.zar_send_amount, 0
            ),
        ),
    )


@router.post(
    "/{remittance_id}/confirm-cash-in", response_model=CashInConfirmResponse
)
def confirm_cash_in(
    remittance_id: uuid.UUID,
    payload: CashInConfirmRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_kyc_approved),
):
    """
    Step 1 of the brief's asynchronous flow: the sender's rand is
    confirmed (simulated), then a settlement message is queued (spec §8).

    The two halves are committed separately and in that order. If the
    queue is unreachable the remittance stays CASH_IN_CONFIRMED — which
    the worker accepts as claimable — and the response says so, so the
    client polls rather than re-confirming. An admin can re-publish it
    with POST /admin/remittances/{id}/confirm-payment.
    """
    remittance = _owned_remittance(db, remittance_id, current_user.id)

    try:
        cashin_cashout_service.simulate_cash_in(
            db, remittance, payload.cash_in_method
        )
    except cashin_cashout_service.QuoteExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except cashin_cashout_service.CashInStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except cashin_cashout_service.RecipientNotRegisteredError as exc:
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
                "Cash-in is confirmed but the settlement queue is "
                f"unreachable ({exc}). It will settle once the queue "
                "recovers — no need to pay in again."
            ),
        )

    db.refresh(remittance)
    return CashInConfirmResponse(
        remittance=RemittanceRead.model_validate(remittance),
        queued=True,
        detail="Cash-in confirmed and queued for settlement.",
    )


@router.get("/", response_model=list[RemittanceRead])
def list_remittances(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_kyc_approved),
):
    """The authenticated sender's transaction history, newest first."""
    return (
        db.query(Remittance)
        .filter(Remittance.sender_id == current_user.id)
        .order_by(Remittance.created_at.desc())
        .all()
    )


@router.get("/{remittance_id}", response_model=RemittanceRead)
def get_remittance_status(
    remittance_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_kyc_approved),
):
    """
    Current status, plus the XRPL transaction hash once it has settled.
    This is the endpoint a client polls after confirming cash-in.
    """
    return _owned_remittance(db, remittance_id, current_user.id)
