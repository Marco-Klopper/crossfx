# CrossFX Backend — Track 2: Settlement & Security

Operational guide for the XRPL settlement path. The *design* and its
justification live in [`../docs/technical-specification.md`](../docs/technical-specification.md)
§9 (UCTUSD Settlement Flow) and §13 (Security Design); this file is how to run it.

For Track 1's setup (venv, `.env`, `alembic`, admin user) see
[`README.md`](README.md) — do that first.

## What Track 2 owns

| Path | Purpose |
|---|---|
| `app/services/xrpl_service.py` | `XRPLService` — TrustSet, Payment, status, balances. The only class that decrypts a seed. |
| `app/services/ledger.py` | `Ledger` — the internal multi-currency ledger, the authoritative record of holdings |
| `app/services/settlement_queue.py` | `SettlementQueue` — Redis Streams publish/consume/ack |
| `app/models/wallet.py` | `Wallet`, `LedgerBalance`, `WalletTransaction`, `PlatformWallet` |
| `app/routers/wallet.py` | Recipient-facing balance + transaction history |
| `app/security/encryption.py` | Fernet encryption of pool seeds at rest |
| `worker/settlement_worker.py` | `SettlementWorker` — the settlement worker |
| `scripts/init_platform_wallets.py` | One-off creation of the two pooled corridor wallets |

## How the classes fit together

Four classes, each owning exactly one piece of state, composed by
constructor injection:

```
SettlementWorker              consumer name + stop flag
  ├── SettlementQueue         a Redis connection (lazy)
  ├── XRPLService             an XRPL node client + issuer/currency
  └── Ledger(db)              one database session
```

`SettlementWorker(queue=..., xrpl=..., session_factory=...)` defaults every
collaborator, so production code is just `SettlementWorker().run()`. The
reason they are injectable is the test suite: it drives the worker against a
fake queue and a fake XRPL node, so the settlement tests need no broker, no
Testnet, and almost no patching.

Two rules the design leans on:

- **`Ledger` never commits.** It is constructed around a session and flushes
  only, so a settlement's ledger write and the remittance status update it
  belongs with land in one transaction or not at all. The caller owns the
  boundary.
- **`XRPLService` is the only class that decrypts a seed.** Callers pass a
  `PlatformWallet` row in and get a transaction hash out. `SettlementWorker`
  therefore has no code path that can touch key material, which is what makes
  the brief's private-key requirement structural rather than a convention.

## The model in one paragraph

Users have **no XRPL accounts**. All real value sits in two pooled corridor
wallets (`platform_wallets`: `send_pool`, `payout_pool`), and what a user owns is
a row per currency in `ledger_balances`. Settling one remittance does two things:
submits a real UCTUSD `Payment` from the send pool to the payout pool on XRPL
Testnet, and credits the recipient's UCTUSD claim in the internal ledger. See §9.1
and §9.2 for why there are two pool accounts rather than one.

## Setup, after `alembic upgrade head`

### 1. Create the pooled wallets

```bash
python -m scripts.init_platform_wallets
```

Funds two Testnet accounts from the XRP faucet, submits a TrustSet from each to
`UCTUSD_ISSUER_ADDRESS`, encrypts both seeds and stores them. **It prints the seeds
once and never again** — save them before closing the terminal. Re-running is safe;
existing pools are left alone.

To adopt wallets you already have (e.g. a pool already funded with UCTUSD):

```bash
python -m scripts.init_platform_wallets --send-pool-seed sEd... --payout-pool-seed sEd...
```

### 2. Get UCTUSD liquidity into the send pool

**Nothing will settle until you do this.** The XRP faucet funds XRP for reserves
and TrustSet costs only — it does not give you the settlement token.

The class settles in **UCTUSD**, a lecturer-issued Testnet IOU
(course announcement, 2026-09-08). `.env.example` already carries the real values:

| | |
|---|---|
| Issuer | `rELez4x4Zqv3KYqboYVfrYPF8521Ycbxa5` |
| Currency code | `5543545553440000000000000000000000000000` (40-char hex — `UCTUSD` is 6 chars) |
| Distributor | `rsWPX7FKwnfk6enosumAzEuTs5Y12Steq4` |

Once step 1 has created the pools and their trust lines, **message Julian Kanjere
with the send pool's address** and he distributes the UCTUSD. Then size demo
remittances against what actually arrived. See spec §15.

