"""
The internal multi-currency ledger.

In the pooled-custody model (spec §9.1) this module — not XRPL — is the
authoritative record of who owns what. Real value sits in two pooled
XRPL accounts; every user's holding is a `LedgerBalance` row, and every
movement of one is an immutable `WalletTransaction` entry.

Every balance change in the system must go through `Ledger`, so that a
balance and its audit trail can never disagree: it writes both, or
neither.

Callers by track:
  Track 2  worker/settlement_worker.py  credits the recipient's claim
  Track 3  routers/wallet.py /cash-out  debits UCTUSD, credits fiat

Usage:
    ledger = Ledger(db)
    wallet = ledger.wallet_for(user)
    ledger.credit(wallet, "UCTUSD", Decimal("52.5"))
    db.commit()
"""
import enum
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User
from app.models.wallet import LedgerBalance, Wallet, WalletTransaction


class Direction(str, enum.Enum):
    """Which way value moved, from the wallet owner's point of view."""

    INCOMING = "incoming"
    OUTGOING = "outgoing"


class EntryStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class LedgerError(Exception):
    """Base class for ledger rejections. Never carries key material."""


class UnsupportedCurrencyError(LedgerError):
    pass


class InsufficientFundsError(LedgerError):
    pass


class Ledger:
    """
    Ledger operations bound to one database session.

    Nothing here commits. The caller owns the transaction boundary, so
    that a settlement's ledger write and its remittance status update
    land together or not at all.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # -- wallets --------------------------------------------------------

    def wallet_for(self, user: User) -> Wallet:
        """
        A user's wallet, created on first use rather than at
        registration, so Track 1's auth flow needs no knowledge of
        Track 2's tables.
        """
        wallet = (
            self.db.query(Wallet).filter(Wallet.user_id == user.id).first()
        )
        if wallet is None:
            wallet = Wallet(user_id=user.id)
            self.db.add(wallet)
            self.db.flush()
        return wallet

    # -- balance movements ----------------------------------------------

    def credit(
        self,
        wallet: Wallet,
        currency: str,
        amount: Decimal,
        *,
        remittance_id=None,
        xrpl_tx_hash: str | None = None,
    ) -> WalletTransaction:
        """Increases a balance and records the entry behind it."""
        return self._move(
            wallet,
            Direction.INCOMING,
            currency,
            amount,
            remittance_id=remittance_id,
            xrpl_tx_hash=xrpl_tx_hash,
        )

    def debit(
        self,
        wallet: Wallet,
        currency: str,
        amount: Decimal,
        *,
        remittance_id=None,
        xrpl_tx_hash: str | None = None,
    ) -> WalletTransaction:
        """
        Decreases a balance and records the entry behind it. Raises
        InsufficientFundsError rather than going negative, which is
        already the "validate sufficient balance" step of a cash-out.
        """
        return self._move(
            wallet,
            Direction.OUTGOING,
            currency,
            amount,
            remittance_id=remittance_id,
            xrpl_tx_hash=xrpl_tx_hash,
        )

    def record_external(
        self,
        wallet: Wallet,
        direction: Direction,
        currency: str,
        amount: Decimal,
        *,
        remittance_id=None,
        xrpl_tx_hash: str | None = None,
        status: EntryStatus = EntryStatus.SUCCESS,
        failure_reason: str | None = None,
    ) -> WalletTransaction:
        """
        Records an entry that moves NO balance, for a leg whose
        counterparty is outside the ledger. Two cases, both real rather
        than convenience:

          - The sender's ZAR. It goes bank/card -> corridor pool and
            never lands in a ledger balance the sender holds, so
            debiting one would mean inventing a balance in order to
            destroy it. The entry exists because the brief requires the
            wallet to show outgoing transactions.
          - A failed settlement. The recipient should see that a
            transfer was attempted and failed; they must not see a
            credit.
        """
        code = self._validate(direction, currency, amount)
        return self._write_entry(
            wallet,
            direction=direction,
            code=code,
            amount=amount,
            remittance_id=remittance_id,
            xrpl_tx_hash=xrpl_tx_hash,
            status=status,
            failure_reason=failure_reason,
        )

    # -- reads ----------------------------------------------------------

    def balance(self, wallet: Wallet, currency: str) -> Decimal:
        """Zero for a currency never held — no row is not an error."""
        code = self._normalise_currency(currency)
        row = (
            self.db.query(LedgerBalance)
            .filter(
                LedgerBalance.wallet_id == wallet.id,
                LedgerBalance.currency == code,
            )
            .first()
        )
        return Decimal(row.amount) if row else Decimal("0")

    def balances(self, wallet: Wallet) -> list[LedgerBalance]:
        """
        Every currency the wallet holds, plus a zero row for every
        supported currency it does not, so the wallet UI can render a
        stable set of currencies rather than one that appears a row at
        a time.
        """
        held = {
            row.currency: row
            for row in self.db.query(LedgerBalance)
            .filter(LedgerBalance.wallet_id == wallet.id)
            .all()
        }
        return [
            held.get(code)
            or LedgerBalance(
                wallet_id=wallet.id, currency=code, amount=Decimal("0")
            )
            for code in settings.supported_currencies_list
        ]

    # -- internals ------------------------------------------------------

    @staticmethod
    def _normalise_currency(currency: str) -> str:
        code = currency.strip().upper()
        if code not in settings.supported_currencies_list:
            raise UnsupportedCurrencyError(
                f"{code} is not in SUPPORTED_CURRENCIES "
                f"({settings.supported_currencies})"
            )
        return code

    @classmethod
    def _validate(
        cls, direction: Direction, currency: str, amount: Decimal
    ) -> str:
        if not isinstance(direction, Direction):
            raise LedgerError(
                f"direction must be a Direction, got {direction!r}"
            )
        if amount <= 0:
            raise LedgerError(f"amount must be positive, got {amount}")
        return cls._normalise_currency(currency)

    def _locked_balance_row(self, wallet_id, code: str) -> LedgerBalance:
        """
        Fetches the (wallet, currency) balance row for update, creating
        it at zero if this is the wallet's first movement in that
        currency.

        with_for_update() is what serialises two concurrent settlement
        workers touching the same recipient. It is a no-op on SQLite
        (the dialect omits it), which is fine because the test suite is
        single-threaded, but it does real work on Postgres, which is
        what the load tests run against.
        """
        row = (
            self.db.query(LedgerBalance)
            .filter(
                LedgerBalance.wallet_id == wallet_id,
                LedgerBalance.currency == code,
            )
            .with_for_update()
            .first()
        )
        if row is None:
            row = LedgerBalance(
                wallet_id=wallet_id, currency=code, amount=Decimal("0")
            )
            self.db.add(row)
            self.db.flush()
        return row

    def _move(
        self,
        wallet: Wallet,
        direction: Direction,
        currency: str,
        amount: Decimal,
        *,
        remittance_id,
        xrpl_tx_hash: str | None,
    ) -> WalletTransaction:
        code = self._validate(direction, currency, amount)

        row = self._locked_balance_row(wallet.id, code)
        if direction is Direction.INCOMING:
            row.amount = Decimal(row.amount) + amount
        else:
            new_amount = Decimal(row.amount) - amount
            if new_amount < 0:
                raise InsufficientFundsError(
                    f"wallet {wallet.id} holds {row.amount} {code}, "
                    f"cannot debit {amount}"
                )
            row.amount = new_amount
        row.updated_at = datetime.now(timezone.utc)

        return self._write_entry(
            wallet,
            direction=direction,
            code=code,
            amount=amount,
            remittance_id=remittance_id,
            xrpl_tx_hash=xrpl_tx_hash,
            status=EntryStatus.SUCCESS,
            failure_reason=None,
        )

    def _write_entry(
        self,
        wallet: Wallet,
        *,
        direction: Direction,
        code: str,
        amount: Decimal,
        remittance_id,
        xrpl_tx_hash: str | None,
        status: EntryStatus,
        failure_reason: str | None,
    ) -> WalletTransaction:
        entry = WalletTransaction(
            wallet_id=wallet.id,
            remittance_id=remittance_id,
            direction=direction.value,
            currency=code,
            amount=amount,
            xrpl_tx_hash=xrpl_tx_hash,
            status=status.value,
            failure_reason=failure_reason,
        )
        self.db.add(entry)
        self.db.flush()
        return entry
