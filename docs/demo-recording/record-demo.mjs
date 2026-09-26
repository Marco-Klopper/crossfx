// Records the CrossFX demo (docs/demo-script.md) as a .webm, driving Edge.
// Usage: node record-demo.mjs <worker-log-path> <output-dir>
import { chromium } from 'playwright'
import fs from 'node:fs'
import path from 'node:path'

const [, , WORKER_LOG, OUT_DIR] = process.argv
const APP = 'http://localhost:5173'
const stamp = new Date().toISOString().slice(11, 16).replace(':', '')
const PASSWORD = 'DemoPass-123'
const SENDER = { name: `Sam Sender`, email: `sender${stamp}@example.com` }
const RECIPIENT = { name: `Riley Recipient`, email: `recipient${stamp}@example.com` }
const ADMIN = { email: 'admin@example.com', password: 'adminpass123' }

fs.mkdirSync(OUT_DIR, { recursive: true })

const browser = await chromium.launch({
  channel: 'msedge',
  headless: true,
})
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
})
const page = await context.newPage()

// --- Video: no ffmpeg needed. Screencast frames from the app page are drawn
// onto a canvas in a helper tab, which the browser's own MediaRecorder encodes.
// its own browser process, so the two pages never compete for foreground/focus
const recBrowser = await chromium.launch({ channel: 'msedge', headless: true })
const rec = await (await recBrowser.newContext()).newPage()
await rec.setContent('<canvas id="c" width="1440" height="900"></canvas>')
await rec.evaluate(() => {
  const canvas = document.getElementById('c')
  const ctx = canvas.getContext('2d')
  ctx.fillStyle = '#000'
  ctx.fillRect(0, 0, 1440, 900)
  let last = null
  window.__draw = (b64) =>
    new Promise((resolve) => {
      const img = new Image()
      img.onload = () => { last = img; ctx.drawImage(img, 0, 0, 1440, 900); resolve() }
      img.src = 'data:image/jpeg;base64,' + b64
    })
  // repaint the last frame so the encoder keeps getting frames when the page is static
  const stream = canvas.captureStream(0)
  const track = stream.getVideoTracks()[0]
  // Push a frame on a fixed clock, so the encoder timeline is wall-clock time
  // even while the page is static (captureStream otherwise skips idle time).
  setInterval(() => {
    if (last) ctx.drawImage(last, 0, 0, 1440, 900)
    track.requestFrame()
  }, 80)
  const mime = MediaRecorder.isTypeSupported('video/webm;codecs=vp9') ? 'video/webm;codecs=vp9' : 'video/webm'
  const recorder = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 3_000_000 })
  const chunks = []
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data)
  window.__start = () => recorder.start(1000)
  window.__stop = () =>
    new Promise((resolve) => {
      recorder.onstop = async () => {
        const blob = new Blob(chunks, { type: 'video/webm' })
        const buf = new Uint8Array(await blob.arrayBuffer())
        let bin = ''
        for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000))
        resolve(btoa(bin))
      }
      recorder.stop()
    })
})
const cdp = await context.newCDPSession(page)
let framesSeen = 0
let drawing = false
let pending = null
async function pump() {
  if (drawing) return
  drawing = true
  while (pending) {
    const d = pending
    pending = null
    await rec.evaluate((x) => window.__draw(x), d).catch(() => {})
  }
  drawing = false
}
cdp.on('Page.screencastFrame', ({ data, sessionId }) => {
  framesSeen++
  // Ack straight away so the page is never held back by the recorder, and
  // keep only the newest undrawn frame if drawing falls behind.
  cdp.send('Page.screencastFrameAck', { sessionId }).catch(() => {})
  pending = data
  pump()
})

// Worker "terminal": the last few meaningful log lines, polled from the file.
await page.exposeFunction('__workerTail', () => {
  try {
    const lines = fs
      .readFileSync(WORKER_LOG, 'utf8')
      .split(/\r?\n/)
      .filter((l) => /^\d{4}-\d\d-\d\d /.test(l) && !/Consume loop/.test(l))
    return lines.slice(-7).join('\n')
  } catch {
    return ''
  }
})

await page.addInitScript(() => {
  const install = () => {
    if (document.getElementById('demo-overlay')) return
    const style = document.createElement('style')
    style.textContent = `
      body { padding-bottom: 236px !important; }
      #demo-overlay { position: fixed; left: 0; right: 0; bottom: 0; z-index: 99999;
        pointer-events: none; font-family: system-ui, sans-serif; }
      #demo-term { background: #0b0f14; color: #b8f7c5; font: 12px/1.45 Consolas, monospace;
        padding: 8px 16px; height: 136px; overflow: hidden; border-top: 2px solid #2b3a4a; white-space: pre; }
      #demo-term b { color: #7aa2f7; font-weight: 600; }
      #demo-cap { background: #1d4ed8; color: #fff; padding: 10px 20px; height: 100px; box-sizing: border-box; }
      #demo-cap .t { font-size: 13px; letter-spacing: .06em; text-transform: uppercase; opacity: .85; }
      #demo-cap .s { font-size: 19px; margin-top: 4px; line-height: 1.3; }
    `
    document.head.appendChild(style)
    const el = document.createElement('div')
    el.id = 'demo-overlay'
    el.innerHTML =
      '<div id="demo-term"></div><div id="demo-cap"><div class="t"></div><div class="s"></div></div>'
    document.body.appendChild(el)
    const term = el.querySelector('#demo-term')
    const tick = async () => {
      try {
        const txt = await window.__workerTail()
        term.innerHTML =
          '<b>$ python -m worker.settlement_worker</b>\n' +
          txt.replace(/&/g, '&amp;').replace(/</g, '&lt;')
      } catch {}
    }
    setInterval(tick, 700)
    tick()
  }
  if (document.body) install()
  else document.addEventListener('DOMContentLoaded', install)
})

