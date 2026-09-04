"""
app.services.xrpl_service — all XRPL network calls are mocked, no live Testnet
access needed to run these.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.services import xrpl_service
from app.services.xrpl_service import XRPLTransactionError


def _response(tx_hash=None, tx_result="tesSUCCESS", validated=True):
    result = {"meta": {"TransactionResult": tx_result}, "validated": validated}
    if tx_hash is not None:
        result["hash"] = tx_hash
    response = MagicMock()
    response.is_successful.return_value = True
    response.result = result
    return response


class TestCreateTestnetAccount:
    def test_returns_address_and_seed(self):
        fake_wallet = MagicMock(classic_address="rFakeAddress", seed="sFakeSeed")
        with patch.object(xrpl_service, "generate_faucet_wallet", return_value=fake_wallet) as mock_gen:
            result = xrpl_service.create_testnet_account()

        mock_gen.assert_called_once()
        assert result == {"address": "rFakeAddress", "seed": "sFakeSeed"}


def _fake_wallet(address="rFakeSenderAddress"):
    return MagicMock(classic_address=address)


class TestEstablishRlusdTrustline:
    def test_submits_trust_set_for_rlusd_issuer_and_returns_hash(self):
        with patch.object(xrpl_service.Wallet, "from_seed", return_value=_fake_wallet()), patch.object(
            xrpl_service, "submit_and_wait", return_value=_response(tx_hash="TRUSTHASH")
        ) as mock_submit:
            tx_hash = xrpl_service.establish_rlusd_trustline("sSomeSeed")

        assert tx_hash == "TRUSTHASH"
        submitted_tx = mock_submit.call_args[0][0]
        assert submitted_tx.limit_amount.issuer == xrpl_service.settings.rlusd_issuer_address
        assert submitted_tx.limit_amount.currency == xrpl_service.settings.rlusd_currency_code

    def test_raises_on_failed_trust_set(self):
        with patch.object(xrpl_service.Wallet, "from_seed", return_value=_fake_wallet()), patch.object(
            xrpl_service, "submit_and_wait", return_value=_response(tx_result="tecNO_DST")
        ):
            with pytest.raises(XRPLTransactionError):
                xrpl_service.establish_rlusd_trustline("sSomeSeed")


class TestSendRlusdPayment:
    def test_submits_payment_and_returns_hash(self):
        with patch.object(xrpl_service.Wallet, "from_seed", return_value=_fake_wallet()), patch.object(
            xrpl_service, "submit_and_wait", return_value=_response(tx_hash="PAYHASH")
        ) as mock_submit:
            tx_hash = xrpl_service.send_rlusd_payment("sSenderSeed", "rDestination", "12.50")

        assert tx_hash == "PAYHASH"
        submitted_tx = mock_submit.call_args[0][0]
        assert submitted_tx.destination == "rDestination"
        assert submitted_tx.amount.value == "12.50"
        assert submitted_tx.amount.issuer == xrpl_service.settings.rlusd_issuer_address

    def test_raises_on_failed_payment(self):
        with patch.object(xrpl_service.Wallet, "from_seed", return_value=_fake_wallet()), patch.object(
            xrpl_service, "submit_and_wait", return_value=_response(tx_result="tecUNFUNDED_PAYMENT")
        ):
            with pytest.raises(XRPLTransactionError):
                xrpl_service.send_rlusd_payment("sSenderSeed", "rDestination", "12.50")


class TestGetTransactionStatus:
    def test_success(self):
        with patch.object(
            xrpl_service.client, "request", return_value=_response(tx_result="tesSUCCESS")
        ):
            assert xrpl_service.get_transaction_status("SOMEHASH") == "success"

    def test_failed(self):
        with patch.object(
            xrpl_service.client, "request", return_value=_response(tx_result="tecUNFUNDED_PAYMENT")
        ):
            assert xrpl_service.get_transaction_status("SOMEHASH") == "failed"

    def test_pending_when_not_yet_validated(self):
        with patch.object(
            xrpl_service.client, "request", return_value=_response(validated=False)
        ):
            assert xrpl_service.get_transaction_status("SOMEHASH") == "pending"
