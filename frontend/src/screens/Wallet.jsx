import { useEffect, useState } from 'react'

import * as api from '../api'
import {
  Alert,
  Badge,
  TxHash,
  amountIn,
  money,
  rate,
  token,
  when,
} from '../ui.jsx'

/**
 * The recipient's custodial wallet and cash-out (brief §4, "Custodial
 * UCTUSD Wallet" and "Simulated Cash-Out"; spec §9.4, §10).
 *
 * The transaction table shows the six things the brief requires the wallet to
 * display: incoming transfers, outgoing/cash-out entries, status, date, amount
 * and the XRP Ledger transaction hash.
 *
 * There is no on-chain account behind any of this — a user's holdings are rows
 * in the internal multi-currency ledger (spec §9.1).
 */
const PAYOUT_CURRENCIES = ['USD', 'ZAR']

export default function Wallet() {
  const [balance, setBalance] = useState(null)
  const [transactions, setTransactions] = useState([])
  const [cashOuts, setCashOuts] = useState([])

  const [amount, setAmount] = useState('')
  const [currency, setCurrency] = useState('USD')

  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)

  async function load() {
    try {
      const [bal, txs, outs] = await Promise.all([
        api.getBalance(),
        api.getTransactions(),
        api.listCashOuts(),
      ])
      setBalance(bal)
      setTransactions(txs)
      setCashOuts(outs)
    } catch (err) {
      setError(err.detail || err.message)
    } finally {
      setLoaded(true)
    }
  }

  useEffect(() => {
    load()
  }, [])

  // Approving a cash-out queues an on-chain burn, so it is APPROVED and then
  // PROCESSING for a while before the worker completes it. Poll only while
  // one is in flight, so an idle wallet makes no background requests.
  const inFlight = cashOuts.some((row) =>
    ['requested', 'approved', 'processing'].includes(row.status),
  )
  useEffect(() => {
    if (!inFlight) return undefined
    const timer = setInterval(load, 3000)
    return () => clearInterval(timer)
  }, [inFlight])

  async function submit(event) {
    event.preventDefault()
    setError('')
    setNotice('')
    setBusy(true)
    try {
      const result = await api.requestCashOut({
        uctusdAmount: amount,
        payoutCurrency: currency,
        // Fresh per submission, so a retry of *this* request is deduped by
        // the API while a genuine second payout is not.
        idempotencyKey: crypto.randomUUID(),
      })
      setNotice(
        `Cash-out requested: ${token(result.uctusd_amount)} → ` +
          `${money(result.payout_amount, result.payout_currency)}. ` +
          'An administrator must approve the payout.',
      )
      setAmount('')
      await load()
    } catch (err) {
      // A 400 here is an insufficient balance — the ledger refuses to go
      // below zero, which is the "validate sufficient balance" step itself.
      setError(err.detail || err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="grid-2">
        <div className="card">
          <h2>Available balance</h2>
          <p className="balance">
            {balance ? amountIn(balance.uctusd_balance, 'UCTUSD') : '—'}
            <small>UCTUSD</small>
          </p>
          <p className="hint">
            Held in the platform&apos;s pooled custody and recorded in the internal
            ledger.
          </p>

          {balance && (
            <div className="breakdown">
              {balance.balances.map((row) => (
                <div className="breakdown-row" key={row.currency}>
                  <span className="label">{row.currency}</span>
                  <span className="value">
                    {amountIn(row.amount, row.currency)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card">
          <h2>Cash out to fiat</h2>
          <p className="hint">
            The cash-out fee is taken in UCTUSD before conversion, so you pay
            the same proportion whichever currency you choose.
          </p>

          <Alert kind="error">{error}</Alert>
          <Alert kind="success">{notice}</Alert>

          <form onSubmit={submit}>
            <div className="field-row">
              <div className="field">
                <label htmlFor="w-amount">Amount (UCTUSD)</label>
                <input
                  id="w-amount"
                  type="number"
                  step="0.000001"
                  min="0.000001"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="w-currency">Pay out in</label>
                <select
                  id="w-currency"
                  value={currency}
                  onChange={(e) => setCurrency(e.target.value)}
                >
                  {PAYOUT_CURRENCIES.map((code) => (
                    <option key={code} value={code}>
                      {code}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <button className="primary" type="submit" disabled={busy}>
              {busy ? 'Requesting…' : 'Request cash-out'}
            </button>
          </form>
        </div>
      </div>

      <div className="card">
        <h2>Transaction history</h2>
        {!loaded ? (
          <p className="empty">Loading…</p>
        ) : transactions.length === 0 ? (
          <p className="empty">No transactions yet.</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Direction</th>
                  <th className="num">Amount</th>
                  <th>Currency</th>
                  <th>Status</th>
                  <th>XRPL hash</th>
                </tr>
              </thead>
              <tbody>
                {transactions.map((row) => (
                  <tr key={row.id}>
                    <td>{when(row.created_at)}</td>
                    <td>{row.direction}</td>
                    <td className="num">
                      {amountIn(row.amount, row.currency)}
                    </td>
                    <td>{row.currency}</td>
                    <td>
                      <TxHash hash={row.xrpl_tx_hash} />
                    </td>
                    <td>
                      <Badge status={row.status} />
                      {row.failure_reason && (
                        <div className="hint" style={{ margin: 0 }}>
                          {row.failure_reason}
                        </div>
                      )}
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

      <div className="card">
        <h2>Your cash-outs</h2>
        {!loaded ? (
          <p className="empty">Loading…</p>
        ) : cashOuts.length === 0 ? (
          <p className="empty">No cash-outs requested.</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Requested</th>
                  <th className="num">UCTUSD</th>
                  <th className="num">Fee</th>
                  <th className="num">Rate</th>
                  <th className="num">Payout</th>
                  <th>Burn tx</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {cashOuts.map((row) => (
                  <tr key={row.id}>
                    <td>{when(row.requested_at)}</td>
                    <td className="num">{amountIn(row.uctusd_amount, 'UCTUSD')}</td>
                    <td className="num">
                      {amountIn(row.cash_out_fee_uctusd, 'UCTUSD')}
                    </td>
                    <td className="num">{rate(row.fx_rate_used)}</td>
                    <td className="num">
                      {money(row.payout_amount, row.payout_currency)}
                    </td>
                    <td>
                      <Badge status={row.status} />
                      {row.failure_reason && (
                        <div className="hint" style={{ margin: 0 }}>
                          {row.failure_reason}
                        </div>
                      )}
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
