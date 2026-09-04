# CrossFX — Business and Technical Specification

*ECO5040W — Financial Software Engineering, UCT — Group 4*
*Ndumiso Zondi (ZNDNDU007) · Marco Klopper (KLPMAR012) · Muki Mdluli (MDLMUK001) · Rafaela Stevenson (STVRAF001)*

> Target length: ~10–15 pages. Fill in each section below as the design solidifies.
> Due alongside the check-in on 18 September.

## 1. Business Problem

## 2. User Journey

## 3. Functional Requirements

## 4. Fee Model

## 5. Exchange-Rate Calculation

## 6. Remittance Limits

## 7. Architecture Diagram

## 8. Cash-In Flow

## 9. RLUSD Settlement Flow

*Written by Track 2 (Settlement & Security).*

### 9.1 Custodial model: one pooled platform wallet

CrossFX settles through a single custodial XRPL Testnet wallet
(`PLATFORM_WALLET_ADDRESS`/`PLATFORM_WALLET_SEED`), not a separate on-chain account per
user. `wallets.rlusd_balance` (`app/models/wallet.py`) is the authoritative internal
sub-ledger of what each user owns; `wallets.xrpl_address`/`xrpl_encrypted_seed` stay
`null` for every user row — they exist in the schema only for a future per-user-account
mode, unused here.

This mirrors how MoneyGram and Western Union actually operate: neither spins up a
dedicated bank account per customer. Both hold pooled/FBO corporate accounts and track
individual balances in an internal ledger layer, moving real money between institutions
only in bulk. Doing the same on top of RLUSD/XRPL avoids per-user Testnet funding and
trustline setup (the RLUSD Testnet faucet is capped at ~$10/24h per wallet — see
`app/services/xrpl_service.py`), and it concentrates custody risk into exactly one seed
rather than N.

### 9.2 Two settlement timelines

"Settled" happens in two different places, on two different clocks:

| | User-Facing Virtual Settlement | Physical Treasury Settlement |
|---|---|---|
| What moves | `wallets.rlusd_balance` (a ledger row) | Real RLUSD on XRPL, platform wallet |
| Trigger | Cash-in confirmed → settlement queue message | Periodic batch (manually triggered for this demo) |
| Latency | Milliseconds | Hours/days in a real network; on-demand here |
| Component | `worker/settlement_worker.py` | Treasury batch job (new, §9.4) |
| User-visible? | Yes — this is what "settled" means to the sender/recipient | No — back-office only |

The recipient's balance and the `Remittance.status` the API exposes are driven entirely
by the ledger leg. The on-chain leg happens later and separately, netted across
whatever remittances are pending — this is what lets the product feel instant
("clears in minutes") without an XRPL round-trip on the critical path, same as the
netting/settlement pattern real money-transmitter networks use.

### 9.3 User-facing virtual settlement (per remittance)

1. Track 3's cash-in confirmation (`routers/admin.py::confirm_zar_payment` or
   `routers/remittances.py::confirm_cash_in`) publishes a settlement message to the
   queue (`SETTLEMENT_STREAM_NAME`), keyed by `Remittance.idempotency_key`.
2. `worker/settlement_worker.py::consume_settlement_queue` reads the message.
3. `process_settlement_message`, in one DB transaction:
   - Skips if `idempotency_key` was already processed (redelivery safety).
   - Loads the `Remittance`.
   - Credits the recipient's `wallets.rlusd_balance` by `Remittance.rlusd_amount`.
   - Writes a `WalletTransaction` (`direction=incoming`, `status=success`).
   - Sets `Remittance.status = SETTLED`, `settled_at = now()`.
   - On any failure: `Remittance.status = FAILED`, no wallet credit, reason logged
     (never the decrypted seed — there's nothing to decrypt on this path, since it
     never calls `xrpl_service`).

No XRPL call happens in this flow. `Remittance.xrpl_tx_hash` stays `null` until the
treasury batch below fills it in — the recipient can already see `SETTLED` and request
a cash-out before the corresponding on-chain movement exists.

### 9.4 Physical treasury settlement (batched)

A separate job — not on the request path, not run by `settlement_worker.py` — nets
accumulated exposure and clears it on-chain:

