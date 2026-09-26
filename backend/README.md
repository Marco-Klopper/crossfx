# CrossFX Backend — Track 1: Identity & Data

This README documents **Track 1** of the CrossFX backend (see
[`../docs/work-split.html`](../docs/work-split.html) for how the four tracks divide up
the project): authentication, mock KYC, beneficiary management, the admin router, and
the database schema underneath all of it.

## 1. What Track 1 is — and isn't

Track 1 owns:

- `app/routers/auth.py`, `kyc.py`, `beneficiaries.py`, `admin.py`
- `app/models/user.py`, `kyc.py`, `beneficiary.py` (and the portable-UUID/relationship
  fixes applied to `wallet.py`/`remittance.py` — see §6)
- `app/schemas/`, `app/dependencies.py`, `app/security/jwt.py`
- `migrations/` (Alembic) and this README

It does **not** own, and does not implement:

- Wallets, XRPL, trust lines, the settlement worker (**Track 2** —
  `app/services/xrpl_service.py`, `app/models/wallet.py`, `worker/`)
- FX rates, fees, quotes, the remittance flow, cash-in/cash-out (**Track 3** —
  `app/services/fx_rate_service.py`, `fee_service.py`, `limits_service.py`,
  `cashin_cashout_service.py`, `app/routers/remittances.py`, `wallet.py`; see
  [`README-track3.md`](README-track3.md))
- The frontend, load testing, most of the spec (**Track 4**)

The cash-in and cash-out endpoints in `admin.py` are gated with the same admin check as
the rest of the router, but their bodies belong to Track 3's domain
(remittances/cash-outs) and were implemented there — they just happen to live in Track
1's file. Track 3 also added the `409` on deleting a beneficiary that has remittances
against it, since it is their rows that make the guard necessary.

## 2. Setup