const say = async (title, text) => {
  await page.evaluate(
    ([t, s]) => {
      const cap = document.getElementById('demo-cap')
      if (!cap) return
      cap.querySelector('.t').textContent = t
      cap.querySelector('.s').textContent = s
    },
    [title, text],
  )
}
const beat = (ms = 2200) => page.waitForTimeout(ms)
const type = async (sel, value) => {
  await page.locator(sel).click()
  await page.locator(sel).fill('')
  await page.locator(sel).pressSequentially(value, { delay: 35 })
}
const tab = async (name) => {
  await page.getByRole('button', { name, exact: true }).first().click()
  await page.waitForTimeout(700)
}
const logout = async () => {
  await page.getByRole('button', { name: 'Log out' }).click()
  await page.waitForTimeout(900)
}
const login = async (email, password) => {
  // the auth screen may open on either mode
  if (await page.getByRole('button', { name: 'Log in', exact: true }).count() === 0) {
    await page.getByRole('button', { name: 'Log in' }).click()
  }
  await type('#email', email)
  await type('#password', password)
  await page.locator('form button[type=submit]').click()
  await page.waitForTimeout(1200)
}
const register = async ({ name, email }) => {
  await page.getByRole('button', { name: 'Create one' }).click()
  await type('#fullName', name)
  await type('#email', email)
  await type('#password', PASSWORD)
  await page.locator('form button[type=submit]').click()
  await page.waitForTimeout(1500)
}

