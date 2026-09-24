"""
Registration, login/logout, basic profile management.
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.token import TokenResponse
from app.schemas.user import (
    MeResponse,
    TransactionLimits,
    UserLogin,
    UserRead,
    UserRegister,
)
from app.security.hashing import hash_password, verify_password
from app.security.jwt import create_access_token
from app.services.limits_service import limits_for

router = APIRouter()

# Generic message for both "no such user" and "wrong password". Returning a
# distinct message per case (spec §13) would let an attacker enumerate which
# emails are registered — same reasoning as returning 404 instead of 403 for
# another sender's beneficiary in routers/beneficiaries.py.
_INVALID_CREDENTIALS_DETAIL = "Incorrect email or password"

# A hash of a random string nobody holds the password to. When the email
# is unknown we verify against this instead of returning immediately, so
# both branches do the same bcrypt work.
#
# Without it the generic message above was undone by the clock:
# `user is None or not verify_password(...)` short-circuits, so an
# unknown email answered in about a millisecond while a known one paid
# bcrypt's ~100ms. That difference is measurable over a network and is
# enough to enumerate accounts - which is the very thing the shared
# message exists to prevent.
#
# Built lazily so importing this module does not cost a key derivation.
_dummy_hash: str | None = None


def _dummy_password_hash() -> str:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(32))
    return _dummy_hash


def _authenticate(db: Session, email: str, password: str) -> User:
    """Returns the user, or raises the same 401 whatever went wrong."""
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        verify_password(password, _dummy_password_hash())
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_CREDENTIALS_DETAIL,
        )
    if not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_CREDENTIALS_DETAIL,
        )
    return user


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
        # This does confirm that an address is registered, which /login
        # goes to some trouble to avoid. It stays that way on purpose:
        # the only real fix is to accept every registration and send a
        # verification email - "check your inbox" either way - and this
        # prototype has no mail transport. Returning a fake 201 without
        # one would strand a legitimate user who had simply forgotten
        # they already signed up. Recorded in the tech spec's 15 as a
        # known limitation rather than papered over here.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        ) from None
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    return _issue_token(_authenticate(db, payload.email, payload.password))


@router.post("/token", response_model=TokenResponse)
def login_form(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """
    Same credential check as /login, wrapped in the OAuth2 form shape Swagger's
    "Authorize" button expects. This exists purely to make /docs clickable —
    the frontend (Track 4) should call /login with a JSON body instead.
    """
    return _issue_token(
        _authenticate(db, form_data.username, form_data.password)
    )


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
    # Delegated rather than branched on here: limits_service.limits_for
    # is what the quote endpoint enforces, and a profile screen that
    # promised different numbers from the ones a send is checked against
    # would be worse than no profile screen.
    daily_limit, monthly_limit = limits_for(current_user.kyc_status)
    limits = TransactionLimits(
        daily_limit_zar=daily_limit, monthly_limit_zar=monthly_limit
    )
    return MeResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        kyc_status=current_user.kyc_status,
        is_admin=current_user.is_admin,
        limits=limits,
    )