Requires Python 3.10+ (`app/config.py` uses PEP 604 `str | None` union syntax, which
doesn't parse on 3.9).

```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

Then fill in two values in `.env` that **must** be real before anything will import
(both crash at module-import time if left as placeholders):

```bash
# A real Fernet key for PRIVATE_KEY_ENCRYPTION_KEY — app/security/encryption.py
# builds a Fernet(...) object at import time, so a placeholder crashes on import.
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# A real random string for SECRET_KEY (JWT signing key) — doesn't have to be a
# Fernet key, just needs to not be "change-me-jwt-secret".
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

For local dev, uncomment the SQLite line in `.env` and comment out the Postgres one —
models use the portable `sqlalchemy.Uuid` type (see §6), so SQLite genuinely works now:

```
DATABASE_URL=sqlite:///./crossfx.db
```

**Gotcha:** `passlib==1.7.4` (pinned in `requirements.txt`) breaks under `bcrypt>=4.1`
(`AttributeError: module 'bcrypt' has no attribute '__about__'`). `requirements.txt`
already pins `bcrypt==4.0.1` to avoid this — don't upgrade that pin without checking.

**Another pre-existing pin conflict, fixed alongside Track 1's own setup:**
`xrpl-py==3.0.0` (Track 2's dependency) requires `httpx<0.25`, but `httpx==0.27.2` was
pinned for `TestClient`. `requirements.txt` now pins `httpx==0.24.1` to satisfy both —
flag it to Track 2 if a future `xrpl-py` upgrade changes that constraint.
`psycopg2-binary` is also bumped to `2.9.10` (from `2.9.9`), which has no Windows/Python
3.13 wheel and fails to build from source.

## 3. Run it

```bash
alembic upgrade head              # creates the schema — see §9
uvicorn app.main:app --reload
python -m scripts.create_admin --email admin@example.com --password adminpass123
```

That is everything Track 1's endpoints need. Bringing the *settlement* path up as
well (pooled wallets, Redis, the worker) is covered in
[`README-track2.md`](README-track2.md).

Open `http://127.0.0.1:8000/docs`. Click **Authorize**, log in with the admin
credentials above (that form posts to `/auth/token`, a thin Swagger-only shim over the
same check `/login` uses), and every endpoint becomes clickable.

**Email gotcha:** don't use `@something.test` for test accounts. Pydantic's `EmailStr`
rejects RFC 2606 reserved TLDs (`.test`, `.example`, `.invalid`, `.localhost`) and the
three reserved `example.com`/`.net`/`.org` domains specifically, as a syntax-level check
— it does **not** do a live DNS lookup (Pydantic explicitly passes
`check_deliverability=False`), so this isn't a network dependency, just a naming trap.
`admin@example.com`, `alice@example.com`, etc. all work fine; `admin@crossfx.test`
does not.

## 4. Golden path (copy-pasteable)

Assumes the server is running on `:8000` and you've created the admin user above.

```bash
# 1. Register
curl -s -X POST localhost:8000/auth/register -H "Content-Type: application/json" \
  -d '{"email":"alice@example.com","password":"password123","full_name":"Alice Sender"}'
# -> 201 {"id": "...", "email": "alice@example.com", "kyc_status": "not_started", ...}

# 2. Log in
curl -s -X POST localhost:8000/auth/login -H "Content-Type: application/json" \
  -d '{"email":"alice@example.com","password":"password123"}'
# -> 200 {"access_token": "...", "token_type": "bearer", "expires_in": 3600}
TOKEN=paste-the-access_token-here

# 3. Check /me -- unverified, zero limits
curl -s localhost:8000/auth/me -H "Authorization: Bearer $TOKEN"
# -> 200 {"kyc_status": "not_started", "limits": {"daily_limit_zar": 0.0, ...}}

# 4. Apply for KYC
curl -s -X POST localhost:8000/kyc/apply -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" -d '{
    "full_name":"Alice Sender","date_of_birth":"1990-05-15","nationality":"South African",
    "identification_number":"9005155800086","residential_address":"1 Long St, Cape Town",
    "mobile_number":"+27821234567","email":"alice@example.com","source_of_funds":"Salary"}'
# -> 201 {"id": "the-application-id", "status": "pending", ...}

# 5. Log in as admin, approve the application
# ADMIN_TOKEN comes from logging in as admin@example.com the same way as step 2
curl -s -X POST localhost:8000/admin/kyc/the-application-id/approve \
  -H "Authorization: Bearer $ADMIN_TOKEN"
# -> 200 {"status": "approved", "reviewed_at": "...", ...}

# 6. Add a beneficiary
curl -s -X POST localhost:8000/beneficiaries/ -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" -d '{
    "full_name":"Bob Recipient","contact":"bob@example.com","country":"United States",
    "preferred_payout_currency":"usd","relationship_to_sender":"Brother"}'
# -> 201 {"preferred_payout_currency": "USD", ...}   (normalized upper-case)

# 7. /me again -- now approved, real limits
curl -s localhost:8000/auth/me -H "Authorization: Bearer $TOKEN"
# -> 200 {"kyc_status": "approved", "limits": {"daily_limit_zar": 3000.0, "monthly_limit_zar": 25000.0}}
```

Or run the whole thing in one go and watch it execute:

```bash
python -m scripts.walkthrough
```

## 5. Endpoint reference

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/auth/register` | — | 201, 409 on duplicate email, 422 on weak password |
| POST | `/auth/login` | — | JSON body; 401 with a **generic** message for both wrong email and wrong password |
| POST | `/auth/token` | — | Swagger-only OAuth2 form shim over the same check as `/login` |
| POST | `/auth/logout` | — | 200; stateless JWTs, nothing to revoke server-side |
| GET | `/auth/me` | user | Profile + `kyc_status` + limits (no wallet balance — that's Track 2) |
| PATCH | `/auth/me` | user | Update name and/or email |
| POST | `/kyc/apply` | user | 201, 409 if a pending/approved application already exists, 422 if under 18 |
| GET | `/kyc/status` | user | Current status + latest application |
| POST | `/beneficiaries/` | user | 201, 409 on duplicate `(sender, contact)`, 422 on unsupported currency |
| GET | `/beneficiaries/` | user | Scoped to the caller |
| GET/DELETE | `/beneficiaries/{id}` | user | **404** (not 403) if the id belongs to another sender |
| GET | `/admin/kyc/applications?status=` | admin | Review queue, optional status filter |
| POST | `/admin/kyc/{id}/approve` | admin | 409 if not currently pending |
| POST | `/admin/kyc/{id}/reject` | admin | Body: `{"reason": "..."}` (optional) |
| DELETE | `/beneficiaries/{id}` | user | **409** if the beneficiary has remittances against it (Track 3) |
| POST | `/admin/remittances/{id}/confirm-payment` | admin | Gated here, implemented by Track 3 — mock cash-in confirmation and republish retry |
| POST | `/admin/cash-outs/{id}/approve`, `/reject` | admin | Gated here, implemented by Track 3 — payout review |
| GET | `/admin/cash-outs?status=` | admin | Payout queue (Track 3) |

Track 3's own routes (`/remittances/*`, `/wallet/cash-out`) are documented in
[`README-track3.md`](README-track3.md); Track 2's in
[`README-track2.md`](README-track2.md).

## 6. Data model

```
users ──┬──< kyc_applications  (user_id FK; reviewed_by_admin_id FK, nullable)
        └──< beneficiaries     (sender_id FK; unique on (sender_id, contact))
```

- **`users`** — `email` (unique), `hashed_password`, `kyc_status` (enum:
  not_started/pending/approved/rejected), `is_admin` (bool — no API route sets this,
  see `scripts/create_admin.py`).
- **`kyc_applications`** — the 8 fields the brief asks for, plus `status` (enum:
  pending/approved/rejected), `reviewed_by_admin_id`, `reviewed_at`, `rejection_reason`.
- **`beneficiaries`** — one sender can have many; `UNIQUE(sender_id, contact)` stops
  accidental duplicate entries.

**Two separate status enums, on purpose:** `User.kyc_status` (`KYCStatus`, includes
`NOT_STARTED`) and `KYCApplication.status` (`ApplicationStatus`, no `NOT_STARTED` —
an application row only exists once submitted). `routers/admin.py`'s approve/reject
endpoints keep the two in sync:

| `ApplicationStatus` | sets `User.kyc_status` to |
|---|---|
| `PENDING` (on submit) | `PENDING` |
| `APPROVED` | `APPROVED` |
| `REJECTED` | `REJECTED` |

**Portability:** every primary/foreign key uses `sqlalchemy.Uuid`, not
`sqlalchemy.dialects.postgresql.UUID` — that maps to a native `uuid` column on Postgres
and `CHAR(32)` on SQLite, so the same models and migrations work against either backend.
This is why local dev can run entirely on SQLite with zero infrastructure while
`DATABASE_URL` still points at Postgres in a deployed environment.

## 7. Auth model

- **JWT**, `HS256`, signed with `SECRET_KEY`. Claims: `sub` (User.id as a string),
  `iat`, `exp`. Default expiry 60 minutes (`ACCESS_TOKEN_EXPIRE_MINUTES`).
- **No refresh tokens, no server-side revocation.** `/auth/logout` says so honestly
  instead of pretending — see §10 (Known limitations).
- **Login never reveals which half was wrong.** Unknown email and wrong password both
  return the identical `401 {"detail": "Incorrect email or password"}` — a distinct
  message per case would let an attacker enumerate registered emails.
- **`/auth/token` exists only so Swagger's Authorize button works.** It's the same
  credential check as `/auth/login`, wrapped in the `OAuth2PasswordRequestForm` shape
  Swagger expects. The frontend (Track 4) should call `/auth/login` with JSON, not this.
- **404, not 403, for another user's data.** Reading or deleting a beneficiary that
  belongs to someone else returns 404 — a 403 would confirm the id exists, which leaks
  information about other users' records.

## 8. Testing

```bash
pytest -q                                  # the suite
pytest -q --cov=app --cov=worker           # with coverage (currently 97%)
ruff check .                               # lint; config in pyproject.toml
```

Run from `backend/`: `pytest.ini` and `app/config.py`'s relative `env_file=".env"`
both assume it.

364 tests, no Postgres/Redis/XRPL/network needed — everything hits an in-memory
SQLite database created fresh per test, with the queue and the XRPL client faked.
The run takes a few seconds; `conftest.py` drops the bcrypt cost factor for the
suite, which is otherwise about 95% of its wall clock.

The table below lists Track 1's own modules; the full suite is seventeen files,
covering the settlement worker, the queue, the ledger, FX, fees, limits, cash-out
and the audit regressions as well.

| File | Covers |
|---|---|
| `test_security.py` | hashing round-trip, JWT round-trip, expired/tampered token rejection |
| `test_auth.py` | register/login/token/me/logout, the generic-401 behaviour |
| `test_kyc.py` | apply, double-apply conflict, age validation, status |
| `test_beneficiaries.py` | CRUD, duplicate conflict, cross-user 404 isolation |
| `test_admin.py` | the 403 gate on every admin route, review queue, approve/reject |
| `test_migrations.py` | runs the chain against a throwaway file and checks the result matches `Base.metadata` — columns, types, nullability, unique constraints, foreign keys and indexes — then downgrades to base and upgrades again |

Also in `tests/`, beyond Track 1's own:

| File | Covers |
|---|---|
| `test_wallet.py`, `test_ledger.py` | balances, the multi-currency ledger, entry immutability |
| `test_remittances.py`, `test_fee_service.py`, `test_fx_rate_service.py`, `test_limits_service.py` | the quote path end to end, and each service on its own |
| `test_cash_out.py` | request, approve, reject, and the refund leg |
| `test_settlement_worker.py`, `test_settlement_queue.py`, `test_xrpl_service.py` | the asynchronous half: claiming, idempotency, reclaiming an abandoned message, ack semantics |
| `test_encryption.py` | the private-key story: round trip, and that a tampered or foreign ciphertext is refused |
| `test_audit_regressions.py` | one test per defect the 24 Sep audit found |

**`conftest.py`'s import-order gotcha:** `app.config.Settings()` and
`app.security.encryption`'s `Fernet(...)` both validate at *import* time, not call
time. So the required env vars are set at the very top of `conftest.py`, before any
`app.*` import. If you add a new fixture file and get a `ValidationError` on
`Settings`, you likely added an `app.*` import above that block.

To add a test: follow the existing `client`/`auth_headers`/`admin_headers`/
`user_factory`/`auth_header_for` fixtures in `conftest.py` — they're built to compose.

## 9. Migrations

```bash
alembic upgrade head                                    # apply
alembic revision --autogenerate -m "add some_column"     # generate a draft
alembic downgrade -1                                      # roll back one step
```

**Always hand-check an autogenerated revision before running it.** Autogenerate is a
good draft, not a correct answer — it won't infer things like check constraints, and it
sometimes gets column-type changes wrong. The initial migration
(`migrations/versions/*_track1_initial_schema.py`) has one hand-added fix worth reading
as an example: Postgres's `CREATE TYPE` for `sa.Enum` columns isn't dropped by
`DROP TABLE`, so `downgrade()` explicitly drops the three enum types too (harmless
no-op on SQLite).

`migrations/env.py` reads `DATABASE_URL` from `app.config.settings`, not from
`alembic.ini` — there is exactly one source of DB config truth.

## 10. Known limitations (spec §15 material)

- **No token revocation.** Stateless JWTs mean a stolen token is valid until it
  expires; there's no blacklist. Acceptable for a prototype, not for production.
- **No rate limiting, email verification, or password reset.**
- **KYC PII is not encrypted at rest** — `identification_number`, `residential_address`
  etc. are plain `String` columns. Real POPIA compliance would need this encrypted or
  the DB itself encrypted at rest; flagged here rather than silently assumed away.
- **Postgres parity is model-level, not test-verified in this environment.** The
  `sqlalchemy.Uuid`/Alembic setup is designed to work identically against Postgres (see
  §6), and the migration's enum-drop fix specifically targets Postgres, but this sandbox
  has no Postgres/Docker available to run the migration and pytest suite against it
  directly. **Before the check-in, run `alembic upgrade head` and `pytest` once with
  `DATABASE_URL` pointed at a real Postgres instance to close this out.**
