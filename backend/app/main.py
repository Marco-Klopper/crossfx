"""
CrossFX API entrypoint.

Run locally with:
    uvicorn app.main:app --reload
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import admin, auth, beneficiaries, kyc, remittances, wallet

app = FastAPI(
    title="CrossFX API",
    description="Prototype cross-border FX remittance platform settling in UCTUSD on XRPL Testnet.",
    version="0.1.0",
)

# Required for Track 4's browser frontend to call this API cross-origin.
# Schema is not created here (no create_all) — that's Alembic's job; see migrations/.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(kyc.router, prefix="/kyc", tags=["kyc"])
app.include_router(beneficiaries.router, prefix="/beneficiaries", tags=["beneficiaries"])
app.include_router(remittances.router, prefix="/remittances", tags=["remittances"])
app.include_router(wallet.router, prefix="/wallet", tags=["wallet"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])


@app.get("/health", tags=["meta"])
def health_check():
    return {"status": "ok", "service": "crossfx-api"}
