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
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.models.requests import Tx
from xrpl.models.transactions import Payment, TrustSet
from xrpl.transaction import submit_and_wait
from xrpl.wallet import Wallet, generate_faucet_wallet

from app.config import settings

client = JsonRpcClient(settings.xrpl_testnet_json_rpc)

# A trust line limit high enough to never need raising for this prototype's volumes.
_TRUSTLINE_LIMIT = "1000000000"


class XRPLTransactionError(Exception):
    """Raised when a submitted transaction doesn't settle with tesSUCCESS."""


def _rlusd_amount(value: str) -> IssuedCurrencyAmount:
    return IssuedCurrencyAmount(
        currency=settings.rlusd_currency_code,
        issuer=settings.rlusd_issuer_address,
        value=value,
    )


def _submit_and_extract_hash(transaction, wallet: Wallet) -> str:
    response = submit_and_wait(transaction, client, wallet)
    tx_result = response.result.get("meta", {}).get("TransactionResult")
    if tx_result != "tesSUCCESS":
        raise XRPLTransactionError(f"XRPL transaction failed: {tx_result}")
    return response.result["hash"]


def create_testnet_account():
    wallet = generate_faucet_wallet(client)
    return {"address": wallet.classic_address, "seed": wallet.seed}


def establish_rlusd_trustline(wallet_seed: str) -> str:
    """
    Submits a TrustSet transaction so the wallet can hold RLUSD.
    Returns the transaction hash.
    """
    wallet = Wallet.from_seed(wallet_seed)
    trust_set = TrustSet(
        account=wallet.classic_address,
        limit_amount=_rlusd_amount(_TRUSTLINE_LIMIT),
    )
    return _submit_and_extract_hash(trust_set, wallet)


def send_rlusd_payment(sender_seed: str, destination_address: str, amount: str) -> str:
    """
    Submits a Payment transaction denominated in RLUSD (issued currency).
    Returns the transaction hash. Caller is responsible for validating the
    result and handling failed-transaction cases per the brief.
    """
    wallet = Wallet.from_seed(sender_seed)
    payment = Payment(
        account=wallet.classic_address,
        destination=destination_address,
        amount=_rlusd_amount(amount),
    )
    return _submit_and_extract_hash(payment, wallet)


def get_transaction_status(tx_hash: str) -> str:
    response = client.request(Tx(transaction=tx_hash))
    result = response.result
    if not result.get("validated", False):
        return "pending"
    return "success" if result["meta"]["TransactionResult"] == "tesSUCCESS" else "failed"
