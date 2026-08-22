"""
Consumes settlement messages and submits RLUSD transfers to XRPL Testnet.

Flow:
  1. ZAR payment confirmed (see app.routers.admin.confirm_zar_payment)
  2. Settlement message pushed to the queue, keyed by remittance idempotency_key
  3. This worker reads the message
  4. Submits the RLUSD Payment via app.services.xrpl_service
  5. Records success/failure + tx hash on the Remittance row
  6. Updates the recipient Wallet balance

Run separately from the API process, e.g.:
    python -m worker.settlement_worker

De-duplication: track processed idempotency_keys (e.g. a unique DB constraint
or a Redis SET) so a redelivered message can never credit a wallet twice.
"""
import time

from app.config import settings


def consume_settlement_queue():
    # TODO: connect to settings.redis_url / chosen broker, read from
    # settings.settlement_stream_name, and call process_settlement_message per item
    while True:
        # placeholder poll loop
        time.sleep(1)


def process_settlement_message(message: dict):
    idempotency_key = message.get("idempotency_key")

    # TODO:
    #  1. Check idempotency_key hasn't already been processed -> skip if so
    #  2. Load Remittance, mark status = settling
    #  3. Decrypt sender/platform wallet seed (app.security.encryption) only within this scope
    #  4. Call xrpl_service.send_rlusd_payment(...)
    #  5. On success: status = settled, store xrpl_tx_hash, credit recipient Wallet.rlusd_balance,
    #     write a WalletTransaction row
    #  6. On failure: status = failed, log reason (never log the decrypted seed)
    raise NotImplementedError


if __name__ == "__main__":
    consume_settlement_queue()
