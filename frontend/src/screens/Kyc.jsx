import { useEffect, useState } from 'react'

import * as api from '../api'
import { Alert, Badge, money, when } from '../ui.jsx'

/**
 * Mock KYC (brief §4, "Mock KYC") — the eight fields the brief names, the
 * resulting status, and the transaction limits that status unlocks.
 *
 * The limits are shown here rather than only on the send screen because an
 * unverified sender's limit is R0, which is what makes "you must complete KYC
 * before sending" a consequence of the limit model rather than a separate
 * rule (spec §6).
 */
const EMPTY = {
  fullName: '',
  dateOfBirth: '',
  nationality: '',
  identificationNumber: '',
  residentialAddress: '',
  mobileNumber: '',
  email: '',
  sourceOfFunds: '',
}

function ProfileCard({ me, onSaved }) {
  const [fullName, setFullName] = useState(me.full_name)
  const [email, setEmail] = useState(me.email)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  async function save(event) {
    event.preventDefault()
    setError('')
    setNotice('')
    setBusy(true)
    try {
      await api.updateProfile({ fullName, email })
      await onSaved()
      setNotice('Profile updated.')
    } catch (err) {
      setError(err.detail || err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <h2>Your profile</h2>
      <p className="hint">
        Name and email. KYC status and admin rights cannot be changed here.
      </p>
      <form onSubmit={save}>
        <div className="field">
          <label htmlFor="profile-name">Full name</label>
          <input
            id="profile-name"
            value={fullName}
            onChange={(event) => setFullName(event.target.value)}
            required
          />
        </div>
        <div className="field">
          <label htmlFor="profile-email">Email</label>
          <input
            id="profile-email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          />
        </div>
        {error && <Alert kind="error">{error}</Alert>}
        {notice && <Alert kind="success">{notice}</Alert>}
        <button type="submit" disabled={busy}>
          {busy ? 'Saving…' : 'Save profile'}
        </button>
      </form>
    </div>
  )
}

export default function Kyc({ me, onReviewed }) {
  const [form, setForm] = useState({
    ...EMPTY,
    fullName: me.full_name,
    email: me.email,
  })
  const [status, setStatus] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  async function load() {
    try {
      setStatus(await api.getKycStatus())
    } catch (err) {
      setError(err.detail || err.message)
    }
  }

  useEffect(() => {
    load()
  }, [])

  // Keep the prefilled identity fields in step with the session. They were
  // seeded at mount only, so a profile refresh that did not remount this
  // screen left a stale name and email in the form.
  useEffect(() => {
    setForm((previous) => ({
      ...previous,
      fullName: previous.fullName || me.full_name,
      email: previous.email || me.email,
    }))
  }, [me.full_name, me.email])

  function set(field) {
    // Functional, so browser autofill firing several fields in one tick
    // cannot drop all but the last.
    return (event) => {
      const { value } = event.target
      setForm((previous) => ({ ...previous, [field]: value }))
    }
  }

  async function submit(event) {
    event.preventDefault()
    setError('')
    setNotice('')
    setBusy(true)
    try {
      await api.applyForKyc(form)
      setNotice('Application submitted. An administrator must approve it.')
      await load()
      await onReviewed()
    } catch (err) {
      setError(err.detail || err.message)
    } finally {
      setBusy(false)
    }
  }

  const current = status?.kyc_status || me.kyc_status
  const canApply = current === 'not_started' || current === 'rejected'

  return (
    <>
      <ProfileCard me={me} onSaved={onReviewed} />
      <div className="card">
        <h2>KYC status</h2>
        <p className="hint">
          Only an approved sender may request a quote.
        </p>

        <div className="breakdown">
          <div className="breakdown-row">
            <span className="label">Current status</span>
            <span className="value">
              <Badge status={current} />
            </span>
          </div>
          <div className="breakdown-row">
            <span className="label">Daily sending limit</span>
            <span className="value">{money(me.limits.daily_limit_zar)}</span>
          </div>
          <div className="breakdown-row">
            <span className="label">Monthly sending limit</span>
            <span className="value">{money(me.limits.monthly_limit_zar)}</span>
          </div>
          {status?.latest_application && (
            <>
              <div className="breakdown-row">
                <span className="label">Submitted</span>
                <span className="value">
                  {when(status.latest_application.submitted_at)}
                </span>
              </div>
              <div className="breakdown-row">
                <span className="label">Reviewed</span>
                <span className="value">
                  {when(status.latest_application.reviewed_at)}
                </span>
              </div>
            </>
          )}
        </div>

        {status?.latest_application?.rejection_reason && (
          <div style={{ marginTop: 16 }}>
            <Alert kind="error">
              Rejected: {status.latest_application.rejection_reason}
            </Alert>
          </div>
        )}
      </div>

      {/*
        Outside the canApply branch on purpose. Submitting sets `notice`, then
        load() flips the status to `pending` -- which unmounts that branch on
        the very same render, so the confirmation this sets could never be
        seen. The load() error path had the same problem in reverse: it is
        reachable whatever the status, but the alert was only mounted while
        the form was.
      */}
      <Alert kind="error">{error}</Alert>
      <Alert kind="success">{notice}</Alert>

      {canApply ? (
        <div className="card">
          <h2>Submit a KYC application</h2>
          <p className="hint">
            Mock verification — nothing is checked against a real identity
            register.
          </p>

          <form onSubmit={submit}>
            <div className="field-row">
              <div className="field">
                <label htmlFor="k-name">Full name</label>
                <input id="k-name" value={form.fullName} onChange={set('fullName')} required />
              </div>
              <div className="field">
                <label htmlFor="k-dob">Date of birth</label>
                <input
                  id="k-dob"
                  type="date"
                  value={form.dateOfBirth}
                  onChange={set('dateOfBirth')}
                  required
                />
              </div>
            </div>

            <div className="field-row">
              <div className="field">
                <label htmlFor="k-nat">Nationality</label>
                <input
                  id="k-nat"
                  value={form.nationality}
                  onChange={set('nationality')}
                  placeholder="South African"
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="k-id">Identification number</label>
                <input
                  id="k-id"
                  value={form.identificationNumber}
                  onChange={set('identificationNumber')}
                  required
                />
              </div>
            </div>

            <div className="field">
              <label htmlFor="k-addr">Residential address</label>
              <input
                id="k-addr"
                value={form.residentialAddress}
                onChange={set('residentialAddress')}
                placeholder="1 Long Street, Cape Town"
                required
              />
            </div>

            <div className="field-row">
              <div className="field">
                <label htmlFor="k-mobile">Mobile number</label>
                <input
                  id="k-mobile"
                  value={form.mobileNumber}
                  onChange={set('mobileNumber')}
                  placeholder="+27821234567"
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="k-email">Email address</label>
                <input
                  id="k-email"
                  type="email"
                  value={form.email}
                  onChange={set('email')}
                  required
                />
              </div>
            </div>

            <div className="field">
              <label htmlFor="k-funds">Source of funds</label>
              <input
                id="k-funds"
                value={form.sourceOfFunds}
                onChange={set('sourceOfFunds')}
                placeholder="Salary"
                required
              />
            </div>

            <button className="primary" type="submit" disabled={busy}>
              {busy ? 'Submitting…' : 'Submit application'}
            </button>
          </form>
        </div>
      ) : (
        <div className="card">
          <Alert kind={current === 'approved' ? 'success' : 'info'}>
            {current === 'approved'
              ? 'Your KYC application has been approved — you can send money.'
              : 'Your application is with an administrator for review.'}
          </Alert>
        </div>
      )}
    </>
  )
}
