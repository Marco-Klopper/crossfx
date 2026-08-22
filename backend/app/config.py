"""
Centralised app configuration, loaded from environment variables (.env).
Keep all "magic numbers" (fees, limits, margins) here so they stay configurable
per the project brief's requirement that all fees/limits be configurable.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "CrossFX"
    environment: str = "development"
    secret_key: str

    database_url: str

    # Private key encryption — key must live outside the DB that stores the encrypted keys.
    private_key_encryption_key: str

    # XRPL Testnet
    xrpl_testnet_json_rpc: str = "https://s.altnet.rippletest.net:51234"
    xrpl_testnet_wss: str = "wss://s.altnet.rippletest.net:51233"
    rlusd_issuer_address: str
    platform_wallet_seed: str | None = None

    # FX / fees
    exchange_rate_source: str = "mock"
    fixed_remittance_fee_zar: float = 25
    percent_fee_bps: int = 150
    fx_margin_bps: int = 100
    cashout_fee_bps: int = 100

    # Remittance limits (ZAR)
    unverified_daily_limit: float = 0
    unverified_monthly_limit: float = 0
    verified_daily_limit: float = 3000
    verified_monthly_limit: float = 25000

    # Queue
    queue_backend: str = "redis"
    redis_url: str = "redis://localhost:6379/0"
    settlement_stream_name: str = "crossfx-settlement-queue"

    class Config:
        env_file = ".env"


settings = Settings()
