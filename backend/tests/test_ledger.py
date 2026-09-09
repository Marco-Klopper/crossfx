"""
Unit tests for app.services.ledger.Ledger — the internal
multi-currency ledger that is the authoritative record of holdings
under pooled custody (spec §9.1).
"""
from decimal import Decimal

import pytest

from app.models.wallet import LedgerBalance, WalletTransaction
from app.services.ledger import (
    Direction,
    EntryStatus,
    InsufficientFundsError,
    Ledger,
    LedgerError,
    UnsupportedCurrencyError,
)


@pytest.fixture()
def ledger(db_session):
    return Ledger(db_session)


@pytest.fixture()
def wallet(ledger, db_session, user_factory):
    w = ledger.wallet_for(user_factory())
    db_session.commit()
    return w


class TestWallets:
    def test_wallet_for_is_idempotent(self, ledger, db_session, user_factory):
        user = user_factory()
        first = ledger.wallet_for(user)
        db_session.commit()
        assert ledger.wallet_for(user).id == first.id


class TestCredits:
    def test_creates_balance_row_and_entry(self, ledger, db_session, wallet):
        ledger.credit(wallet, "UCTUSD", Decimal("25.5"))
        db_session.commit()

        assert ledger.balance(wallet, "UCTUSD") == Decimal("25.5")
        assert db_session.query(WalletTransaction).count() == 1

    def test_credits_accumulate(self, ledger, db_session, wallet):
        for _ in range(3):
            ledger.credit(wallet, "UCTUSD", Decimal("10"))
        db_session.commit()

        assert ledger.balance(wallet, "UCTUSD") == Decimal("30")
        # One balance row, three entries: a rollup, not a log.
        assert db_session.query(LedgerBalance).count() == 1
        assert db_session.query(WalletTransaction).count() == 3

    def test_entry_carries_the_xrpl_hash(self, ledger, db_session, wallet):
        ledger.credit(
            wallet, "UCTUSD", Decimal("7"), xrpl_tx_hash="ABCDEF123"
        )
        db_session.commit()

        entry = db_session.query(WalletTransaction).one()
        assert entry.xrpl_tx_hash == "ABCDEF123"
        assert entry.direction == Direction.INCOMING.value
        assert entry.status == EntryStatus.SUCCESS.value


class TestMultiCurrency:
    def test_currencies_are_independent(self, ledger, db_session, wallet):
        ledger.credit(wallet, "UCTUSD", Decimal("100"))
        ledger.credit(wallet, "ZAR", Decimal("2000"))
        db_session.commit()

        assert ledger.balance(wallet, "UCTUSD") == Decimal("100")
        assert ledger.balance(wallet, "ZAR") == Decimal("2000")
        assert ledger.balance(wallet, "USD") == Decimal("0")

    def test_balances_pads_unheld_currencies_with_zero(
        self, ledger, db_session, wallet
    ):
        ledger.credit(wallet, "UCTUSD", Decimal("3"))
        db_session.commit()

        rows = {
            row.currency: Decimal(row.amount)
            for row in ledger.balances(wallet)
        }
        assert rows == {
            "UCTUSD": Decimal("3"),
            "USD": Decimal("0"),
            "ZAR": Decimal("0"),
        }


class TestDebits:
    def test_reduces_balance(self, ledger, db_session, wallet):
        ledger.credit(wallet, "UCTUSD", Decimal("50"))
        ledger.debit(wallet, "UCTUSD", Decimal("20"))
        db_session.commit()

        assert ledger.balance(wallet, "UCTUSD") == Decimal("30")

    def test_beyond_balance_is_refused(self, ledger, wallet):
        """This is the "validate sufficient balance" step of cash-out."""
        ledger.credit(wallet, "UCTUSD", Decimal("5"))
        with pytest.raises(InsufficientFundsError):
            ledger.debit(wallet, "UCTUSD", Decimal("6"))

    def test_of_a_never_held_currency_is_refused(self, ledger, wallet):
        with pytest.raises(InsufficientFundsError):
            ledger.debit(wallet, "USD", Decimal("1"))


class TestValidation:
    def test_unsupported_currency_is_refused(self, ledger, wallet):
        with pytest.raises(UnsupportedCurrencyError):
            ledger.credit(wallet, "BTC", Decimal("1"))

    def test_currency_codes_are_normalised(self, ledger, db_session, wallet):
        ledger.credit(wallet, " uctusd ", Decimal("1"))
        db_session.commit()
        assert ledger.balance(wallet, "UCTUSD") == Decimal("1")

    @pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-1")])
    def test_non_positive_amounts_are_refused(self, ledger, wallet, amount):
        with pytest.raises(LedgerError):
            ledger.credit(wallet, "UCTUSD", amount)

    def test_a_bare_string_direction_is_refused(self, ledger, wallet):
        """
        Direction is an enum precisely so a typo cannot become a new
        kind of ledger entry.
        """
        with pytest.raises(LedgerError):
            ledger.record_external(
                wallet, "sideways", "UCTUSD", Decimal("1")
            )


class TestExternalLegs:
    def test_records_without_moving_balance(
        self, ledger, db_session, wallet
    ):
        """
        The sender's ZAR leg: their money went bank -> corridor pool,
        so there is a transaction to show them but no ledger balance of
        theirs to debit.
        """
        ledger.record_external(
            wallet, Direction.OUTGOING, "ZAR", Decimal("1000")
        )
        db_session.commit()

        assert ledger.balance(wallet, "ZAR") == Decimal("0")
        assert db_session.query(WalletTransaction).count() == 1
        assert db_session.query(LedgerBalance).count() == 0

    def test_a_failed_leg_credits_nothing(self, ledger, db_session, wallet):
        ledger.record_external(
            wallet,
            Direction.INCOMING,
            "UCTUSD",
            Decimal("99"),
            status=EntryStatus.FAILED,
            failure_reason="tecUNFUNDED_PAYMENT",
        )
        db_session.commit()

        assert ledger.balance(wallet, "UCTUSD") == Decimal("0")
        entry = db_session.query(WalletTransaction).one()
        assert entry.status == EntryStatus.FAILED.value
        assert entry.failure_reason == "tecUNFUNDED_PAYMENT"
