"""
Measures the two metrics the HTTP load test cannot see: message-queue
throughput, and UCTUSD transaction processing time (the brief's
"message-queue throughput" and "RLUSD transaction processing time").

Why these are measured separately from locustfile.py: the API hands a
remittance to the queue and returns. Everything after that happens in another
process, against the XRP Ledger Testnet, at a speed the API has no control
over. Timing it through the HTTP client would measure the wrong thing, and
driving it under load would exhaust the pool's UCTUSD liquidity — which is
finite and distributed by the lecturer — within seconds.

So this reads the outcome instead, from the two places it is recorded:

  Redis   stream length and consumer-group backlog — how much work is
          queued and how much is still unacked.
  Database  per-remittance timestamps. cash_in_confirmed_at is stamped when
          the API commits; settled_at when the worker has a validated ledger.
          The gap between them IS the settlement latency, including the queue
          wait and the on-chain round trip.

Usage (from performance-testing/):

    ./.venv/bin/python measure_settlement.py
    ./.venv/bin/python measure_settlement.py --watch 30   # sample for 30s
"""
import argparse
import os
import sqlite3
import sys
import time
from collections import Counter

DEFAULT_DB = os.path.join("..", "backend", "crossfx.db")
DEFAULT_REDIS = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
STREAM = os.environ.get("SETTLEMENT_STREAM_NAME", "crossfx-settlement-queue")
GROUP = os.environ.get("SETTLEMENT_CONSUMER_GROUP", "crossfx-settlement-workers")


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = min(int(round(fraction * (len(ordered) - 1))), len(ordered) - 1)
    return ordered[index]


# -- the queue --------------------------------------------------------------


def queue_stats(url):
    """Stream length and unacked backlog, or None if Redis is unreachable."""
    try:
        import redis
    except ImportError:
        return {"error": "redis-py not installed (pip install redis)"}

    try:
        client = redis.Redis.from_url(url, decode_responses=True)
        length = client.xlen(STREAM)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    stats = {"stream_length": length, "groups": []}
    try:
        for group in client.xinfo_groups(STREAM):
            stats["groups"].append(
                {
                    "name": group.get("name"),
                    "consumers": group.get("consumers"),
                    # Delivered but not yet acked — work in flight, or work a
                    # dead worker left behind.
                    "pending": group.get("pending"),
                    "lag": group.get("lag"),
                }
            )
    except Exception:
        # The group does not exist until a worker has started once.
        pass
    return stats


def measure_queue_throughput(url, seconds):
    """
    Samples the stream length over a window to get messages published per
    second — run this while a load test is publishing.
    """
    try:
        import redis

        client = redis.Redis.from_url(url, decode_responses=True)
        start_length = client.xlen(STREAM)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    print(f"  sampling the queue for {seconds}s…")
    time.sleep(seconds)
    end_length = client.xlen(STREAM)

    published = end_length - start_length
    return {
        "window_seconds": seconds,
        "messages_published": published,
        "publish_rate_per_second": round(published / seconds, 2),
    }


# -- settlement -------------------------------------------------------------


def settlement_stats(db_path):
    """
    Settlement latency and outcome counts, straight from the remittances
    table.
    """
    if not os.path.exists(db_path):
        return {"error": f"database not found at {db_path}"}

    connection = sqlite3.connect(db_path)
    try:
        statuses = Counter(
            row[0]
            for row in connection.execute("SELECT status FROM remittances")
        )
        rows = connection.execute(
            """
            SELECT cash_in_confirmed_at, settled_at
            FROM remittances
            WHERE settled_at IS NOT NULL AND cash_in_confirmed_at IS NOT NULL
            """
        ).fetchall()
    except sqlite3.Error as exc:
        return {"error": f"{exc}"}
    finally:
        connection.close()

    latencies = []
    for confirmed_at, settled_at in rows:
        try:
            # SQLite stores these as naive ISO strings.
            from datetime import datetime

            start = datetime.fromisoformat(confirmed_at)
            end = datetime.fromisoformat(settled_at)
            latencies.append((end - start).total_seconds())
        except (TypeError, ValueError):
            continue

    # SETTLED and FAILED are both terminal outcomes the worker reached; the
    # success rate is over those, not over every row (a QUOTED remittance was
    # never submitted to anything).
    settled = statuses.get("SETTLED", 0) + statuses.get("settled", 0)
    failed = statuses.get("FAILED", 0) + statuses.get("failed", 0)
    terminal = settled + failed

    return {
        "status_counts": dict(statuses),
        "settled": settled,
        "failed": failed,
        "success_rate_percent": (
            round(100 * settled / terminal, 2) if terminal else None
        ),
        "samples": len(latencies),
        "latency_seconds": {
            "min": round(min(latencies), 3) if latencies else None,
            "median": round(percentile(latencies, 0.50), 3) if latencies else None,
            "p95": round(percentile(latencies, 0.95), 3) if latencies else None,
            "max": round(max(latencies), 3) if latencies else None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--redis", default=DEFAULT_REDIS)
    parser.add_argument(
        "--watch",
        type=int,
        default=0,
        help="sample queue throughput over this many seconds",
    )
    args = parser.parse_args()

    print("=" * 62)
    print("Message queue")
    print("=" * 62)
    stats = queue_stats(args.redis)
    if "error" in stats:
        print(f"  unavailable: {stats['error']}")
        print("  (start Redis, then re-run — see backend/README-track2.md)")
    else:
        print(f"  stream {STREAM}")
        print(f"  messages in stream    {stats['stream_length']}")
        for group in stats["groups"]:
            print(
                f"  group {group['name']}: consumers={group['consumers']} "
                f"pending={group['pending']} lag={group['lag']}"
            )
        if not stats["groups"]:
            print("  no consumer group yet — the worker has never run")

    if args.watch:
        print()
        throughput = measure_queue_throughput(args.redis, args.watch)
        if "error" in throughput:
            print(f"  unavailable: {throughput['error']}")
        else:
            print(
                f"  published {throughput['messages_published']} messages in "
                f"{throughput['window_seconds']}s "
                f"= {throughput['publish_rate_per_second']}/s"
            )

    print()
    print("=" * 62)
    print("UCTUSD settlement (cash_in_confirmed_at -> settled_at)")
    print("=" * 62)
    settlement = settlement_stats(args.db)
    if "error" in settlement:
        print(f"  unavailable: {settlement['error']}")
        return 1

    print(f"  remittances by status  {settlement['status_counts']}")
    print(f"  settled                {settlement['settled']}")
    print(f"  failed                 {settlement['failed']}")
    rate = settlement["success_rate_percent"]
    print(
        f"  success rate           "
        f"{f'{rate}%' if rate is not None else 'n/a (nothing terminal yet)'}"
    )

    if settlement["samples"] == 0:
        print(
            "\n  No settled remittances to time yet. Settlement needs Redis, the\n"
            "  pooled wallets (scripts/init_platform_wallets), the worker\n"
            "  running, and UCTUSD liquidity in the send pool."
        )
    else:
        latency = settlement["latency_seconds"]
        print(f"  samples                {settlement['samples']}")
        print(f"  median latency         {latency['median']}s")
        print(f"  95th percentile        {latency['p95']}s")
        print(f"  min / max              {latency['min']}s / {latency['max']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
