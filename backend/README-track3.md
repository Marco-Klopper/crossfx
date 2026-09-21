# CrossFX Backend — Track 3: FX, Fees & Remittance Flow

The sender's journey and the recipient's payout: pricing a send, taking the rand
in, handing settlement to Track 2's queue, and turning the settled UCTUSD back
into fiat.

Track 1 (`README.md`) is identity and data. Track 2 (`README-track2.md`) is XRPL
settlement and security. This file is the third: **what happens between them**.

Spec sections: [§4 Fee Model](../docs/technical-specification.md#4-fee-model),
§5 Exchange-Rate Calculation, §6 Remittance Limits, §8 Cash-In Flow,
§10 Cash-Out Flow.

---

## 1. What Track 3 owns

| Path | What |
|---|---|
| `app/services/fx_rate_service.py` | USD/ZAR rate, three interchangeable sources |
| `app/services/fee_service.py` | Fee, margin and payout arithmetic; the `Quote` |
| `app/services/limits_service.py` | Which remittances consume a sender's limits, and over what window |
| `app/services/cashin_cashout_service.py` | The simulated rails and both state machines |
| `app/models/remittance.py` | `Remittance`, `RemittanceStatus`, `CashOut`, `CashOutStatus` |
| `app/models/fx_rate.py` | Pinned rates for `EXCHANGE_RATE_SOURCE=table` |
| `app/schemas/remittance.py` | The request/response contract Track 4 builds against |
| `app/routers/remittances.py` | `POST /quote`, `POST /{id}/confirm-cash-in`, `GET /`, `GET /{id}` |
| `app/routers/wallet.py` → cash-out | `POST /wallet/cash-out`, `GET /wallet/cash-outs[/{id}]` |
| `app/routers/admin.py` → two bodies | `confirm-payment`, `cash-outs/{id}/approve` and `/reject` |
| `migrations/versions/b7f4c9e21d08_*.py` | `cash_outs`, `fx_rates`, quote expiry |
| `scripts/seed_fx_rates.py`, `scripts/walkthrough_remittance.py` | Pin a rate; drive the whole journey |

Two endpoints live in Track 1's `admin.py` and one in Track 2's `wallet.py`.
They are gated by their owning track and implemented here, which is what their
original docstrings said would happen.

---

## 2. The journey, end to end

```
POST /remittances/quote                     -> a QUOTED remittance, priced and held
POST /remittances/{id}/confirm-cash-in      -> CASH_IN_CONFIRMED, then QUEUED
   (Track 2's worker settles it)            -> SETTLED, recipient credited in UCTUSD
POST /wallet/cash-out                       -> UCTUSD debited, cash-out REQUESTED
POST /admin/cash-outs/{id}/approve          -> fiat credited, cash-out COMPLETED
```

Run it against a live server and watch every request and response:

```bash
uvicorn app.main:app --reload &
python -m scripts.create_admin --email admin@example.com --password adminpass123
python -m scripts.walkthrough_remittance
```

The script runs the sender's half unconditionally. The cash-out half needs
something to have settled, so start Redis, the pooled wallets and the worker
first if you want all ten steps:

```bash
docker run -p 6379:6379 redis:7-alpine
python -m scripts.init_platform_wallets
python -m worker.settlement_worker
```

Without Redis the confirm step still succeeds — it returns `queued: false` and
leaves the remittance republishable. That is deliberate; see §4 below.

---

## 3. Pricing, in one place

`fee_service.calculate_quote` is pure arithmetic: no database, no network, no
clock. Everything it needs is passed in, which is why it is trivially testable
and cheap enough to sit on the endpoint the load tests hammer.

```python
from decimal import Decimal
from app.services.fee_service import calculate_quote
from app.services.fx_rate_service import get_usd_zar_rate

quote = calculate_quote(Decimal("1000.00"), get_usd_zar_rate(db), "USD")
quote.transaction_fee_zar   # Decimal('40.00')   R25 fixed + 1.50%
quote.fx_margin_zar         # Decimal('10.00')   1.00%
quote.net_converted_zar     # Decimal('950.00')
quote.uctusd_amount         # Decimal('51.351351')  at 18.50 mid-market
quote.effective_rate        # Decimal('19.473684')  all-in ZAR per UCTUSD
```

**The margin is charged once.** An earlier version of this module deducted
`fx_margin_zar` *and* converted the remainder at a marked-up rate, so the
customer paid the spread twice and the disclosed margin line was untrue. Fixed,
and pinned by `test_fee_service.py::test_margin_is_charged_once_not_twice`.

Fiat rounds half-up to 2dp; anything credited to a customer rounds **down** to
the column's precision. Under pooled custody the payout pool has to cover every
claim in the ledger (spec §9.6), so a fraction rounded the customer's way is a
shortfall the platform funds.

### Exchange rates

`get_usd_zar_rate(db)` — one function, three sources, chosen by
`EXCHANGE_RATE_SOURCE`:

- `mock` (default) — deterministic drift around `FX_MOCK_BASE_RATE`, derived from
  a hash of the current time bucket. It moves, but it is a pure function of the
  clock, so two workers agree and a test can pin it. `FX_MOCK_VOLATILITY_BPS=0`
  flattens it for a scripted demo.
- `api` — a keyless public endpoint, cached for `FX_RATE_CACHE_SECONDS`, falling
  back to the last good rate if a refresh fails. Cached because quoting is
  supposed to be pure compute, not a measurement of someone else's API.
- `table` — the most recent `fx_rates` row: `python -m scripts.seed_fx_rates
  --rate 18.50`.

All three raise `FxRateUnavailableError`, which the routers map to `503`. A quote
with a guessed rate would be worse than no quote.

---

## 4. Cash-in, and why a dead queue is not an error

Confirming cash-in does two things in two commits, in this order:

1. `status → CASH_IN_CONFIRMED`, commit;
2. `SettlementQueue.publish(idempotency_key, id)`, `status → QUEUED`, commit.

Publishing first would let a crash in between leave a message pointing at a
remittance the worker will refuse to claim — a settlement lost silently. This
ordering can only produce the harmless opposite.

So when Redis is down the endpoint returns **200 with `queued: false`**, not a
503. The cash-in genuinely was confirmed; telling the sender their request failed
invites them to pay in twice. The remittance sits in `CASH_IN_CONFIRMED`, which
is one of the two statuses `settlement_worker.CLAIMABLE` accepts, and

```
POST /admin/remittances/{id}/confirm-payment
```

republishes it once the queue is back. Republishing is safe: the worker claims
each remittance exactly once (spec §9.5).

**The recipient must already have a CrossFX account.** Checked at cash-in, not at
quote time — quoting is only pricing, but taking a sender's cash for a transfer
that provably cannot land is worth preventing. `409` with the contact in the
message.

---

## 5. Cash-out

A `CashOut` belongs to a **user, not a remittance**: by the time value is cashed
out it has been pooled into one ledger balance, and asking which remittance a
given rand came from is a question the ledger cannot answer.

The UCTUSD is debited when the cash-out is **requested**, not when it is
approved. Otherwise a recipient could open three cash-outs against one balance
and have all three approved, each individually valid when it was checked.
`Ledger.debit` refusing to go below zero *is* the "validate sufficient balance"
step — there is no second check to get out of step with it.

A rejection refunds by writing a fresh `incoming` entry, never by deleting the
debit: `wallet_transactions` is an immutable audit trail (spec §9.4).

Payout currencies are **USD and ZAR** — narrower than the list a sender may elect
for a beneficiary, because a currency needs both a ledger slot
(`SUPPORTED_CURRENCIES`) and a rate. UCTUSD is excluded: cashing out into the
token you already hold is a no-op.

Cash-out requires authentication but **not** approved KYC. Gating it would make a
recipient unable to touch money that is already theirs; in a real corridor those
checks belong to the payout partner. Recorded as a gap in spec §14/§15.

---

## 6. Limits

`limits_service.assert_within_limits(db, user, amount)` is the enforcement point.
Two decisions live there because nothing else makes them:

- **The window is a South African calendar day and month** (fixed UTC+02:00 — SAST
  has never observed DST, and a fixed offset needs no `tzdata` on Windows). A UTC
  window would reset the daily limit at 02:00 local.
- **Live quotes hold headroom; expired quotes and failed remittances do not.**
  That is what makes persisting a quote as a `QUOTED` row load-bearing: the row
  *is* the reservation, and `quote_expires_at` returns it if nobody funds it.

The daily limit is checked first — it is the tighter one, and "come back
tomorrow" is actionable in a way that "come back next month" is not. A breach is
a `403` naming the period, the limit and the remaining headroom, and every quote
carries the same four figures in its `limits` block.

---

## 7. Configuration

Everything is a setting; nothing is a constant (the brief requires fees and
limits to be configurable). See `.env.example`:

| Setting | Default | Meaning |
|---|---|---|
| `FIXED_REMITTANCE_FEE_ZAR` | 25 | Flat fee per remittance |
| `PERCENT_FEE_BPS` | 150 | 1.50% of the send amount |
| `FX_MARGIN_BPS` | 100 | 1.00% spread, charged once |
| `CASHOUT_FEE_BPS` | 100 | 1.00% of the UCTUSD cashed out |
| `EXCHANGE_RATE_SOURCE` | `mock` | `mock` \| `api` \| `table` |
| `FX_MOCK_BASE_RATE` / `FX_MOCK_VOLATILITY_BPS` | 18.50 / 150 | The mock's centre and drift |
| `FX_API_URL` / `FX_API_RATE_PATH` | open.er-api.com | Provider and dotted path to the rate |
| `FX_RATE_CACHE_SECONDS` | 300 | How long a fetched rate is reused |
| `QUOTE_TTL_MINUTES` | 15 | How long a quote is honoured |
| `VERIFIED_DAILY_LIMIT` / `VERIFIED_MONTHLY_LIMIT` | 3000 / 25000 | ZAR ceilings once KYC is approved |
| `UNVERIFIED_DAILY_LIMIT` / `UNVERIFIED_MONTHLY_LIMIT` | 0 / 0 | Zero, so KYC is a consequence of the limit model |

---

## 8. Tests

```bash
pytest -v
```

No Postgres, no Redis, no XRPL — the queue is faked by the `fake_queue` /
`broken_queue` fixtures in `conftest.py`, and everything else runs against the
per-test in-memory SQLite database Track 1 set up.

| File | Covers |
|---|---|
| `test_fee_service.py` | The worked example, the margin-charged-once regression, rounding, payout pricing, limit arithmetic |
| `test_fx_rate_service.py` | All three sources, the cache, the stale-rate fallback, malformed responses |
| `test_limits_service.py` | The SAST window boundary, which statuses consume headroom, expiry release |
| `test_remittances.py` | Quote, cash-in, the broken-queue branch, status, history, ownership |
| `test_cash_out.py` | Reserve-on-request, approval, rejection and refund, the admin queue |
| `test_admin.py` | The cash-in confirmation hook and the republish retry |

New fixtures worth reusing: `approved_auth_headers` (a KYC-approved sender —
the plain `auth_headers` user gets a `403` from every Track 3 sender route),
`beneficiary_factory`, `quote_factory` (posts a real quote and hands back the
body), and `fake_queue`, whose `fail_with` can be toggled mid-test.

---

## 9. Gotchas

- **`auth_headers` is not KYC-approved.** Use `approved_auth_headers` for
  anything under `/remittances/*`.
- **Timestamps in the database are naive UTC.** `limits_service` reduces every
  value it compares the same way; a tz-aware datetime in a `WHERE` clause against
  these columns is a silently wrong comparison, not an error.
- **The mock rate moves.** Pin `FX_MOCK_VOLATILITY_BPS=0` in any test that
  asserts an exact UCTUSD figure.
- **`Numeric(18, 6)` on `cash_outs.payout_amount`** means a rand payout renders as
  `940.490000`. The value is exact; the column is shared across currencies.
- **Deleting a beneficiary with remittances is now a `409`.** The settlement
  worker resolves the recipient through that row.
