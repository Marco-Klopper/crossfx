"""
Encrypts/decrypts XRPL private keys (seeds) before they touch the database.

Brief requirements this exists to satisfy:
  - encryption key must NOT live in the same database as the encrypted keys
    (see PRIVATE_KEY_ENCRYPTION_KEY in .env — keep it in a secrets manager / vault
    in anything beyond this academic prototype)
  - decrypted keys must only ever be handled inside the XRPL signing component
    (app.services.xrpl_service) — never returned via the API, never logged
"""
from cryptography.fernet import Fernet

from app.config import settings

_fernet = Fernet(settings.private_key_encryption_key.encode())


def encrypt_seed(plain_seed: str) -> str:
    return _fernet.encrypt(plain_seed.encode()).decode()


def decrypt_seed(encrypted_seed: str) -> str:
    return _fernet.decrypt(encrypted_seed.encode()).decode()
