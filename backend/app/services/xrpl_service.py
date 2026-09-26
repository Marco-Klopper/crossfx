"""
XRPL Testnet integration: account setup, trust lines, issued-currency
payments, and transaction validation.

Docs: https://xrpl.org/docs/tutorials/python/build-apps/get-started

`XRPLService` is the ONLY place in the codebase that decrypts a wallet
seed, which is the brief's private-key requirement ("only be decrypted
by the component responsible for signing XRPL transactions"). Callers
hand in a PlatformWallet row and get a transaction hash back; no seed
crosses that boundary in either direction, and nothing here is logged.

NOTE on the settlement asset. The class settles in UCTUSD, a
lecturer-issued Testnet IOU (course announcement, 2026-09-08). Nothing
in this module is token-specific: the issuer and currency code are
injected, so changing the settlement token is a .env change.

UCTUSD's currency code is the 40-character hex form
(5543545553440000000000000000000000000000) because the symbol is six
characters; only 3-character ISO-style codes can be given literally.
Anything comparing a currency code must use `self.currency_code`.
"""
import re
from decimal import Decimal

from xrpl.asyncio.transaction.reliable_submission import (
    XRPLReliableSubmissionException,
)
from xrpl.clients import JsonRpcClient
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.models.requests import AccountLines, Tx
from xrpl.models.transactions import Payment, TrustSet
from xrpl.transaction import submit_and_wait
from xrpl.wallet import Wallet, generate_faucet_wallet

from app.config import settings
from app.security.encryption import decrypt_seed

# A trust line limit high enough never to need raising at this
# prototype's volumes. A trust line is a cap, not a balance: setting it
# grants no tokens.
TRUSTLINE_LIMIT = "1000000000"


class XRPLTransactionError(Exception):
    """
    A submitted transaction did not settle with tesSUCCESS.

    `result_code` is the raw XRPL engine code where one could be
    recovered (tecPATH_DRY, tecUNFUNDED_PAYMENT, ...), which is what
    the settlement worker records on the failed remittance.
    """

    def __init__(self, message: str, result_code: str | None = None) -> None:
        super().__init__(message)
        self.result_code = result_code


# XRPL engine result codes: tes (success), tec, tef, tel, tem, ter.
_RESULT_CODE = re.compile(r"\bte[cfslmr][A-Za-z_]+\b")


def _extract_result_code(text: str) -> str | None:
    match = _RESULT_CODE.search(text)
    return match.group(0) if match else None