let step = 'start'
try {
  // ---- 0. Before we start -------------------------------------------------
  await page.goto(APP)
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 65, maxWidth: 1440, maxHeight: 900, everyNthFrame: 2 })
  await page.waitForTimeout(800)
  await rec.evaluate(() => window.__start())
  await page.waitForSelector('#email')
  await say(
    'Setup',
    'Redis, the API, the settlement worker (terminal below) and the React app are running. Pool wallets are funded with UCTUSD.',
  )
  await beat(4000)

  // Pre-create the recipient so the sender can add them as a beneficiary.
  step = 'pre-create recipient'
  await say('Setup', `Pre-creating the recipient account (${RECIPIENT.email}) — a recipient must be a registered user.`)
  await register(RECIPIENT)
  await beat(1500)
  await logout()

  // ---- 1. Sender registers and completes KYC ------------------------------
  step = 'sender register'
  await say('1 · Sender registers and completes KYC', `Registering ${SENDER.email}. Passwords are bcrypt-hashed.`)
  await register(SENDER)
  await beat(1500)
  await tab('KYC & profile')
  await say(
    '1 · KYC status and limits',
    "Limits card: R0 daily, R0 monthly. Unverified users can't send — that is the limit model, not a separate rule.",
  )
  await beat(4500)
  await say('1 · Profile management', 'Basic profile management: change the full name and save.')
  await type('#profile-name', SENDER.name)
  await page.getByRole('button', { name: 'Save profile' }).click()
  await beat(2500)

  step = 'kyc form'
  await say('1 · Mock KYC', 'Eight KYC fields: name, date of birth, nationality, ID number, address, mobile, email, source of funds.')
  await type('#k-name', SENDER.name)
  await page.locator('#k-dob').fill('1990-05-15')
  await type('#k-nat', 'South African')
  await type('#k-id', '9005155800086')
  await type('#k-addr', '1 Long Street, Cape Town')
  await type('#k-mobile', '+27821234567')
  await type('#k-email', SENDER.email)
  await type('#k-funds', 'Salary')
  await page.getByRole('button', { name: 'Submit application' }).click()
  await beat(3500)
  await say('1 · KYC submitted', 'Status is now pending — the sender still cannot send.')
  await beat(2500)

  // ---- 2. Admin approves KYC ----------------------------------------------
  step = 'admin kyc'
  await logout()
  await say('2 · Admin approves KYC', 'Switching to the administrator: log in as admin@example.com and open the Admin tab.')
  await login(ADMIN.email, ADMIN.password)
  await tab('Admin')
  await beat(3000)
  const kycCard = page.locator('.card', { has: page.getByRole('heading', { name: 'KYC review queue' }) })
  await say('2 · KYC review queue', "The pending application is in the queue. Approving lifts the sender to the verified limits.")
  await beat(3000)
  await kycCard.locator('tr', { hasText: SENDER.email }).getByRole('button', { name: 'Approve' }).click()
  await beat(3500)
  await logout()
  await login(SENDER.email, PASSWORD)
  await tab('KYC & profile')
  await say('2 · Sender is verified', 'Refresh: status is approved and the limits card now shows the verified daily and monthly limits.')
  await beat(5000)

  // ---- 3. Beneficiary, quote, fees, limits --------------------------------
  step = 'beneficiary'
  await say('3 · Beneficiary', 'Adding the recipient by their CrossFX email, with country, payout currency and relationship.')
  await tab('Recipients')
  await type('#b-name', RECIPIENT.name)
  await type('#b-contact', RECIPIENT.email)
  await type('#b-country', 'United States')
  await page.locator('#b-currency').selectOption('USD')
  await type('#b-rel', 'Friend')
  await page.getByRole('button', { name: 'Add recipient' }).click()
  await beat(3000)

  step = 'quote'
  await tab('Send money')
  await say('3 · Exchange-rate quote and fees', 'R1 000 send: fee, FX margin, amount converted, UCTUSD the recipient receives, estimated cash-out fee and payout.')
  await type('#s-amount', '1000')
  await page.getByRole('button', { name: 'Get a quote' }).click()
  await page.waitForSelector('text=Quotation')
  await beat(7000)

  step = 'limit rejection'
  await say('3 · Limits', 'Now R5 000: exceeding the daily limit is rejected, naming the period and the headroom.')
  await type('#s-amount', '5000')
  await page.getByRole('button', { name: 'Get a quote' }).click()
  await beat(5500)

  step = 'requote'
  await say('3 · Back to R1 000', 'A valid quote again. Nothing has been paid or queued yet.')
  await type('#s-amount', '1000')
  await page.getByRole('button', { name: 'Get a quote' }).click()
  await page.waitForSelector('text=Confirm cash-in')
  await beat(3500)

  // ---- 4. Cash-in, queued settlement --------------------------------------
  step = 'cash-in'
  await say('4 · Simulated ZAR cash-in', 'Nothing is queued until the rand is confirmed. The sender picks a payment method and confirms.')
  await beat(3500)
  await page.getByRole('button', { name: 'Confirm cash-in' }).click()
  await say(
    '4 · Queued settlement',
    'API returns in ms; a message is on the Redis stream. Watch the worker terminal claim it and submit to the XRP Ledger.',
  )
  await page.waitForSelector('.badge.good:has-text("settled")', { timeout: 120000 })
  await say('4 · Settled', 'Status is settled with the XRPL transaction hash. A redelivered message cannot credit twice (compare-and-swap claim).')
  await beat(7000)

  // ---- 5. Recipient wallet, cash-out --------------------------------------
  step = 'recipient wallet'
  await logout()
  await say('5 · Recipient wallet', 'Recipient logs in: UCTUSD balance and the incoming transfer with date, status and tx hash.')
  await login(RECIPIENT.email, PASSWORD)
  await tab('Wallet')
  await beat(7000)

  step = 'cash-out request'
  await say('5 · Simulated cash-out', 'Request a cash-out of 20 UCTUSD to USD. The fee is taken in UCTUSD first. Status: requested.')
  await type('#w-amount', '20')
  await page.getByRole('button', { name: 'Request cash-out' }).click()
  await beat(1200)
  await beat(4500)

  step = 'admin payout'
  await logout()
  await say('5 · Admin payout queue', 'Admin > Payout queue: approve the payout. Approving queues an on-chain burn for the worker.')
  await login(ADMIN.email, ADMIN.password)
  await tab('Admin')
  await beat(3500)
  const payCard = page.locator('.card', { has: page.getByRole('heading', { name: 'Payout queue' }) })
  await payCard.getByRole('button', { name: 'Approve' }).first().click()
  await say('5 · The burn', 'The worker pays the net UCTUSD back to the issuing address (handing tokens to an exchange), then credits the fiat.')
  await beat(6000)

  step = 'recipient completed'
  await logout()
  await login(RECIPIENT.email, PASSWORD)
  await tab('Wallet')
  await say('5 · Cash-out completes', 'The wallet updates on its own: approved → processing → completed, with the burn transaction hash.')
  await page.waitForSelector('.badge.good:has-text("completed")', { timeout: 120000 })
  await beat(8000)
  await say('Done', 'That is the full journey: KYC → quote → cash-in → queued XRPL settlement → wallet → burn cash-out.')
  await beat(4000)
} catch (err) {
  console.error('FAILED at step:', step, err.message)
  await page.screenshot({ path: path.join(OUT_DIR, 'failure.png') }).catch(() => {})
  process.exitCode = 1
} finally {
  await cdp.send('Page.stopScreencast').catch(() => {})
  while (drawing || pending) await new Promise((r) => setTimeout(r, 50))
  const b64 = await rec.evaluate(() => window.__stop())
  const dest = path.join(OUT_DIR, 'crossfx-demo.webm')
  fs.writeFileSync(dest, Buffer.from(b64, 'base64'))
  await browser.close()
  await recBrowser.close()
  console.log('frames:', framesSeen, 'video:', dest, `${(fs.statSync(dest).size / 1e6).toFixed(1)} MB`)
}
