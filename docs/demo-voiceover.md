# CrossFX: Demo Video Voiceover Script

Narration for the recorded demo (`docs/demo-recording/crossfx-demo.mp4`), about **3 minutes 25 seconds** to match the recording (204 s), roughly 480 spoken words at a relaxed pace. It follows the on-screen caption sections 1 to 5 of the recording, and the run sheet in `submission/demo-script.md`. Lines are split by track owner, so each person can record their own section, or one person can read the lot.

Timings are read off the recording. The video has no audio track, only on-screen captions. The numbers are the ones on screen (rate 18.3257, 51.839831 UCTUSD, estimated payout $51.32).

---

## Intro (0:00 – 0:10) — Marco

This is CrossFX, a cross-border remittance prototype. A sender pays in South African rand, and the recipient receives UCTUSD, a stablecoin settled on the XRP Ledger Testnet. It's a prototype: everything you'll see runs on Testnet, and no real money moves. Let's walk through one remittance, from sign-up to cash-out.

## 1. Sender registers and completes KYC (0:10 – 0:40) — Marco

*On screen: register, KYC & profile tab.*

I'm registering a new sender. Passwords are hashed with bcrypt and never stored in plain text.

On the KYC and profile tab, look at the limits card: zero rand a day, zero a month. Unverified users can't send at all. That's the limit model working, not a separate rule.

Here I can edit my profile, so I'll change the full name and save. Then the mock KYC form, with the eight fields the brief asks for: name, date of birth, nationality, ID number, address, mobile, email and source of funds. I submit, and the status becomes pending. The sender still can't send.

## 2. Admin approves KYC (0:40 – 1:00) — Marco

*On screen: admin login, KYC review queue.*

Now I switch to the administrator. The pending application is in the review queue, and I approve it.

Back as the sender, the status is approved, and the limits card now shows three thousand rand a day and twenty-five thousand a month.

## 3. Beneficiary, quote, fees and limits (1:00 – 1:30) — Marco, then Ndumiso

*On screen: Recipients tab, Send money tab.*

**Marco:** Next, a beneficiary. I add the recipient by their CrossFX email, with country, payout currency and relationship. The recipient has to be a registered user, so I created that account first.

**Ndumiso:** Now the quote. I'll send one thousand rand. Every charge is its own line. A fee of forty rand, which is twenty-five fixed plus one and a half percent. A one percent FX margin of ten rand. That leaves nine hundred and fifty rand to convert, and at this rate the recipient gets about fifty-one point eight four UCTUSD. We also show the estimated cash-out fee and payout, and how much limit headroom is left.

Now I'll ask for five thousand rand. It's refused, and the message names the limit I hit and how much room remains. Back to one thousand, and we have a valid quote. Notice nothing has been paid or queued yet.

## 4. Cash-in and queued settlement (1:30 – 2:15) — Ndumiso, then Muki

*On screen: Confirm cash-in, worker log panel, settled status.*

**Ndumiso:** The transfer must not start before the rand is confirmed, so until this click, nothing is on the queue. I choose a payment method and confirm the simulated cash-in.

**Muki:** The API returned in milliseconds. In the worker panel, you can see it claim the message and submit the payment to the XRP Ledger. That takes around fifteen seconds, because it waits for the ledger to validate. That's why settlement is asynchronous.

[Pause over the wait.]

The status is now settled, and here is the transaction hash. If the same message were delivered twice, the worker's compare-and-swap claim means the recipient is still only credited once.

## 5. Recipient wallet, cash-out and burn (2:15 – 3:15) — Muki, then Ndumiso

*On screen: recipient Wallet tab, cash-out form, admin payout queue, worker log.*

**Muki:** Now the recipient logs in. Here's the UCTUSD balance, and the incoming transfer with its date, status and transaction hash.

**Ndumiso:** From here the recipient requests a cash-out of twenty UCTUSD to US dollars. The one percent fee comes off first, so twenty UCTUSD pays out nineteen eighty in US dollars. The status is requested, and the UCTUSD is reserved straight away.

As the admin, I approve it in the payout queue. Approving doesn't pay anything yet. It queues a burn. The worker sends the net UCTUSD from the payout pool back to the issuing address, which stands in for handing the tokens to an exchange.

[Pause over the wait.]

Back in the wallet, which updates on its own, the cash-out moves from approved to processing to completed, with the burn transaction hash. The fiat is only credited once the burn lands on the ledger. If the burn were rejected, the cash-out would be marked failed and the UCTUSD refunded.

## Outro (3:15 – 3:24) — Muki

That's the whole journey: KYC, quote, cash-in, queued settlement on the XRP Ledger, the recipient's wallet, and a cash-out that burns the tokens on-chain. Behind it, the private keys are encrypted at rest, failed payments are stored with the ledger's result code, and nobody is credited for a payment that didn't land. Thanks for watching.

---

## Recording notes

- **One voice or four?** Four voices match the live demo on slide 8. If you want a single narrator, drop the bold names and read straight through.
- **Sync to the video.** The captions are on screen already, so don't read them out; say what the viewer can't see. Record against the playback, or time to the section markers above and adjust after the first take.
- **Cover the waits.** The two ledger waits are the only dead air. The lines marked "Pause over the wait" can stretch or shrink to fit, or trim the footage.
- **Cut for time.** If it runs over five minutes, drop the profile edit in section 1 and the second sentence of the outro first.
