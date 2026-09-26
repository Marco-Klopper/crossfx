# CrossFX: Presentation Script

Spoken script for the 15-slide deck, split by track owner: Marco (Track 1, identity and data), Muki (Track 2, settlement and security), Ndumiso (Track 3, FX, fees and flow), Rafaela (Track 4, frontend, performance and docs).
About 12 minutes of speaking at a normal pace, including the narration during the live demo on slide 8. Square brackets are stage directions or placeholders. The same text is in each slide's speaker notes.

## Slide 1: CrossFX  (Rafaela)

**Rafaela:** Good [morning/afternoon], everyone. We're Group 4: Marco, Muki, Ndumiso and me, Rafaela. Over the next ten to fifteen minutes we'll show you CrossFX, a cross-border remittance prototype. A sender in South Africa pays in rand, and the recipient receives a stablecoin in a custodial wallet, settled on the XRP Ledger Testnet. One point up front: this is an academic prototype. Everything runs on Testnet, and no real money moves anywhere. We split the build into four tracks, and each of us will present our own part.

## Slide 2: Sending money home costs too much and takes too long  (Rafaela)

**Rafaela:** Let's start with the problem. Sending money across borders is expensive: the World Bank puts the global average cost near six percent on a two-hundred-dollar remittance. It is slow, because value passes through several intermediaries. And it is opaque: the exchange-rate margin is usually buried inside the rate, so the customer never sees what they actually paid. Stablecoins offer a faster settlement instrument, but only if the platform around them handles cash-in, cash-out, custody and compliance properly. That is what we set out to build.

## Slide 3: Settle in a stablecoin, disclose every charge  (Rafaela)

**Rafaela:** Our solution: the sender pays in rand, and the recipient receives UCTUSD in a custodial web wallet. UCTUSD is the lecturer-issued Testnet token that stands in for RLUSD, which the brief allows. We kept the issuer and currency code in configuration, so swapping the token is a settings change, not a code change. Settlement is asynchronous: a message queue and a background worker move the value on the XRP Ledger. The recipient can then hold the balance or request a simulated cash-out to US dollars.

## Slide 4: Register to cash-out in five stages  (Rafaela)

**Rafaela:** Here is the journey in five stages. One: the sender registers and completes mock KYC, and an administrator approves it. Two: they add a beneficiary, who must be a registered user. Three: they enter a rand amount and get a full quote, with the rate, the fees, the margin, the stablecoin the recipient gets, and the estimated payout. Four: they confirm a simulated cash-in, which puts a message on the queue, and the worker transfers on the ledger. Five: the recipient sees the balance and the transaction hash, and can request a cash-out. These same five stages are our live demo. Muki will take you through how it is built.

## Slide 5: An API that hands off, and a worker that settles  (Muki)

**Muki:** Thanks, Rafaela. I built the settlement and security side, so let me show you how the system fits together. There are four moving parts. A React single-page app for senders, recipients and admins. A FastAPI REST API with interactive OpenAPI documentation. A Redis Streams queue using a consumer group. And a settlement worker that signs and submits the payment to XRPL Testnet and records the result. Data lives in PostgreSQL, or SQLite for local development. For custody we chose two pooled platform wallets on the ledger, and each user holds a claim in an internal ledger. That means users have no private keys of their own, which matters for security, as we'll see. Ndumiso will explain what the sender is charged.

## Slide 6: Every charge is a separate, configurable line  (Ndumiso)

**Ndumiso:** Thanks, Muki. I built the FX, fee and remittance flow. Every charge is its own line, and every one is configurable: a fixed fee of twenty-five rand, a one-and-a-half percent fee, a one percent FX margin, and a one percent cash-out fee. Here's a worked example. Send a thousand rand at an exchange rate of eighteen-fifty. The fee is forty rand and the margin is ten, so nine hundred and fifty rand is converted, and the recipient gets about fifty-one point three five UCTUSD. The total cost is fifty rand, five percent all-in. And one thing worth flagging: our audit found an earlier draft charged the margin twice, so we fixed it and a regression test now guards against it. Muki, over to you for what happens next.

## Slide 7: Confirm cash-in, queue, settle, credit once  (Muki)

**Muki:** Once the sender confirms payment, my part takes over. First, nothing is queued until the rand cash-in is confirmed, as the brief requires. Second, the worker claims each remittance with a compare-and-swap, so if a message is delivered twice, the recipient is still credited once. Third, failures are kept: a rejected payment is stored as failed with the ledger's result code, and nobody is credited. Fourth, we reconcile: the payout wallet's on-chain balance equals the sum of every user's claim in our ledger. We set up the trust line once, then sign, submit, wait for validation, and store the transaction hash. Now let's see it working. Marco will start us off.

## Slide 8: Live demo: one remittance, end to end  (Marco, Ndumiso, Muki)

**Marco:** Thanks, Muki. I built the identity and data layer, so I'll start the demo. [Register a new sender.] Notice the passwords are hashed and never stored in plaintext. [Open the KYC and profile tab.] The limits card shows zero rand, because unverified users can't send. That's the limit model at work. I can edit my profile here, and I'll fill in the eight KYC fields the brief asks for and submit. Now I'll switch to the admin account. [Approve the application.] Back as the sender, my status is approved and the limits are now three thousand a day and twenty-five thousand a month. Finally I'll add a beneficiary, using the recipient's registered email. [Hand over to Ndumiso.]

