# CrossFX — Final Demo Script

Target length: **about 5 minutes**, as the TA asked (about 10 minutes of slides, about 5 of demo).
The TA strongly recommends **recording the demo**, so record it once with this script, keep it as the
submission fallback, and play it on slide 8 if the live run misbehaves. The sections below are timed for
that 5-minute cut; the optional extras are marked. Presenters follow track ownership: Marco (identity, KYC, beneficiaries), Ndumiso (quote, limits, cash-in, cash-out),
Muki (settlement, wallet, keys), Rafaela (slides before and after the demo). Whoever is speaking drives the
browser; one other person watches the terminals. The full spoken script is in `docs/presentation-script.md` in the repo.

Every step below maps to an item in the brief's "Working Web Application" list.

---

## 0. Before you go on stage (do this 30 minutes early)

**Terminals (four):**

| # | Command | Expect |
|---|---|---|
| 1 | `docker run -p 6379:6379 redis:7-alpine` | Redis ready |
| 2 | `cd backend && uvicorn app.main:app` | API on `http://127.0.0.1:8000` |
| 3 | `cd backend && python -m worker.settlement_worker` | Worker idle, waiting on the stream |
| 4 | `cd frontend && npm run dev` | App on `http://localhost:5173` |

**One-off setup (skip if already done on this database):**

```bash
cd backend
alembic upgrade head
python -m scripts.create_admin --email admin@example.com --password adminpass123
python -m scripts.init_platform_wallets      # only once per database; prints the pool addresses
```

**Liquidity check (the most likely thing to break the demo):**

- The send pool must hold UCTUSD or every settlement fails with `tecPATH_DRY`
  (spec §15). Confirm its balance on the XRPL Testnet explorer before starting.
- A demo remittance of R500 delivers roughly 25 UCTUSD. Budget for 3–4 attempts.
- If the pool is low, ask Marc (LVNMAR013@myuct.ac.za) for a top-up well ahead of time.

**Browser:** two windows in separate profiles (or one normal and one private) so
the sender and the recipient can be logged in at once. Have `http://127.0.0.1:8000/docs`
open in a third tab, and the XRPL Testnet explorer ready to paste a hash into.

**Pre-create the recipient** (saves a minute on stage): register
`recipient@example.com` in window 2 and leave it logged in on the Wallet tab.

**Fallback if anything fails live:**

```bash
cd backend && python -m scripts.walkthrough_remittance
```

prints the same journey, request by request, against the running API.

---

## 1. Sender registers and completes KYC (~1 min) — Marco

*Brief: registration and login, mock KYC, view KYC status and limits, profile.*

1. Window 1, register `sender@example.com` (any name, password of 8+ characters).
2. Open the **KYC & profile** tab.
   - Point out the **limits card: R0 daily, R0 monthly**. *"Unverified users can't send. That is the limit model, not a separate rule."*
   - Under **Your profile**, change the full name and click **Save profile**. *"Basic profile management."*
3. Fill in the eight KYC fields (name, date of birth, nationality, ID number, address, mobile, email, source of funds) and submit. Status becomes **pending**.

**Say:** passwords are bcrypt-hashed; see the security slide.

## 2. Admin approves KYC (~0.5 min) — Marco

*Brief: administrator approval functions.*

1. Window 3 (or log out and back in): log in as `admin@example.com`. Open the **Admin** tab.
2. Approve the pending application.
3. Back in the sender's window, refresh: status is **approved** and the limits card now reads **R3 000 daily, R25 000 monthly**.

## 3. Beneficiary (Marco), then quote, fees and limits (Ndumiso) (~1 min)

*Brief: beneficiary creation, exchange-rate quotation, fee calculation, daily and monthly limits.*

1. **Recipients** tab: add a beneficiary using `recipient@example.com`, country and payout currency, and relationship. *Note: the recipient must be a registered user (spec §15).*
2. **Send money** tab: choose the beneficiary and enter **R1 000**.
3. Read the quote aloud. At a mid-market rate of 18.50 it matches the slide-6 worked example:
   - fee R40.00 (R25 + 1.5%), margin R10.00, converted R950.00,
   - **51.351351 UCTUSD** to the recipient, estimated cash-out fee, estimated USD payout,
   - the remaining daily and monthly headroom.
   *(If the live rate differs, quote the numbers on screen; the arithmetic is the same.)*
4. **Show the limit rejection:** request a quote for **R5 000**. The API refuses it and names the period and headroom. *"Exceeding the daily limit is rejected, as the brief requires."*

## 4. Cash-in (Ndumiso), then queued settlement (Muki) (~1.5 min)

*Brief: simulated ZAR cash-in, queued transfer, XRPL transaction hash.*

