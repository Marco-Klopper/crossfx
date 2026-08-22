"""
Registration, login/logout, basic profile management.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter()


@router.post("/register")
def register(db: Session = Depends(get_db)):
    # TODO: validate input, hash password (app.security.hashing), create User row
    raise NotImplementedError


@router.post("/login")
def login(db: Session = Depends(get_db)):
    # TODO: verify credentials, issue JWT access token
    raise NotImplementedError


@router.post("/logout")
def logout():
    # TODO: invalidate/blacklist token if using a token blacklist, or just client-side discard
    raise NotImplementedError


@router.get("/me")
def get_profile(db: Session = Depends(get_db)):
    # TODO: return profile, kyc_status, transaction limits, wallet balance summary
    raise NotImplementedError