**Ndumiso:** Thanks, Marco. Now the quote. [Enter R1 000 and read the quote aloud: fee, margin, converted amount, UCTUSD received, estimated payout.] Now I'll ask for five thousand rand. [Show the refusal.] The system rejects it and tells me which limit I hit and how much headroom is left. Back to a valid amount: I'll confirm the simulated cash-in. [Click Confirm cash-in.] Until this moment nothing was queued. [Hand over to Muki.]

**Muki:** The API returned in milliseconds. [Switch to the worker terminal.] Here the worker claims the message and submits the payment to the ledger. That takes around fifteen seconds, because it waits for a validated ledger. [Back on the Send screen.] The status is settled, and here is the transaction hash. [Paste it into the Testnet explorer and point to tesSUCCESS.] Now the recipient's wallet: the balance, the incoming transfer, its date, status and hash. [Hand over to Ndumiso.]

**Ndumiso:** From the recipient's wallet I'll request a cash-out to US dollars. You can see the fee deducted and the estimated payout. The status is requested. [Switch to the admin account and approve it in the payout queue.] And the wallet now shows the cash-out as completed.

**Muki:** One last thing on the security side. [Show the wallet table.] The seed column is ciphertext, and the encryption key lives in the environment, not in this database. And if a payment fails, it is stored as failed with the ledger's result code and no credit. [Hand back to Rafaela's slides. Fallback if anything breaks: run scripts.walkthrough_remittance.]

## Slide 9: Two keys to protect, one module allowed to use them  (Muki)

**Muki:** Back to the slides for the security design. Because of pooled custody, there are only two private keys in the entire system, and both are encrypted at rest. The encryption key lives in the environment, never in the database that holds the ciphertext. Only the signing module can decrypt a seed, and a search of the code finds exactly one call. No API route or schema can return a key, and none is logged or committed. The trade-off is honest: pooled custody concentrates risk rather than removing it. Marco will cover the identity side.

## Slide 10: A stablecoin does not remove the compliance duty  (Marco)

**Marco:** On the identity side, the same design covers regulation. A stablecoin does not remove the compliance duty. In the spec we cover each area in the brief: KYC and anti-money-laundering, where an admin must approve before anyone can send; configurable limits, which are zero until a user is verified; exchange control, because cross-border rand flows fall under South African capital-flow rules; and custody, stablecoin regulation and licensing, which a real service would need. Beyond that, passwords are bcrypt-hashed, login does equal work for unknown users so accounts can't be enumerated, and the admin flag can only be set from a local script, so a normal account can't promote itself. This is not a legal opinion, and we're candid about the gaps: KYC data isn't encrypted at rest yet, and cash-out isn't gated on recipient KYC. Rafaela will take you through testing.

## Slide 11: 371 backend and 21 frontend tests, run on every pull request  (Rafaela)

**Rafaela:** On testing: we have three hundred and seventy-one backend tests and twenty-one frontend tests. They cover authentication, KYC, limits, fees, the ledger, the settlement worker, database migrations and security. The linters and a production build run too, and all of it runs in GitHub Actions on every pull request. When our audit found bugs, we added a regression test for each one, so they stay fixed.

## Slide 12: A fast API and a slow ledger, joined by a queue  (Rafaela)

**Rafaela:** Now performance, which I ran and wrote up. With forty concurrent senders on a single API worker, the median response time was eight milliseconds and the ninety-fifth percentile was twenty-two. The API sustained around eighty-two to eighty-four requests per second with no failures across almost ten thousand requests. The queue accepted about twenty-seven messages per second. Settlement on the ledger took a median of about fifteen seconds, and all ten funded settlements succeeded. The headline: the on-chain step is about fifteen hundred times slower than the request that triggers it, and that is exactly why the queue is essential.

## Slide 13: Add workers, not tuning  (Rafaela)

**Rafaela:** What did we learn about bottlenecks? The ledger round trip dominates: about fifteen seconds per settlement, almost all of it waiting for the ledger to close, so one worker handles about four and a half per minute. In a burst of ten, the last waits nearly three minutes, mostly queue time. Login takes over half a second because bcrypt is deliberately slow. The fix for settlement is more workers, not tuning, and the consumer group is built for that. In fairness: the sample is small, it ran on one laptop, and multi-worker scaling and the Postgres limit are projected, not measured.

## Slide 14: What building it taught us  (Ndumiso, Muki, Marco, Rafaela)

**Ndumiso:** Lessons learned, one from each of us. Mine: audit early. Our audit caught a double-charged margin and a limit check that held on Postgres but not on SQLite.

**Muki:** Mine: put configuration in config. Because the token issuer was a setting, moving from RLUSD to the lecturer's token was a settings edit, not a rewrite.

**Marco:** Mine: agree the contracts up front. Everything hangs off the user record, so getting identity and the data model right first let the other three tracks build against it in parallel.

**Rafaela:** And mine: liquidity is a real constraint. Our first twelve settlements failed because the pool was unfunded, and nothing warns you yet. [Each person may add one more personal reflection.]

## Slide 15: What we would do next  (Rafaela)

**Rafaela:** To finish, here is what we would do next. Monitor pool liquidity and refuse quotes the pool can't settle. Gate cash-out on recipient KYC and encrypt KYC data at rest. Match recipients by user id instead of email. Benchmark on Postgres with multiple API and settlement workers. And add token revocation. Thank you. We're happy to take questions.
