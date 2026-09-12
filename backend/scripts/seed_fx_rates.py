"""
Pins a USD/ZAR rate for EXCHANGE_RATE_SOURCE=table (spec §5).

Run from backend/, after `alembic upgrade head`:

    python -m scripts.seed_fx_rates --rate 18.50

Each run appends a row rather than editing the last one, so a quote can
always be explained afterwards by the rate that was current when it was
issued. The `table` source reads the most recent `effective_from`.

Use this when a demo or a marking session needs a fixed, known rate:
`mock` moves (slowly, but it moves) and `api` needs the internet.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from app.database import SessionLocal
from app.models.fx_rate import FxRate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rate",
        required=True,
        help="ZAR per 1 USD, e.g. 18.50",
    )
    parser.add_argument(
        "--source",
        default="seed_fx_rates",
        help="A note about where the rate came from",
    )
    args = parser.parse_args()

    try:
        rate = Decimal(args.rate)
    except InvalidOperation:
        raise SystemExit(f"--rate must be a number, got {args.rate!r}")
    if rate <= 0:
        raise SystemExit("--rate must be positive")

    db = SessionLocal()
    try:
        row = FxRate(
            currency_pair=FxRate.USD_ZAR,
            rate=rate,
            effective_from=datetime.now(timezone.utc),
            source=args.source,
        )
        db.add(row)
        db.commit()
        print(f"Pinned {FxRate.USD_ZAR} = {rate} (effective {row.effective_from:%Y-%m-%d %H:%M:%SZ})")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(
        "Set EXCHANGE_RATE_SOURCE=table in .env for quotes to use it."
    )


if __name__ == "__main__":
    main()