1. Query `Remittance` rows where `status = SETTLED and treasury_settled_at is null`.
2. Sum `rlusd_amount` across them (the net outstanding exposure since the last batch).
3. Call `xrpl_service.send_rlusd_payment` once, from the platform wallet, for that net
   amount. This is the only point in the system where the platform seed is decrypted
   (via `app.security.encryption.decrypt_seed`) and used to sign.
4. On `tesSUCCESS`: stamp every `Remittance` included in the batch with the same
   `xrpl_tx_hash`, a shared `treasury_batch_id`, and `treasury_settled_at = now()`.
   Several remittances legitimately sharing one `xrpl_tx_hash` is expected — it's the
   on-chain evidence for a batch, not a 1:1 receipt per remittance.
5. On failure: nothing is stamped, so the batch simply retries (or grows) next run —
   `treasury_settled_at is null` is the retry queue, no separate failure state needed.

For this prototype the "schedule" is a manually-triggered CLI run during the demo,
showing a batch of virtual-settled remittances clear together in a single Testnet
transaction. A production version would run this on a timer (see §15).

### 9.5 Idempotency and failure isolation

Two independent dedup keys, one per timeline: `idempotency_key` guards the ledger leg
against a redelivered queue message crediting a wallet twice; `treasury_settled_at`
guards the on-chain leg against double-submitting the same net exposure. A failure in
one timeline never blocks or corrupts the other — a recipient's balance is correct and
spendable the instant virtual settlement completes, independent of whether or when the
treasury batch clears.

## 10. Cash-Out Flow

## 11. Database Design

*Written by Track 1 (Identity & Data). Full detail lives in
[`backend/README.md`](../backend/README.md); this is the spec-facing summary.*

### Entity-relationship overview

```
users ──┬──< kyc_applications  (user_id FK; reviewed_by_admin_id FK, nullable)
        └──< beneficiaries     (sender_id FK; unique on (sender_id, contact))
```

Tracks 2 and 3 add `wallets` / `wallet_transactions` (one-to-one and one-to-many off
`users`) and `remittances` (many-to-one off both `users` and `beneficiaries`) —
included in the same initial migration for a single shared baseline, but designed and
owned by those tracks.

### Tables (Track 1's four)

| Table | Purpose | Key constraints |
|---|---|---|
| `users` | Account, credentials, KYC status, admin flag | `email` unique+indexed; `kyc_status` enum (not_started/pending/approved/rejected); `is_admin` bool |
| `kyc_applications` | One mock KYC submission per review cycle | FK `user_id → users.id`; FK `reviewed_by_admin_id → users.id` (nullable); `status` enum (pending/approved/rejected) |
| `beneficiaries` | Recipients a sender has registered | FK `sender_id → users.id`; `UNIQUE(sender_id, contact)` — stops duplicate recipient entries |
| (migration also creates `wallets`, `wallet_transactions`, `remittances` — Tracks 2/3) | | |

### Design decisions

- **Two status enums, not one.** `User.kyc_status` (`KYCStatus`) includes a
  `NOT_STARTED` member for a user who has never applied; `KYCApplication.status`
  (`ApplicationStatus`) does not, because an application row only ever exists once
  submitted. `routers/admin.py`'s approve/reject endpoints are what keep the two
  columns in sync — approving an application also flips the owning user's
  `kyc_status`, in the same DB transaction.
- **Portable UUID primary keys.** Every table uses `sqlalchemy.Uuid` rather than the
  Postgres-only `sqlalchemy.dialects.postgresql.UUID`. `Uuid` compiles to a native
  `uuid` column on Postgres and a `CHAR(32)` column on SQLite, so the exact same models
  and Alembic migrations run against either backend — which is what lets local
  development and CI run entirely on SQLite with zero infrastructure while a deployed
  environment still points `DATABASE_URL` at Postgres.
- **`UNIQUE(sender_id, contact)` on beneficiaries**, not a global unique on `contact` —
  two different senders are allowed to send to the same recipient contact; one sender
  registering the same contact twice is the accidental-duplicate case being guarded
  against.
- **Admin is a boolean column, not a separate table or claim.** Simpler than a roles
  table for a prototype with exactly one elevated role; no API route can set it (see
  `backend/scripts/create_admin.py`), so it can't be self-granted by a compromised
  account.
- **Schema is managed by Alembic, not `create_all`.** `backend/migrations/` holds the
  migration history; `backend/migrations/env.py` reads the DB URL from
  `app.config.settings` so there is one source of config truth rather than a second
  copy in `alembic.ini`.

