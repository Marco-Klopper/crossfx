/**
 * Small shared presentation helpers, so the screens carry flow logic rather
 * than formatting detail.
 */

// Money arrives from the API as a JSON string ("950.00", "51.351351") because
// the backend uses Decimal columns. Parsing it to a JS number would introduce
// exactly the float error the backend avoids, so these format the string and
// never do arithmetic on it.

/** Formats a fiat amount with a currency prefix. */
export function money(amount, currency = 'ZAR') {
  if (amount === null || amount === undefined) return '—'
  const symbol = currency === 'ZAR' ? 'R' : currency === 'USD' ? '$' : ''
  const value = Number(amount).toLocaleString('en-ZA', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  return symbol ? `${symbol}${value}` : `${value} ${currency}`
}

/** UCTUSD, at the six decimal places the ledger column holds. */
export function token(amount) {
  if (amount === null || amount === undefined) return '—'
  return `${Number(amount).toLocaleString('en-ZA', {
    minimumFractionDigits: 6,
    maximumFractionDigits: 6,
  })} UCTUSD`
}

export function rate(value) {
  if (value === null || value === undefined) return '—'
  return Number(value).toFixed(6)
}

export function when(timestamp) {
  if (!timestamp) return '—'
  // The API returns naive UTC timestamps, so mark them as UTC before the
  // browser renders them in local time.
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
