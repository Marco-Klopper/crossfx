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
- **Custodial wallet approach** (per-user XRPL account vs. pooled platform wallet) —
  state and justify the choice here (Track 2).
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