Check what a pool actually holds:

```python
from app.services.xrpl_service import XRPLService
XRPLService().issued_balance("<pool address>")
```

### 3. Start Redis

```bash
docker run -p 6379:6379 redis:7-alpine
```

### 4. Run the worker

Separate process from the API:

```bash
python -m worker.settlement_worker
```

It creates its consumer group on start, replays anything it left unacked, then
blocks for new messages. `SIGINT`/`SIGTERM` finish the current message and stop.
Several workers can run at once — they share a consumer group, which is what makes
Track 4's queue-throughput benchmark meaningful.

## Publishing a settlement (Track 3's integration point)

*Now wired up: `cashin_cashout_service.queue_for_settlement`, called from both
`/remittances/{id}/confirm-cash-in` and `/admin/remittances/{id}/confirm-payment`.
See [`README-track3.md`](README-track3.md) §4 for the commit ordering and what
happens when the queue is down.*

Confirming ZAR cash-in should set the remittance to `CASH_IN_CONFIRMED` (or
`QUEUED`) and then:

```python
from app.services.settlement_queue import SettlementQueue

SettlementQueue().publish(remittance.idempotency_key, remittance.id)
```

The message carries only those two identifiers. The worker re-reads every
authoritative figure from the `Remittance` row, so a stale or tampered message
cannot change what gets settled. Publishing the same message twice is safe (§9.5).

## Crediting and debiting a balance (Track 3's cash-out)

*Now wired up: `cashin_cashout_service.request_cash_out` / `simulate_cash_out` /
`fail_cash_out`, behind `POST /wallet/cash-out` and the admin review routes.*

Every balance change must go through `app/services/ledger.py` — it writes the
balance and its audit entry together or not at all.

```python
from decimal import Decimal
from app.services.ledger import Ledger

ledger = Ledger(db)
wallet = ledger.wallet_for(user)

# Cash-out: UCTUSD out, fiat in. debit() raises InsufficientFundsError if the
# balance won't cover it, which is the "validate sufficient balance" step
# already done for you.
ledger.debit(wallet, "UCTUSD", Decimal("50"))
ledger.credit(wallet, "USD", Decimal("49.5"))
db.commit()
```

`Ledger` is constructed around one session and never commits — the caller owns
the transaction boundary, so a ledger write and the status update it belongs
with land together or not at all.

## Tests

```bash
pytest tests/test_settlement_worker.py tests/test_ledger.py \
       tests/test_settlement_queue.py tests/test_xrpl_service.py tests/test_wallet.py
```

Every XRPL call and the Redis client are mocked, so none of this needs a live
Testnet or a running broker.

| File | Covers |
|---|---|
| `test_settlement_worker.py` | The full flow: pool-to-pool payment, ledger credit, idempotency on redelivery, failure handling, the no-seed boundary |
| `test_ledger.py` | Credits, debits, multi-currency isolation, insufficient funds, external legs |
| `test_settlement_queue.py` | Consumer-group creation, publish payload, pending-vs-new reads, ack |
| `test_xrpl_service.py` | TrustSet/Payment/status, and the pooled layer's decrypt-and-sign path |
| `test_wallet.py` | `/wallet/balance` and `/wallet/transactions`, and that neither leaks key material |

## Gotchas

- **Import-time config.** `app.security.encryption` builds its `Fernet` at import
  time, so `PRIVATE_KEY_ENCRYPTION_KEY` must be a real Fernet key before anything
  imports. `tests/conftest.py` sets it before the first `import app...` — don't add
  an app import above that block.
- **Rotating `PRIVATE_KEY_ENCRYPTION_KEY` orphans the pool seeds.** They can no
  longer be decrypted, and no API can read them back. Keep the seeds
  `init_platform_wallets` printed, or you will be creating new pools.
- **A remittance stuck in `SETTLING`** means the one case that needs a human: the
  on-chain payment succeeded but the ledger write did not. Its queue message is
  deliberately left unacked. See §9.5.
- **`xrpl-py==3.0.0` constrains `httpx<0.25`**, and `httpx==0.24.1` needs `anyio<4`
  (`anyio>=4` crashes importing xrpl with `module 'anyio' has no attribute 'abc'`).
  Both are pinned in `requirements.txt`; don't bump one without the others.
