"""
JWT access-token creation/decoding. Deliberately has no FastAPI/HTTP imports —
app.dependencies.get_current_user is what turns a decode failure into a 401;
this module just raises plain exceptions so it stays unit-testable on its own.
"""
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from app.config import settings


class InvalidTokenError(Exception):
    """Raised for any decode failure: bad signature, malformed token, or expiry."""


def create_access_token(subject: str) -> str:
    """subject is the User.id, stringified — JWT's 'sub' claim must be a string."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": subject, "iat": int(now.timestamp()), "exp": int(expires_at.timestamp())}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str:
    """Returns the subject (User.id as a string) or raises InvalidTokenError."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise InvalidTokenError(str(exc)) from exc

    subject = payload.get("sub")
    if subject is None:
        raise InvalidTokenError("token payload missing 'sub' claim")
    return subject
