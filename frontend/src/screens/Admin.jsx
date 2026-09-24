import { useEffect, useState } from 'react'

import * as api from '../api'
import { Alert, Badge, money, token, rate, when } from '../ui.jsx'

/**
 * The administrator interface (brief §5, "administrator interface";
 * deliverable ii, "administrator approval functions").
 *
 * Three jobs, which are the three places a human stands in this corridor:
 * approving KYC, confirming that a sender's rand arrived, and releasing a
 * recipient's payout.
 */
export default function Admin({ onKycReviewed }) {
  const [applications, setApplications] = useState([])
  const [cashOuts, setCashOuts] = useState([])
  const [remittanceId, setRemittanceId] = useState('')

  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  async function load() {
    try {
      const [apps, outs] = await Promise.all([
        api.listKycApplications('pending'),
        api.listAllCashOuts('requested'),
      ])
      setApplications(apps)
      setCashOuts(outs)
    } catch (err) {
      setError(err.detail || err.message)
    }
  }

  useEffect(() => {
    load()
  }, [])

  /** Runs one admin action, then reloads both queues. */
  async function act(work, successMessage) {
    setError('')
    setNotice('')
    setBusy(true)
    try {
      await work()
      setNotice(successMessage)
      await load()
    } catch (err) {
      setError(err.detail || err.message)
    } finally {
      setBusy(false)
    }
  }

  function reviewKyc(row, approve) {
    const reason = approve
      ? null
      : window.prompt(`Why is ${row.full_name}'s application being rejected?`)
    if (!approve && reason === null) return
    act(
      () =>
        approve ? api.approveKyc(row.id) : api.rejectKyc(row.id, reason),
      `${row.full_name}'s application ${approve ? 'approved' : 'rejected'}.`,
    ).then(onKycReviewed)
  }

  function reviewCashOut(row, approve) {
    const reason = approve
      ? null
      : window.prompt('Why is this payout being rejected?')
    if (!approve && reason === null) return
    act(
      () =>
        approve ? api.approveCashOut(row.id) : api.rejectCashOut(row.id, reason),
      approve
        ? 'Payout released and the fiat leg credited.'
        : 'Payout rejected and the UCTUSD refunded.',
    )
  }

  function confirmPayment(event) {
    event.preventDefault()
    act(
      async () => {
        const result = await api.confirmPayment(remittanceId.trim())
        if (!result.queued) throw new Error(result.detail)
        setRemittanceId('')
      },
      'Payment confirmed and queued for settlement.',
    )
  }

  return (
    <>
      <Alert kind="error">{error}</Alert>
      <Alert kind="success">{notice}</Alert>

      <div className="card">
        <h2>KYC review queue</h2>
        <p className="hint">
          Pending applications. Approving one sets the sender's limits to
          R3,000 daily and R25,000 monthly.
        </p>
        {applications.length === 0 ? (
          <p className="empty">Nothing waiting for review.</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Applicant</th>
                  <th>Email</th>
                  <th>Nationality</th>
                  <th>ID number</th>
                  <th>Submitted</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {applications.map((row) => (
                  <tr key={row.id}>
                    <td>{row.full_name}</td>
                    <td>{row.email}</td>
                    <td>{row.nationality}</td>
                    <td className="mono">{row.identification_number}</td>
                    <td>{when(row.submitted_at)}</td>
                    <td>
                      <div className="actions">
                        <button
                          className="primary"
                          disabled={busy}
                          onClick={() => reviewKyc(row, true)}
                        >
                          Approve
                        </button>
                        <button
                          className="danger"
                          disabled={busy}
                          onClick={() => reviewKyc(row, false)}
                        >
                          Reject
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <h2>Payout queue</h2>
        <p className="hint">
          Requested cash-outs, oldest first. The UCTUSD was already debited when
          the recipient asked — approving credits the fiat, rejecting refunds
          the token.
        </p>
        {cashOuts.length === 0 ? (
          <p className="empty">No payouts waiting.</p>
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
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {cashOuts.map((row) => (
                  <tr key={row.id}>
                    <td>{when(row.requested_at)}</td>
                    <td className="num">{Number(row.uctusd_amount).toFixed(6)}</td>
                    <td className="num">
                      {Number(row.cash_out_fee_uctusd).toFixed(6)}
                    </td>
                    <td className="num">{rate(row.fx_rate_used)}</td>
                    <td className="num">
                      {money(row.payout_amount, row.payout_currency)}
                    </td>
                    <td>
                      <Badge status={row.status} />
                    </td>
                    <td>
                      <div className="actions">
                        <button
                          className="primary"
                          disabled={busy}
                          onClick={() => reviewCashOut(row, true)}
                        >
                          Approve
                        </button>
                        <button
                          className="danger"
                          disabled={busy}
                          onClick={() => reviewCashOut(row, false)}
                        >
                          Reject
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <h2>Mock payment service</h2>
        <p className="hint">
          Confirms a sender's ZAR cash-in on their behalf. This is also the
          retry for a remittance whose settlement message never reached the
          queue — republishing is safe, because the worker claims each
          remittance exactly once.
        </p>
        <form onSubmit={confirmPayment}>
          <div className="field">
            <label htmlFor="a-remittance">Remittance ID</label>
            <input
              id="a-remittance"
              className="mono"
              value={remittanceId}
              onChange={(e) => setRemittanceId(e.target.value)}
              placeholder="e.g. 3f1c…"
              required
            />
          </div>
          <button className="primary" type="submit" disabled={busy}>
            {busy ? 'Confirming…' : 'Confirm payment received'}
          </button>
        </form>
      </div>
    </>
  )
}
