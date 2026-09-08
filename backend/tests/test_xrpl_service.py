"""
app.services.xrpl_service.XRPLService.

The node client is injected rather than patched, so no live Testnet
access is needed. `submit_and_wait` and `generate_faucet_wallet` are
module-level xrpl-py functions, so those two are still patched.
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.services import xrpl_service as module
from app.services.xrpl_service import XRPLService, XRPLTransactionError

ISSUER = "rTestIssuerAddressXXXXXXXXXXXXXXXX"
CURRENCY = "5543545553440000000000000000000000000000"


@pytest.fixture()
def node():
    """A stand-in for the XRPL JSON-RPC client."""
    return MagicMock()


@pytest.fixture()
def xrpl(node):
    return XRPLService(node, issuer=ISSUER, currency_code=CURRENCY)


def _tx_response(tx_hash=None, tx_result="tesSUCCESS", validated=True):
    result = {
        "meta": {"TransactionResult": tx_result},
        "validated": validated,
    }
    if tx_hash is not None:
        result["hash"] = tx_hash
    response = MagicMock()
    response.result = result
    return response


def _lines_response(lines):
    response = MagicMock()
    response.result = {"lines": lines}
    return response


def _signing_wallet(address="rFakeSenderAddress"):
    return MagicMock(classic_address=address)


class TestConfiguration:
    def test_defaults_come_from_settings(self):
        service = XRPLService(MagicMock())
        assert service.issuer == module.settings.rlusd_issuer_address
        assert service.currency_code == module.settings.rlusd_currency_code

    def test_issuer_and_currency_are_overridable(self, xrpl):
        """
        Switching tokens (RLUSD <-> UCTUSD) must never need a code
        change — see the module docstring.
        """
        assert xrpl.issuer == ISSUER
        assert xrpl.currency_code == CURRENCY


class TestCreateTestnetAccount:
    def test_returns_address_and_seed(self, xrpl, node):
        faucet_wallet = MagicMock(
            classic_address="rFakeAddress", seed="sFakeSeed"
        )
        with patch.object(
            module, "generate_faucet_wallet", return_value=faucet_wallet
        ) as mock_faucet:
            result = xrpl.create_testnet_account()

        mock_faucet.assert_called_once_with(node)
        assert result == {"address": "rFakeAddress", "seed": "sFakeSeed"}


class TestEstablishTrustline:
    def test_submits_trust_set_for_the_configured_issuer(self, xrpl):
        with patch.object(
            module.Wallet, "from_seed", return_value=_signing_wallet()
        ), patch.object(
            module, "submit_and_wait", return_value=_tx_response("TRUSTHASH")
        ) as mock_submit:
            tx_hash = xrpl.establish_trustline("sSomeSeed")

        assert tx_hash == "TRUSTHASH"
        submitted = mock_submit.call_args[0][0]
        assert submitted.limit_amount.issuer == ISSUER
        assert submitted.limit_amount.currency == CURRENCY
        assert submitted.limit_amount.value == module.TRUSTLINE_LIMIT

    def test_raises_on_a_failed_trust_set(self, xrpl):
        with patch.object(
            module.Wallet, "from_seed", return_value=_signing_wallet()
        ), patch.object(
            module,
            "submit_and_wait",
            return_value=_tx_response(tx_result="tecNO_DST"),
        ):
            with pytest.raises(XRPLTransactionError):
                xrpl.establish_trustline("sSomeSeed")


class TestSendPayment:
    def test_submits_payment_and_returns_hash(self, xrpl):
        with patch.object(
            module.Wallet, "from_seed", return_value=_signing_wallet()
        ), patch.object(
            module, "submit_and_wait", return_value=_tx_response("PAYHASH")
        ) as mock_submit:
            tx_hash = xrpl.send_payment("sSenderSeed", "rDestination", "12.50")

        assert tx_hash == "PAYHASH"
        submitted = mock_submit.call_args[0][0]
        assert submitted.destination == "rDestination"
        assert submitted.amount.value == "12.50"
        assert submitted.amount.issuer == ISSUER
        assert submitted.amount.currency == CURRENCY

    def test_raises_on_a_failed_payment(self, xrpl):
        with patch.object(
            module.Wallet, "from_seed", return_value=_signing_wallet()
        ), patch.object(
            module,
            "submit_and_wait",
            return_value=_tx_response(tx_result="tecUNFUNDED_PAYMENT"),
        ):
            with pytest.raises(XRPLTransactionError):
                xrpl.send_payment("sSenderSeed", "rDestination", "12.50")


class TestPooledCustodyLayer:
    """
    What the rest of the system calls: pass a PlatformWallet row, get a
    transaction hash. Decryption happens inside this class and nowhere
    else, which is the brief's private-key requirement.
    """

    @staticmethod
    def _pool(seed="sEdPoolSeedNotReal", address="rPoolAddress"):
        from app.security.encryption import encrypt_seed

        return MagicMock(
            xrpl_address=address, xrpl_encrypted_seed=encrypt_seed(seed)
        )

    def test_pooled_payment_decrypts_the_seed_and_signs_with_it(self, xrpl):
        pool = self._pool(seed="sEdSendPoolSeed")

        with patch.object(
            module.Wallet, "from_seed", return_value=_signing_wallet()
        ) as mock_from_seed, patch.object(
            module, "submit_and_wait", return_value=_tx_response("POOLHASH")
        ) as mock_submit:
            tx_hash = xrpl.send_pooled_payment(pool, "rPayoutPool", "52.5")

        assert tx_hash == "POOLHASH"
        # The plaintext seed only ever appears here, inside XRPLService.
        mock_from_seed.assert_called_once_with("sEdSendPoolSeed")
        submitted = mock_submit.call_args[0][0]
        assert submitted.destination == "rPayoutPool"
        assert submitted.amount.value == "52.5"

    def test_failed_pooled_payment_raises(self, xrpl):
        with patch.object(
            module.Wallet, "from_seed", return_value=_signing_wallet()
        ), patch.object(
            module,
            "submit_and_wait",
            return_value=_tx_response(tx_result="tecPATH_DRY"),
        ):
            with pytest.raises(XRPLTransactionError):
                xrpl.send_pooled_payment(self._pool(), "rPayoutPool", "52.5")

    def test_pool_trustline_uses_the_decrypted_seed(self, xrpl):
        pool = self._pool(seed="sEdPayoutPoolSeed")

        with patch.object(
            module.Wallet, "from_seed", return_value=_signing_wallet()
        ) as mock_from_seed, patch.object(
            module, "submit_and_wait", return_value=_tx_response("TRUSTHASH")
        ):
            assert xrpl.establish_pool_trustline(pool) == "TRUSTHASH"

        mock_from_seed.assert_called_once_with("sEdPayoutPoolSeed")


class TestTransactionStatus:
    def test_success(self, xrpl, node):
        node.request.return_value = _tx_response(tx_result="tesSUCCESS")
        assert xrpl.transaction_status("SOMEHASH") == "success"

    def test_failed(self, xrpl, node):
        node.request.return_value = _tx_response(
            tx_result="tecUNFUNDED_PAYMENT"
        )
        assert xrpl.transaction_status("SOMEHASH") == "failed"

    def test_pending_when_not_yet_validated(self, xrpl, node):
        node.request.return_value = _tx_response(validated=False)
        assert xrpl.transaction_status("SOMEHASH") == "pending"


class TestIssuedBalance:
    """The pooled total the internal ledger reconciles against."""

    def test_returns_the_issuers_trust_line_balance(self, xrpl, node):
        node.request.return_value = _lines_response(
            [{"currency": CURRENCY, "balance": "1234.567891"}]
        )
        assert xrpl.issued_balance("rPool") == Decimal("1234.567891")

    def test_asks_the_node_for_only_the_issuers_validated_line(
        self, xrpl, node
    ):
        """
        Issuer filtering is done by the node via peer=, not
        client-side, and the read is pinned to the validated ledger
        because this figure is used for reconciliation (spec §9.6).
        """
        node.request.return_value = _lines_response([])
        xrpl.issued_balance("rPool")

        request = node.request.call_args[0][0]
        assert request.account == "rPool"
        assert request.peer == ISSUER
        assert request.ledger_index == "validated"

    def test_ignores_a_different_currency_on_the_same_issuer(
        self, xrpl, node
    ):
        node.request.return_value = _lines_response(
            [{"currency": "EUR", "balance": "999"}]
        )
        assert xrpl.issued_balance("rPool") == Decimal("0")

    def test_zero_when_no_trust_line_exists_yet(self, xrpl, node):
        node.request.return_value = _lines_response([])
        assert xrpl.issued_balance("rPool") == Decimal("0")
