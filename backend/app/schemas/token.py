"""
JWT access-token shapes. See app.security.jwt for token creation/decoding.
"""
from pydantic import BaseModel


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds, matches app.config.settings.access_token_expire_minutes


class TokenPayload(BaseModel):
    """Decoded JWT claims. sub is the User.id as a string (JWT 'sub' must be a string)."""

    sub: str
    exp: int
    iat: int
