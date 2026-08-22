# Performance Testing

Metrics to capture per the brief:
- API response times
- Requests processed per second
- Message-queue throughput
- RLUSD transaction processing time
- Transaction success/failure rates
- System behaviour under concurrent use

## Suggested tooling
- **Locust** (Python, fits the existing stack) — `pip install locust`
- **k6** — good if the team wants JS-based load scripts instead

## Suggested approach
1. Generate synthetic users (see `scripts/` once added) to simulate concurrent senders.
2. Run load against `/remittances/quote` and `/remittances/{id}/confirm-cash-in` separately —
   the first is pure compute, the second triggers the async settlement path.
3. Track queue depth and worker throughput during a burst of confirmations.
4. Summarise results in a small number of tables/charts in the final spec, and call out
   any bottleneck found (e.g. DB contention, XRPL Testnet rate limits, worker concurrency).

Results and raw output go in `results/` (gitignored — don't commit large log dumps).
