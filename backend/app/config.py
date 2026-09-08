"""
Centralised app configuration, loaded from environment variables (.env).
Keep all "magic numbers" (fees, limits, margins) here so they stay configurable
per the project brief's requirement that all fees/limits be configurable.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "CrossFX"
    environment: str = "development"
    secret_key: str

    # JWT (app.security.jwt / app.dependencies.get_current_user)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Comma-separated browser origins allowed to call this API (Track 4's frontend).
    # Kept as a plain string rather than list[str]: pydantic-settings expects JSON
    # array syntax for list-typed env vars, which is an awkward thing to hand-edit
    # in a .env file. See cors_origins_list below for the parsed form.
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    database_url: str

    # Private key encryption — key must live outside the DB that stores the encrypted keys.
    private_key_encryption_key: str

    # XRPL Testnet. The settlement asset is UCTUSD, the lecturer-issued
    # Testnet IOU (course announcement, 2026-09-08). Both values are
    # config, not constants, so the settlement token can be changed
    # without touching code. The currency code is the 40-character hex
    # form because "UCTUSD" is six characters; only 3-character
    # ISO-style codes may be given literally on XRPL.
    xrpl_testnet_json_rpc: str = "https://s.altnet.rippletest.net:51234"
    xrpl_testnet_wss: str = "wss://s.altnet.rippletest.net:51233"
    uctusd_issuer_address: str
    uctusd_currency_code: str = "5543545553440000000000000000000000000000"

    # Pooled custody (spec §9.1): the two corridor pool accounts are
    # rows in the platform_wallets table, not env vars — their seeds
    # are Fernet-encrypted at rest and only PRIVATE_KEY_ENCRYPTION_KEY
    # (above) lives outside the DB. Create them once with:
    #     python -m scripts.init_platform_wallets

    # Ledger currencies the internal multi-currency ledger will accept.
    supported_currencies: str = "UCTUSD,USD,ZAR"

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

    # Queue (Redis Streams — one of the brokers the brief names)
    queue_backend: str = "redis"
    redis_url: str = "redis://localhost:6379/0"
    settlement_stream_name: str = "crossfx-settlement-queue"
    settlement_consumer_group: str = "crossfx-settlement-workers"
    # How long a worker blocks waiting for a message before looping (ms).
    settlement_block_ms: int = 5000

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def supported_currencies_list(self) -> list[str]:
        return [
            code.strip().upper()
            for code in self.supported_currencies.split(",")
            if code.strip()
        ]


settings = Settings()
