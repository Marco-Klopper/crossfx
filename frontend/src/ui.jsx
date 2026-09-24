/**
 * Small shared presentation helpers, so the screens carry flow logic rather
 * than formatting detail.
 */

// Money arrives from the API as a JSON string ("950.00", "51.351351") because
// the backend uses Decimal columns. Parsing it to a JS number would introduce
// exactly the float error the backend avoids, so everything below works on the
// string and never converts it.
//
// That comment used to sit above three functions that all called Number().
// At these magnitudes it never visibly misrendered, but the claim was false,
// and the screens had drifted into two different formats for the same data:
// toLocaleString('en-ZA') produced "1 000,00" while .toFixed(6) produced
// "18.500000", so a quote breakdown showed a comma decimal on one row and a
// dot on the next.
//
// One convention now, everywhere: space for thousands, dot for the decimal
// point. Dot rather than the en-ZA comma because the same screens show
// six-decimal token amounts and exchange rates, where a comma reads badly and
// risks being taken for a thousands separator.

const GROUP = '\u2009' // thin space — groups digits without looking like a gap

/**
 * Rounds a decimal string to `places`, half away from zero, using BigInt so
 * the arithmetic is exact. Returns null if the value is not a plain decimal.
 */
function round(value, places) {
  const text = String(value).trim()
  const match = /^([+-]?)(\d+)(?:\.(\d+))?$/.exec(text)
  if (!match) return null

  const sign = match[1] === '-' ? '-' : ''
  const frac = match[3] || ''
  // One extra digit, so the last one decides the rounding.
  const scaled = (frac + '0'.repeat(places + 1)).slice(0, places + 1)
  let digits = BigInt(match[2] + scaled)
  const decider = digits % 10n
  digits /= 10n
  if (decider >= 5n) digits += 1n

  const padded = digits.toString().padStart(places + 1, '0')
  const whole = places === 0 ? padded : padded.slice(0, -places)
  const fraction = places === 0 ? '' : padded.slice(-places)
  return { sign, whole, fraction }
}

function group(whole) {
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, GROUP)
}

/** Formats a decimal string to a fixed number of places, grouped. */
function decimal(value, places) {
  if (value === null || value === undefined || value === '') return null
  const parts = round(value, places)
  if (parts === null) return null
  const { sign, whole, fraction } = parts
  return `${sign}${group(whole)}${fraction ? `.${fraction}` : ''}`
}

/** Formats a fiat amount with a currency prefix. */
export function money(amount, currency = 'ZAR') {
  const value = decimal(amount, 2)
  if (value === null) return '—'
  const symbol = currency === 'ZAR' ? 'R' : currency === 'USD' ? '$' : ''
  return symbol ? `${symbol}${value}` : `${value} ${currency}`
}

/** UCTUSD, at the six decimal places the ledger column holds. */
export function token(amount) {
  const value = decimal(amount, 6)
  return value === null ? '—' : `${value} UCTUSD`
}

/** A bare amount at the precision its currency is held to. */
export function amountIn(value, currency) {
  const formatted = decimal(value, currency === 'UCTUSD' ? 6 : 2)
  return formatted === null ? '—' : formatted
}

export function rate(value) {
  const formatted = decimal(value, 6)
  return formatted === null ? '—' : formatted
}

export function when(timestamp) {
  if (!timestamp) return '—'
  // The API now tags every timestamp as UTC (backend schemas/common.py), so
  // the suffix is normally already there. The fallback stays for anything
  // served by an older build: without it a bare "2026-09-24T14:32:00" is read
  // as local time, which put a fifteen-minute quote nearly two hours in the
  // past for a UTC+2 viewer.
  const iso = /[Zz]|[+-]\d{2}:\d{2}$/.test(timestamp) ? timestamp : `${timestamp}Z`
  return new Date(iso).toLocaleString('en-ZA', {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

const TONE = {
  // remittance
  quoted: 'info',
  cash_in_confirmed: 'pending',
  queued: 'pending',
  settling: 'pending',
  settled: 'good',
  failed: 'bad',
  refunded: 'info',
  // kyc
  not_started: 'neutral',
  pending: 'pending',
  approved: 'good',
  rejected: 'bad',
  // cash-out + ledger entries
  requested: 'info',
  completed: 'good',
  success: 'good',
}

export function Badge({ status }) {
  if (!status) return null
  return (
    <span className={`badge ${TONE[status] || 'neutral'}`}>
      {String(status).replace(/_/g, ' ')}
    </span>
  )
}

export function Alert({ kind = 'info', children }) {
  if (!children) return null
  return <div className={`alert ${kind}`}>{children}</div>
}

/** A row in the quote breakdown. */
export function Line({ label, value, deduct = false, total = false }) {
  return (
    <div
      className={`breakdown-row${deduct ? ' deduct' : ''}${total ? ' total' : ''}`}
    >
      <span className="label">{label}</span>
      <span className="value">{value}</span>
    </div>
  )
}

/** A link to the remittance on the public XRPL Testnet explorer. */
export function TxHash({ hash }) {
  if (!hash) return <span className="mono">—</span>
  return (
    <a
      className="mono"
      href={`https://testnet.xrpl.org/transactions/${hash}`}
      target="_blank"
      rel="noreferrer"
      title={hash}
    >
      {hash.slice(0, 12)}…
    </a>
  )
}
