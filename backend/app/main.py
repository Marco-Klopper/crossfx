"""
CrossFX API entrypoint.

Run locally with:
    uvicorn app.main:app --reload
"""
from fastapi import FastAPI

from app.routers import admin, auth, beneficiaries, kyc, remittances, wallet

app = FastAPI(
    title="CrossFX API",
    description="Prototype cross-border FX remittance platform settling in RLUSD on XRPL Testnet.",
    version="0.1.0",
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