class XRPLService:
    """
    A connection to one XRPL node, configured for one issued currency.
    """

    def __init__(
        self,
        client: JsonRpcClient | None = None,
        *,
        issuer: str | None = None,
        currency_code: str | None = None,
    ) -> None:
        self.client = client or JsonRpcClient(settings.xrpl_testnet_json_rpc)
        self.issuer = issuer or settings.uctusd_issuer_address
        self.currency_code = currency_code or settings.uctusd_currency_code

    # -- internals ------------------------------------------------------

    def _amount(self, value: str) -> IssuedCurrencyAmount:
        return IssuedCurrencyAmount(
            currency=self.currency_code,
            issuer=self.issuer,
            value=value,
        )

    def _submit(self, transaction, wallet: Wallet) -> str:
        """
        Signs, submits, and blocks until the transaction is in a
        validated ledger. Returns its hash, or raises
        XRPLTransactionError if the ledger rejected it.

        Two failure shapes, both normalised to XRPLTransactionError so
        callers have one thing to catch:

          - xrpl-py raises XRPLReliableSubmissionException when a
            transaction reaches a validated ledger with a non-tes
            code. This is the path a real rejection takes — confirmed
            against Testnet, where an unfunded pool produced
            tecPATH_DRY.
          - Should it instead return a response carrying a non-tes
            TransactionResult, the check below catches that too.
        """
        try:
            response = submit_and_wait(transaction, self.client, wallet)
        except XRPLReliableSubmissionException as exc:
            code = _extract_result_code(str(exc))
            raise XRPLTransactionError(
                f"XRPL transaction failed: {code or exc}", result_code=code
            ) from exc

        result = response.result.get("meta", {}).get("TransactionResult")
        if result != "tesSUCCESS":
            raise XRPLTransactionError(
                f"XRPL transaction failed: {result}", result_code=result
            )
        return response.result["hash"]

    def _seed_for(self, platform_wallet) -> str:
        """
        Decrypts a pooled wallet's seed.
        """
        return decrypt_seed(platform_wallet.xrpl_encrypted_seed)

    # -- account setup --------------------------------------------------

    def create_testnet_account(self) -> dict:
        """
        Funds a new account from the XRP faucet. An XRPL account does
        not exist until funded, so this has to happen before it can
        transact or hold a trust line.
        """
        wallet = generate_faucet_wallet(self.client)
        return {"address": wallet.classic_address, "seed": wallet.seed}

    def establish_trustline(self, wallet_seed: str) -> str:
        """
        Submits a TrustSet so the wallet can hold the issued currency.
        """
        wallet = Wallet.from_seed(wallet_seed)
        trust_set = TrustSet(
            account=wallet.classic_address,
            limit_amount=self._amount(TRUSTLINE_LIMIT),
        )
        return self._submit(trust_set, wallet)

    def send_payment(
        self, sender_seed: str, destination_address: str, amount: str
    ) -> str:
        """
        Submits a Payment denominated in the issued currency and
        returns its hash.
        """
        wallet = Wallet.from_seed(sender_seed)
        payment = Payment(
            account=wallet.classic_address,
            destination=destination_address,
            amount=self._amount(amount),
        )
        return self._submit(payment, wallet)

    def establish_pool_trustline(self, pool) -> str:
        """
        TrustSet from a pooled wallet to the issuer. Both pools need
        one — the send pool to hold liquidity, the payout pool to
        receive it. Run once per pool at setup, never on a request path.
        """
        return self.establish_trustline(self._seed_for(pool))

    def send_pooled_payment(
        self, source_pool, destination_address: str, amount: str
    ) -> str:
        """
        The per-remittance on-chain leg: a payment from the send-side
        pool to the payout-side pool, submitted once per settlement
        message by worker/settlement_worker.py.

        Raises XRPLTransactionError if the transaction does not reach
        tesSUCCESS, which the worker turns into a failed remittance
        (the brief's "failed-transaction handling").
        """
        return self.send_payment(
            self._seed_for(source_pool), destination_address, amount
        )

    def burn(self, source_pool, amount: str) -> str:
        """
        Redeems tokens by paying them back to the issuer, which is how
        a withdrawal is simulated: sending UCTUSD to the issuing address
        stands in for handing it to an exchange. Issued tokens paid to
        their issuer leave circulation.

        Raises XRPLTransactionError if the transaction does not reach
        tesSUCCESS, which the worker turns into a failed cash-out.
        """
        return self.send_payment(
            self._seed_for(source_pool), self.issuer, amount
        )

    # -- reads ----------------------------------------------------------

    def transaction_status(self, tx_hash: str) -> str:
        """Returns "pending", "success" or "failed"."""
        result = self.client.request(Tx(transaction=tx_hash)).result
        if not result.get("validated", False):
            return "pending"
        if result["meta"]["TransactionResult"] == "tesSUCCESS":
            return "success"
        return "failed"

    def issued_balance(self, address: str) -> Decimal:
        """
        An account's holding of the issued currency, read from its
        trust line to the issuer. Zero when no trust line exists yet.

        This is the figure the internal ledger reconciles against: the
        sum of every user's ledger balance should equal the payout
        pool's on-chain balance (spec §9.6).
        """
        # peer= asks the node for only this issuer's line rather than
        # every line on the account; ledger_index="validated" keeps the
        # read off unvalidated state, which matters for a
        # reconciliation figure.
        response = self.client.request(
            AccountLines(
                account=address,
                peer=self.issuer,
                ledger_index="validated",
            )
        )
        for line in response.result.get("lines", []):
            if line.get("currency") == self.currency_code:
                return Decimal(line["balance"])
        return Decimal("0")
