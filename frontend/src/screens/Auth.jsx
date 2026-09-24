import { useState } from 'react'

import * as api from '../api'
import { Alert } from '../ui.jsx'

/**
 * Registration and login (brief §4, "User Registration and Login").
 *
 * One component for both, because they share a form and differ by one field:
 * registering also asks for a name, and logs the new account straight in so
 * the demo never stops to log in twice.
 */
export default function Auth({ onAuthenticated }) {
  const [mode, setMode] = useState('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const registering = mode === 'register'

  async function submit(event) {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      if (registering) {
        await api.register({ email, password, fullName })
      }
      await api.login({ email, password })
      await onAuthenticated()
    } catch (err) {
      setError(err.detail || err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-wrap">
      <div className="brand">CrossFX</div>
      <p className="tagline">
        Send rand from South Africa. Your recipient is credited in UCTUSD,
        settled on the XRP Ledger Testnet.
      </p>

      <div className="card">
        <h2>{registering ? 'Create an account' : 'Log in'}</h2>
        <p className="hint">
          {registering
            ? 'You will complete a mock KYC check before you can send.'
            : 'Senders and recipients both use this login.'}
        </p>

        <Alert kind="error">{error}</Alert>

        <form onSubmit={submit}>
          {registering && (
            <div className="field">
              <label htmlFor="fullName">Full name</label>
              <input
                id="fullName"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                required
                autoComplete="name"
              />
            </div>
          )}

          <div className="field">
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
              placeholder="alice@example.com"
            />
          </div>

          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              autoComplete={registering ? 'new-password' : 'current-password'}
            />
          </div>

          <button className="primary" type="submit" disabled={busy}>
            {busy
              ? 'Working…'
              : registering
                ? 'Create account'
                : 'Log in'}
          </button>
        </form>

        <p className="switcher">
          {registering ? 'Already registered?' : 'No account yet?'}{' '}
          <button
            className="link"
            onClick={() => {
              setMode(registering ? 'login' : 'register')
              setError('')
            }}
          >
            {registering ? 'Log in' : 'Create one'}
          </button>
        </p>
      </div>
    </div>
  )
}
