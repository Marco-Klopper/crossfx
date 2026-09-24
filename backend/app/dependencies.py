"""
Reusable FastAPI dependencies for auth/authorization. These three are the pieces
other tracks are expected to import:
  - get_current_user       any authenticated route
  - require_kyc_approved   Track 3's quote/remittance endpoints
  - require_admin          Track 1's own routers/admin.py, and any future admin route
"""
import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import KYCStatus, User
from app.security.jwt import InvalidTokenError, decode_access_token

# tokenUrl points at the form-login endpoint so Swagger's "Authorize" button works;
# it does not mean this is the only way to obtain a token (see routers/auth.py /login).
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        subject = decode_access_token(token)
        user_id = uuid.UUID(subject)
    except (InvalidTokenError, ValueError):
        # ValueError covers a syntactically valid JWT whose 'sub' isn't a UUID.
        # `from None`: the decode failure is not something a caller should see,
        # and chaining it would put token internals in a traceback.
        raise credentials_error from None

    user = db.get(User, user_id)
    if user is None:
        raise credentials_error
    return user


def require_kyc_approved(current_user: User = Depends(get_current_user)) -> User:
    if current_user.kyc_status != KYCStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires an approved KYC application",
        )
    return current_user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user