## 12. API Overview

*Auth/KYC/beneficiary/admin rows below are Track 1's; Track 4 owns filling in the rest
of this table (remittances, wallet) as those endpoints land.*

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/register` | — | Create a sender account |
| POST | `/auth/login` | — | JSON credential login, returns a JWT |
| POST | `/auth/token` | — | OAuth2-form login (Swagger's Authorize button only) |
| POST | `/auth/logout` | — | Client-side token discard (stateless JWTs, nothing to revoke) |
| GET | `/auth/me` | user | Profile, KYC status, applicable transaction limits |
| POST | `/kyc/apply` | user | Submit a mock KYC application |
| GET | `/kyc/status` | user | Current KYC status + latest application |
| POST | `/beneficiaries/` | user | Register a recipient |
| GET | `/beneficiaries/` | user | List the caller's own recipients |
| GET, DELETE | `/beneficiaries/{id}` | user | Read/remove a recipient (404, not 403, if it isn't the caller's) |
| GET | `/admin/kyc/applications` | admin | KYC review queue |
| POST | `/admin/kyc/{id}/approve`, `/reject` | admin | Review a KYC application |
| POST | `/admin/remittances/{id}/confirm-payment` | admin | *Admin-gated here; body implemented by Track 3* |
| POST | `/admin/cash-outs/{id}/approve` | admin | *Admin-gated here; body implemented by Track 3* |
| — | `/remittances/*`, `/wallet/*` | — | Owned by Tracks 2/3 — see their sections |

## 13. Security Design

- **Password hashing:** bcrypt via `passlib` (`app/security/hashing.py`).
- **Private key encryption at rest**, key stored separately from the DB — see Track 2's
  section for the wallet-seed specifics; the mechanism (`app/security/encryption.py`,
  Fernet) is already in place.
- **Custodial wallet approach: one pooled platform wallet**, not a per-user XRPL
  account — see §9.1 for the full justification (matches the MoneyGram/Western Union
  pattern of pooled/FBO accounts plus an internal ledger). This concentrates custody
  risk into exactly one seed rather than N: compromise of the platform seed threatens
  every user's funds, so it is decrypted (`app.security.encryption.decrypt_seed`) in
  exactly one place in the codebase — the treasury batch job (§9.4) — and never on a
  per-request or per-user-action path, minimising both decrypt frequency and blast
  radius.
- **Decoupled settlement timelines** (§9.2) as a security boundary, not just a
  performance one: the user-facing ledger credit (`worker/settlement_worker.py`) never
  touches the platform seed or `xrpl_service`, so a bug or compromise in the
  high-traffic per-remittance path cannot reach the one component authorised to move
  real funds on-chain.
- **Authentication:** stateless JWTs (`HS256`), 60-minute expiry, `sub`/`iat`/`exp`
  claims only. No refresh tokens and no server-side revocation — a limitation, recorded
  in §15, not hidden.
- **No user-enumeration via login.** `/auth/login` returns the identical
  `401 {"detail": "Incorrect email or password"}` whether the email doesn't exist or
  the password is wrong, so failed logins can't be used to discover which emails are
  registered.
- **Authorization boundary returns 404, not 403.** Reading or deleting another sender's
  beneficiary returns 404 — a 403 would confirm the record exists under someone else's
  account, which is itself information disclosure.
- **Admin access** is a boolean flag on `User`, checked by a single
  `app.dependencies.require_admin` dependency on every admin route. No API endpoint can
  grant it — it's set only via a local CLI script — so a compromised regular account
  cannot self-promote.
- **KYC PII is not encrypted at rest** in this prototype (see §15) — identification
  numbers and addresses are plain columns. Flagged as a gap against POPIA rather than
  assumed away; see §14.

## 14. Regulatory Considerations

- KYC / AML
- Transaction monitoring
- Customer transaction limits
- Protection of customer information (POPIA)
- Custody of crypto assets
- Stablecoin / crypto-asset regulation
- Foreign-exchange and capital-flow controls (SARB / Exchange Control Regulations)
- Consumer protection
- Safeguarding of customer funds
- Licensing considerations for a real remittance service (e.g. FSCA, NPS Act, Reserve Bank authorisation)

## 15. Assumptions and Limitations
