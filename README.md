# CrossFX

**A prototype cross-border FX remittance platform settling value in UCTUSD on the XRP Ledger Testnet.**

Built for **ECO5040W — Financial Software Engineering**, University of Cape Town (2026 Class Project).

---

## Overview

CrossFX lets a sender in South Africa remit ZAR to a recipient who receives the equivalent
value as UCTUSD in a custodial web wallet. The recipient can hold the UCTUSD or request a
simulated cash-out into USD or another supported fiat currency.

UCTUSD is the lecturer-issued XRPL Testnet IOU the class settles in, standing in for RLUSD
under the brief's "lecturer-approved test token" allowance. The issuer and currency code are
configuration, not constants, so the settlement token can be changed without touching code —
see the tech spec §15.

This is an **academic prototype only** — no real customer funds, real remittances, or
production blockchain credentials are used anywhere in this system.

Core journey: register → mock KYC → add beneficiary → quote (FX rate + fees) → simulated
ZAR cash-in → queued UCTUSD settlement on XRPL Testnet → recipient wallet → simulated cash-out.

## Team — Group 4

| Name | Student Number |
|---|---|
| Ndumiso Zondi | ZNDNDU007 |
| Marco Klopper | KLPMAR012 |
| Muki Mdluli | MDLMUK001 |
| Rafaela Stevenson | STVRAF001 |

## Tech Stack

- **Backend:** Python, FastAPI
- **Database:** PostgreSQL (SQLite for local dev)
- **Blockchain:** XRP Ledger Testnet via `xrpl-py`, UCTUSD (issued token, requires TrustSet)
- **Message queue:** Redis Streams (swap for RabbitMQ/Kafka if preferred)
- **Auth:** JWT, passwords hashed with bcrypt
- **Secrets:** Private keys encrypted at rest, encryption key stored separately from the DB

## Repo Structure

```
crossfx/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app entrypoint
│   │   ├── config.py            # Settings (env-driven)
│   │   ├── database.py          # SQLAlchemy engine/session
│   │   ├── models/              # ORM models: user, kyc, beneficiary, remittance, wallet
│   │   ├── routers/             # API routes: auth, kyc, beneficiaries, remittances, wallet, admin
│   │   ├── services/            # fx_rate, fee calc, xrpl integration, cash-in/cash-out
│   │   └── security/            # password hashing, private-key encryption
│   ├── worker/
│   │   └── settlement_worker.py # Consumes queue, submits UCTUSD transfers to XRPL Testnet
│   ├── tests/
│   ├── requirements.txt
│   └── .env.example
├── frontend/                    # Web front end (framework TBD by team)
├── docs/
│   ├── technical-specification.md   # Skeleton matching the brief's required sections
│   └── resources/                   # Course-provided brief + resource pack (PDFs)
├── performance-testing/         # Load test scripts + results (locust/k6)
└── .gitignore
```

## Getting Started (local dev)

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DB, XRPL, and encryption settings
uvicorn app.main:app --reload
```

Get free Testnet XRP from the [XRPL faucet](https://xrpl.org/resources/dev-tools/xrp-faucets)
before testing settlement.

## Course Resources

See [`docs/resources/`](docs/resources/) for the official project brief and the
supplementary XRPL/trust-line/wallet resource pack from UCT.

## Key Dates

- **18 Aug** — Kick-off meeting
- **21 Aug** — Project check-in
- **5–13 Sep** — Mid-term vacation
- **18 Sep** — Check-in: technical spec complete, PoC scaffolding started
- **25 Sep** — Final: working PoC demo, presentation, lessons learnt

## Assessment Weighting

| Component | Weight |
|---|---|
| Business & technical specification | 20% |
| Web application functionality | 35% |
| XRPL & message-queue integration | 20% |
| Security & private-key protection | 10% |
| Performance testing | 10% |
| Presentation & demonstration | 5% |

## References

- [XRPL Python dev quickstart](https://xrpl.org/docs/tutorials/python/build-apps/get-started)
- [XRPL Testnet faucets](https://xrpl.org/resources/dev-tools/xrp-faucets)
- [Xaman Testnet access guide](https://help.xaman.app/app/learning-more-about-xaman/how-to-access-testnet-on-xrp-ledger)
- [xrpl.services](https://xrpl.services/)