1. The quote from step 3 already created the remittance (status **quoted**). Use R500 instead if the pool is thin.
2. Point out it has not been paid yet. *"Nothing is queued yet: the transfer must not start before the ZAR is confirmed."*
3. Click **Confirm cash-in** and pick the payment method. (An admin can also confirm on the sender's behalf from the **Mock payment service** card on the Admin tab.)
4. **Switch to the worker terminal** and show the message being claimed and the XRPL submission. *"Asynchronous: the API returned in about 10 ms; the ledger takes about 15 seconds."*
5. Back on the Send screen the status moves to **settled** and shows the **XRPL transaction hash**. Paste it into the Testnet explorer and show `tesSUCCESS`.

**Say:** the hash is stored; a redelivered message cannot credit twice (compare-and-swap claim).

## 5. Recipient wallet (Muki), then cash-out and burn (Ndumiso) (~1.5 min)

*Brief: recipient wallet, simulated cash-out. Marc's clarification: a withdrawal "burns" the tokens by sending them back to the issuing address, and the fiat conversion is simulated in our own ledger.*

1. Window 2 (recipient), **Wallet** tab: available UCTUSD balance, the incoming transfer with date, status and tx hash.
2. Request a **cash-out** to USD (for example 20 UCTUSD). Show the fee deduction and payout estimate. Status: **requested**. The UCTUSD is reserved (debited) straight away.
3. Window 3 (admin), **Admin > Payout queue**: approve the payout. *"Approving does not pay anything yet. It queues a message, like the settlement does."*
4. **Switch to the worker terminal.** It shows `Burning 19.800000 UCTUSD for cash-out ...`, then `Burn for cash-out ... settled: <hash>`. *"The worker pays the net UCTUSD from the payout pool back to the issuing address, `rELez4x4Zqv3KYqboYVfrYPF8521Ycbxa5`. That stands in for handing the tokens to an exchange. The fee stays with the platform."*
5. Recipient's wallet updates on its own (it polls while a cash-out is in flight): **requested** → **approved** → **processing** → **completed**, with the **Burn tx** hash and the USD balance credited. Paste the hash into the Testnet explorer: `tesSUCCESS`, a payment to the issuer.

**Say:** the fiat is only credited after the burn lands on-chain. If the burn is rejected, the cash-out is marked **failed** and the UCTUSD is refunded, so the recipient is never out of pocket. A redelivered message cannot burn twice (compare-and-swap claim, as for settlement). The fiat leg itself is simulated, as the brief allows.

## 6. Failure handling and encrypted keys (optional, ~1 min, cut from the 5-minute run) — Muki

Cover this on the security slide instead, or show it only if time allows.

*Brief: failed-transaction handling, encrypted XRPL private keys.*

- Show `backend/app/security/encryption.py` and the `platform_wallets` table:
  the seed column is ciphertext, and the key is in `.env`, not the database.
- Mention the 12 recorded `tecPATH_DRY` failures from the unfunded-pool run in
  `performance-testing/README.md`: failed settlements are stored as FAILED with
  the XRPL result code and give no credit.
- `http://127.0.0.1:8000/docs` shows the interactive OpenAPI documentation.

---

## Likely questions, and where the answer is

| Question | Answer |
|---|---|
| Why UCTUSD, not RLUSD? | Lecturer-approved test token; issuer and currency code are config (spec §15). |
| Custody model? | One pooled send wallet and one payout wallet; users hold ledger claims (spec §9.1). |
| Duplicate credits? | Idempotency key plus compare-and-swap claim (spec §9.5). |
| What is not done? | Cash-out KYC gating, KYC data encryption at rest, JWT revocation (spec §15). |
| Real-world licensing? | Spec §14.9. |

## Timing summary

| Section | Minutes |
|---|---:|
| 1 Register and KYC | 1 |
| 2 Admin approval | 0.5 |
| 3 Beneficiary, quote, limits | 1 |
| 4 Cash-in and settlement | 1.5 |
| 5 Wallet, cash-out and burn | 1.5 |
| 6 Failures and keys (optional) | 0 (cut) |
| **Total** | **5.5** |

Settlement and the cash-out burn each take about 15 to 30 seconds on the ledger. In the recording, narrate over
the wait or cut it; live, have the next sentence ready.

## Recording tips

- Record the full run once the pool is funded and everything is warm; keep the cleanest take.
- Use a fresh database or fresh emails so registration does not hit a "already registered" error.
- Show the terminals and the browser together so the queue and worker are visible.
- A scripted recording of this whole flow (captions and a worker-log panel) is in `docs/demo-recording/`;
  `record-demo.mjs` re-runs it. It needs `npm install playwright` in a scratch folder and Edge or Chrome installed.
- Keep the walkthrough script output as a second fallback.
