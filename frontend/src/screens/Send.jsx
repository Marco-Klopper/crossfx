import { useEffect, useRef, useState } from 'react'

import * as api from '../api'
import { Alert, Badge, Line, TxHash, money, rate, token, when } from '../ui.jsx'

/**
 * The sender's journey: quote → confirm cash-in → watch it settle
 * (brief §3 steps 4–9, spec §4–§8).
 *
 * The quotation shows every figure the brief requires it to disclose, and
 * `effective_rate` alongside the mid-market `fx_rate` — publishing only the
 * first would advertise a rate the sender does not actually get, and only the
 * second would hide how the price was built.
 */
const METHODS = [
  { id: 'agent_cash', label: 'Cash at an agent' },
  { id: 'bank_transfer', label: 'Bank transfer (EFT)' },
  { id: 'card', label: 'Card payment' },
]

// Settlement is asynchronous, so the status is polled rather than pushed.
const POLL_MS = 3000

export default function Send({ me }) {
  const [beneficiaries, setBeneficiaries] = useState([])
  const [beneficiaryId, setBeneficiaryId] = useState('')
  const [amount, setAmount] = useState('1000.00')

  const [quote, setQuote] = useState(null)
  const [method, setMethod] = useState('agent_cash')
  const [tracked, setTracked] = useState(null)

  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [warning, setWarning] = useState('')
  const [busy, setBusy] = useState(false)
  const [history, setHistory] = useState([])

  const pollRef = useRef(null)

  async function loadHistory() {
    try {
      setHistory(await api.listRemittances())
    } catch (err) {
      setError(err.detail || err.message)
    }
  }

  useEffect(() => {
    api
      .listBeneficiaries()
      .then((rows) => {
        setBeneficiaries(rows)
        if (rows.length > 0) setBeneficiaryId(rows[0].id)
      })
      .catch((err) => setError(err.detail || err.message))
    loadHistory()
  }, [])

  // Poll the tracked remittance until it reaches a terminal state. Cleared on
  // unmount so a tab switch does not leave a timer running.
  useEffect(() => {
    if (!tracked) return undefined
    if (tracked.status === 'settled' || tracked.status === 'failed') {
      loadHistory()
      return undefined
    }
    pollRef.current = setTimeout(async () => {
      try {
        setTracked(await api.getRemittance(tracked.id))
      } catch {
        // A transient read failure should not kill the poll loop.
      }
    }, POLL_MS)
    return () => clearTimeout(pollRef.current)
  }, [tracked])

  async function getQuote(event) {
    event.preventDefault()
    setError('')
    setNotice('')
    setWarning('')
    setQuote(null)
    setTracked(null)
    setBusy(true)
    try {
      setQuote(
        await api.createQuote({ beneficiaryId, zarSendAmount: amount }),
      )
    } catch (err) {
      // A 403 here is the limit check: the message names the period, the
      // limit and the headroom left.
      setError(err.detail || err.message)
    } finally {
      setBusy(false)
    }
  }

  async function payIn() {
    setError('')
    setNotice('')
    setWarning('')
    setBusy(true)
    try {
      const result = await api.confirmCashIn(quote.remittance_id, method)
      setTracked(result.remittance)
      setQuote(null)
      if (result.queued) {
        setNotice(result.detail)
      } else {
        // Not an error: the cash-in was confirmed, only the queue was
        // unreachable. Telling the sender it failed would invite them to pay
        // a second time.
        setWarning(result.detail)
      }
      await loadHistory()
    } catch (err) {
      setError(err.detail || err.message)
    } finally {
      setBusy(false)
    }
  }

  if (me.kyc_status !== 'approved') {
    return (
      <div className="card">
        <h2>Send money</h2>
        <Alert kind="warn">
          Sending requires an approved KYC application — your limit is{' '}
          {money(me.limits.daily_limit_zar)} until then. Open the KYC tab to
          apply.
        </Alert>
        {/*
          This branch used to render no error at all, so anything the mount
          effect above set was written to state and never shown. The history
          fetch was itself the usual cause: GET /remittances/ was gated on
          approved KYC, so it returned 403 on every mount of the default tab
          for exactly the users who land here. The backend no longer gates
          reads that way, and whatever else goes wrong is now visible.
        */}
        <Alert kind="error">{error}</Alert>
      </div>
    )
  }

  return (
    <>
      <div className="grid-2">
        <div className="card">
          <h2>New transfer</h2>
          <p className="hint">
            Enter what you want to send in rand. The quote is held for 15
            minutes.
          </p>

          <Alert kind="error">{error}</Alert>
          <Alert kind="success">{notice}</Alert>
          <Alert kind="warn">{warning}</Alert>

          {beneficiaries.length === 0 ? (
            <Alert kind="info">
              Add a recipient first — see the Recipients tab.
            </Alert>
          ) : (
            <form onSubmit={getQuote}>
              <div className="field">
                <label htmlFor="s-ben">Recipient</label>
                <select
                  id="s-ben"
                  value={beneficiaryId}
                  onChange={(e) => setBeneficiaryId(e.target.value)}
                >
                  {beneficiaries.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.full_name} — {row.contact} (
                      {row.preferred_payout_currency})
                    </option>
                  ))}
                </select>
              </div>

              <div className="field">
                <label htmlFor="s-amount">You send (ZAR)</label>
                <input
                  id="s-amount"
                  type="number"
                  step="0.01"
                  min="0.01"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  required
                />
              </div>

              <button className="primary" type="submit" disabled={busy}>
                {busy ? 'Pricing…' : 'Get a quote'}
              </button>
            </form>
          )}
        </div>

        <div className="card">
          <h2>Quotation</h2>
          {!quote ? (
            <p className="empty">Request a quote to see the breakdown.</p>
          ) : (
            <>
              <div className="breakdown">
                <Line label="You send" value={money(quote.zar_send_amount)} />
                <Line
                  label="Transaction fee"
                  value={`−${money(quote.transaction_fee_zar)}`}
                  deduct
                />
                <Line
                  label="FX margin"
                  value={`−${money(quote.fx_margin_zar)}`}
                  deduct
                />
                <Line
                  label="Amount converted"
                  value={money(quote.net_converted_zar)}
                />
                <Line
                  label="Exchange rate (mid-market)"
                  value={`${rate(quote.fx_rate)} ZAR/USD`}
                />
                <Line
                  label="Recipient receives"
                  value={token(quote.uctusd_amount)}
                  total
                />
                <Line
                  label="All-in effective rate"
                  value={`${rate(quote.effective_rate)} ZAR per UCTUSD`}
                />
                <Line
                  label="Estimated cash-out fee"
                  value={token(quote.cash_out_fee_uctusd)}
                />
                <Line
                  label={`Estimated payout (${quote.estimated_payout_currency})`}
                  value={money(
                    quote.estimated_payout_amount,
                    quote.estimated_payout_currency,
                  )}
                />
              </div>

              <p className="hint" style={{ marginTop: 12 }}>
                Quote expires {when(quote.quote_expires_at)}. Remaining today:{' '}
                {money(quote.limits.daily_remaining_zar)} of{' '}
                {money(quote.limits.daily_limit_zar)} · this month:{' '}
                {money(quote.limits.monthly_remaining_zar)} of{' '}
                {money(quote.limits.monthly_limit_zar)}.
              </p>

              <div className="field">
                <label htmlFor="s-method">How are you paying in?</label>
                <select
                  id="s-method"
                  value={method}
                  onChange={(e) => setMethod(e.target.value)}
                >
                  {METHODS.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </div>

              <div className="actions">
                <button className="primary" onClick={payIn} disabled={busy}>
                  {busy ? 'Confirming…' : 'Confirm cash-in'}
                </button>
                <button className="secondary" onClick={() => setQuote(null)}>
                  Cancel
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {tracked && (
        <div className="card">
          <h2>Settlement</h2>
          <p className="hint">
            The transfer is queued and settled by a background worker, so this
            updates on its own.
          </p>
          <div className="breakdown">
            <Line
              label="Status"
              value={<Badge status={tracked.status} />}
            />
            <Line label="Sent" value={money(tracked.zar_send_amount)} />
            <Line label="Settling" value={token(tracked.uctusd_amount)} />
            <Line
              label="XRPL transaction"
              value={<TxHash hash={tracked.xrpl_tx_hash} />}
            />
            <Line label="Settled at" value={when(tracked.settled_at)} />
          </div>
        </div>
      )}

      <div className="card">
        <h2>Your transfers</h2>
        {history.length === 0 ? (
          <p className="empty">Nothing sent yet.</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Created</th>
                  <th className="num">Sent</th>
                  <th className="num">Rate</th>
                  <th className="num">UCTUSD</th>
                  <th>Method</th>
                  <th>Status</th>
                  <th>XRPL hash</th>
                </tr>
              </thead>
              <tbody>
                {history.map((row) => (
                  <tr key={row.id}>
                    <td>{when(row.created_at)}</td>
                    <td className="num">{money(row.zar_send_amount)}</td>
                    <td className="num">{rate(row.fx_rate_used)}</td>
                    <td className="num">{Number(row.uctusd_amount).toFixed(6)}</td>
                    <td>{row.cash_in_method || '—'}</td>
                    <td>
                      <Badge status={row.status} />
                    </td>
                    <td>
                      <TxHash hash={row.xrpl_tx_hash} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}
