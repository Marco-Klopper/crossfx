"""
Registration, login/logout, basic profile management.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import KYCStatus, User
from app.schemas.token import TokenResponse
from app.schemas.user import MeResponse, TransactionLimits, UserLogin, UserRead, UserRegister
from app.security.hashing import hash_password, verify_password
from app.security.jwt import create_access_token

router = APIRouter()

# Generic message for both "no such user" and "wrong password". Returning a
# distinct message per case (spec §13) would let an attacker enumerate which
# emails are registered — same reasoning as returning 404 instead of 403 for
# another sender's beneficiary in routers/beneficiaries.py.
_INVALID_CREDENTIALS_DETAIL = "Incorrect email or password"


def _issue_token(user: User) -> TokenResponse:
    access_token = create_access_token(subject=str(user.id))
    return TokenResponse(
        access_token=access_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, db: Session = Depends(get_db)):
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_CREDENTIALS_DETAIL)
    return _issue_token(user)


@router.post("/token", response_model=TokenResponse)
def login_form(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """
    Same credential check as /login, wrapped in the OAuth2 form shape Swagger's
    "Authorize" button expects. This exists purely to make /docs clickable —
    the frontend (Track 4) should call /login with a JSON body instead.
    """
    user = db.query(User).filter(User.email == form_data.username).first()
    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_CREDENTIALS_DETAIL)
    return _issue_token(user)


@router.post("/logout")
def logout():
    """
    We issue stateless JWTs with no server-side blacklist, so the server has
    nothing to invalidate — the client just discards the token. Documented as
    a known limitation in docs/technical-specification.md §15 rather than
    faked with a no-op that implies real revocation.
    """
    return {"detail": "Logged out. Discard the access token client-side; the server cannot revoke it."}


@router.get("/me", response_model=MeResponse)
def get_profile(current_user: User = Depends(get_current_user)):
    if current_user.kyc_status == KYCStatus.APPROVED:
        limits = TransactionLimits(
            daily_limit_zar=settings.verified_daily_limit,
            monthly_limit_zar=settings.verified_monthly_limit,
        )
    else:
        limits = TransactionLimits(
            daily_limit_zar=settings.unverified_daily_limit,
            monthly_limit_zar=settings.unverified_monthly_limit,
        )
    return MeResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        kyc_status=current_user.kyc_status,
        limits=limits,
    )
