"""
XRPL Testnet integration: account setup, TrustSet to the RLUSD issuer,
RLUSD payment submission, and transaction validation.

Docs: https://xrpl.org/docs/tutorials/python/build-apps/get-started

NOTE: settings.rlusd_issuer_address is a config value on purpose. The official
RLUSD Testnet faucet is capped at ~$10/24h per wallet, so UCT may switch the class
to a fallback IOU issuer if Ripple doesn't fund the UCT wallet in time. Swapping
issuers must stay a one-line .env change, never a code change — don't hardcode
the issuer address anywhere below.
"""
from xrpl.clients import JsonRpcClient

from app.config import settings

client = JsonRpcClient(settings.xrpl_testnet_json_rpc)


def create_testnet_account():
    # TODO: use xrpl.wallet.generate_faucet_wallet(client) for dev/test account creation
    raise NotImplementedError


def establish_rlusd_trustline(wallet_seed: str) -> str:
    """
    Submits a TrustSet transaction so the wallet can hold RLUSD.
    Returns the transaction hash.
    """
    # TODO: build TrustSet tx with limit_amount pointing at settings.rlusd_issuer_address,
    # sign with the wallet's seed, submit_and_wait via xrpl.transaction
    raise NotImplementedError


def send_rlusd_payment(sender_seed: str, destination_address: str, amount: str) -> str:
    """
    Submits a Payment transaction denominated in RLUSD (issued currency).
    Returns the transaction hash. Caller is responsible for validating the
    result and handling failed-transaction cases per the brief.
    """
    # TODO: build Payment tx with IssuedCurrencyAmount, sign, submit_and_wait,
    # check meta.TransactionResult == "tesSUCCESS"
    raise NotImplementedError


def get_transaction_status(tx_hash: str) -> str:
    # TODO: query the ledger for the tx and return success/failed/pending
    raise NotImplementedError
