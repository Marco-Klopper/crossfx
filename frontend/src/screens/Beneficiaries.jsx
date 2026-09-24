import { useEffect, useState } from 'react'

import * as api from '../api'
import { Alert, when } from '../ui.jsx'

/**
 * Beneficiary management (brief §4, "Beneficiary Management") — the five
 * fields the brief names.
 *
 * The contact field carries a warning the API enforces later: under pooled
 * custody the settlement credit lands in the recipient's internal wallet, so
 * the recipient must already hold a CrossFX account under this exact address.
 * The backend checks that at cash-in rather than here, and returns a 409 —
 * telling the sender up front is cheaper than letting them find out when they
 * try to pay.
 */
const CURRENCIES = ['USD', 'ZAR', 'EUR', 'GBP', 'UCTUSD']

const EMPTY = {
  fullName: '',
  contact: '',
  country: '',
  preferredPayoutCurrency: 'USD',
  relationshipToSender: '',
}

export default function Beneficiaries() {
  const [rows, setRows] = useState([])
  const [form, setForm] = useState(EMPTY)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  async function load() {
    try {
      setRows(await api.listBeneficiaries())
    } catch (err) {
      setError(err.detail || err.message)
    }
  }

  useEffect(() => {
    load()
  }, [])

  function set(field) {
    return (event) => setForm({ ...form, [field]: event.target.value })
  }

  async function submit(event) {
    event.preventDefault()
    setError('')
    setNotice('')
    setBusy(true)
    try {
      await api.createBeneficiary(form)
      setNotice(`${form.fullName} added.`)
      setForm(EMPTY)
      await load()
    } catch (err) {
      setError(err.detail || err.message)
    } finally {
      setBusy(false)
    }
  }

  async function remove(row) {
    setError('')
    setNotice('')
    try {
      await api.deleteBeneficiary(row.id)
      await load()
    } catch (err) {
      // A 409 means the recipient has remittances against them — the
      // settlement worker resolves the recipient through this row, so it
      // cannot be deleted.
      setError(err.detail || err.message)
    }
  }

  return (
    <>
      <div className="card">
        <h2>Add a recipient</h2>
        <p className="hint">
          The contact must be the email address the recipient registered with —
          that is how their wallet is found at settlement.
        </p>

        <Alert kind="error">{error}</Alert>
        <Alert kind="success">{notice}</Alert>

        <form onSubmit={submit}>
          <div className="field-row">
            <div className="field">
              <label htmlFor="b-name">Full name</label>
              <input id="b-name" value={form.fullName} onChange={set('fullName')} required />
            </div>
            <div className="field">
              <label htmlFor="b-contact">Mobile number or email</label>
              <input
                id="b-contact"
                value={form.contact}
                onChange={set('contact')}
                placeholder="bob@example.com"
                required
              />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="b-country">Country</label>
              <input
                id="b-country"
                value={form.country}
                onChange={set('country')}
                placeholder="United States"
                required
              />
            </div>
            <div className="field">
              <label htmlFor="b-currency">Preferred payout currency</label>
              <select
                id="b-currency"
                value={form.preferredPayoutCurrency}
                onChange={set('preferredPayoutCurrency')}
              >
                {CURRENCIES.map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="b-rel">Relationship to you</label>
              <input
                id="b-rel"
                value={form.relationshipToSender}
                onChange={set('relationshipToSender')}
                placeholder="Brother"
                required
              />
            </div>
          </div>

          <button className="primary" type="submit" disabled={busy}>
            {busy ? 'Saving…' : 'Add recipient'}
          </button>
        </form>
      </div>

      <div className="card">
        <h2>Your recipients</h2>
        {rows.length === 0 ? (
          <p className="empty">No recipients yet.</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Contact</th>
                  <th>Country</th>
                  <th>Payout</th>
                  <th>Relationship</th>
                  <th>Added</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td>{row.full_name}</td>
                    <td>{row.contact}</td>
                    <td>{row.country}</td>
                    <td>{row.preferred_payout_currency}</td>
                    <td>{row.relationship_to_sender}</td>
                    <td>{when(row.created_at)}</td>
                    <td>
                      <button className="link" onClick={() => remove(row)}>
                        Remove
                      </button>
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
