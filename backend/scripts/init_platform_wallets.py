"""
One-off setup of the two pooled corridor wallets (spec §9.1). Run
from backend/, after `alembic upgrade head`:

    python -m scripts.init_platform_wallets

By default this funds two fresh XRPL Testnet accounts from the XRP faucet,
submits a TrustSet from each to the UCTUSD issuer, encrypts both seeds and
stores them as `platform_wallets` rows. Re-running it leaves existing pools
alone, so it is safe to run again after adding a migration.

To adopt wallets you already have (e.g. a pool already funded with UCTUSD
and you don't want to re-fund):

    python -m scripts.init_platform_wallets \
        --send-pool-seed sEd... --payout-pool-seed sEd...

IMPORTANT — the seeds are printed once, on creation, and never
again: they are stored encrypted and no API route can read them back.
Keep them somewhere safe before closing the terminal. Never commit
them.

LIQUIDITY: the send pool needs a UCTUSD balance before it can settle
anything. The XRP faucet funds XRP for reserves and TrustSet costs
only. Once this script has created the pools and their trust lines,
send the send pool's address to the lecturer, who distributes the
UCTUSD liquidity — see spec §15.
"""
import argparse
from datetime import datetime, timezone

from xrpl.wallet import Wallet as XRPLWallet

from app.database import SessionLocal
from app.models.wallet import PlatformWallet, PoolRole
from app.security.encryption import encrypt_seed
from app.services.xrpl_service import XRPLService


def _create_pool(
    db,
    xrpl: XRPLService,
    role: PoolRole,
    seed: str | None,
    skip_trustline: bool,
) -> tuple[PlatformWallet, str | None]:
    existing = (
        db.query(PlatformWallet)
        .filter(PlatformWallet.role == role)
        .first()
    )
    if existing is not None:
        print(
            f"{role.value}: already exists at "
            f"{existing.xrpl_address}, leaving it alone"
        )
        return existing, None

    if seed:
        address = XRPLWallet.from_seed(seed).classic_address
        print(f"{role.value}: adopting existing wallet {address}")
        revealed = None
    else:
        print(f"{role.value}: requesting a Testnet faucet wallet...")
        account = xrpl.create_testnet_account()
        seed, address = account["seed"], account["address"]
        revealed = seed
        print(f"{role.value}: funded {address}")

    pool = PlatformWallet(
        role=role,
        xrpl_address=address,
        xrpl_encrypted_seed=encrypt_seed(seed),
    )
    db.add(pool)
    db.flush()

    if not skip_trustline:
        # UCTUSD is an issued token, so a pool cannot hold it without a
        # line. Both pools need one: the send pool to hold
        # liquidity, the payout pool to receive it.
        print(f"{role.value}: submitting TrustSet to {xrpl.issuer}...")
        tx_hash = xrpl.establish_pool_trustline(pool)
        pool.trustline_established = datetime.now(timezone.utc)
        print(f"{role.value}: trust line established ({tx_hash})")

    return pool, revealed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--send-pool-seed",
        dest="send_seed",
        help="Adopt an existing wallet instead of using the faucet",
    )
    parser.add_argument("--payout-pool-seed", dest="payout_seed")
    parser.add_argument(
        "--skip-trustlines",
        action="store_true",
        help=(
            "Create the rows without submitting TrustSet (offline "
            "setup; settlement fails until trust lines exist)"
        ),
    )
    args = parser.parse_args()

    xrpl = XRPLService()
    db = SessionLocal()
    revealed: list[tuple[str, str]] = []
    try:
        for role, seed in (
            (PoolRole.SEND_POOL, args.send_seed),
            (PoolRole.PAYOUT_POOL, args.payout_seed),
        ):
            _pool, new_seed = _create_pool(
                db, xrpl, role, seed, args.skip_trustlines
            )
            if new_seed:
                revealed.append((role.value, new_seed))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    if revealed:
        print("\n" + "=" * 72)
        print(
            "SAVE THESE SEEDS NOW — they are stored encrypted and "
            "cannot be read back."
        )
        print("=" * 72)
        for role, seed in revealed:
            print(f"  {role:<14} {seed}")
        print("=" * 72)

    print(
        "\nPool wallets ready. Get UCTUSD liquidity into the send pool "
        "before settling anything."
    )


if __name__ == "__main__":
    main()
