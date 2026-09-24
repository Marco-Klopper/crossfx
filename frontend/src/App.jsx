import { useCallback, useEffect, useState } from 'react'

import * as api from './api'
import { Badge } from './ui.jsx'
import Auth from './screens/Auth.jsx'
import Kyc from './screens/Kyc.jsx'
import Beneficiaries from './screens/Beneficiaries.jsx'
import Send from './screens/Send.jsx'
import Wallet from './screens/Wallet.jsx'
import Admin from './screens/Admin.jsx'

/**
 * The shell: session state, the tab bar, and which screen is mounted.
 *
 * There is no router. The app is eight screens behind one login, so a tab
 * switcher does the whole job without a dependency — and the demo is driven
 * by clicking through in order anyway.
 */
export default function App() {
  const [me, setMe] = useState(null)
  const [isAdmin, setIsAdmin] = useState(false)
  const [tab, setTab] = useState('send')
  const [booting, setBooting] = useState(true)

  /**
   * Reloads the profile.
   *
   * GET /auth/me now returns is_admin. It previously did not, and admin-ness
   * was inferred by calling GET /admin/kyc/applications and reading a 403 as
   * "no" — which fetched the whole KYC table as a permission probe on every
   * refresh, and hid the Admin tab whenever that request merely failed.
   */
  const refresh = useCallback(async () => {
    if (!api.isLoggedIn()) {
      setMe(null)
      setIsAdmin(false)
      return
    }
    try {
      const profile = await api.getMe()
      setMe(profile)
      setIsAdmin(Boolean(profile.is_admin))
    } catch {
      // The token was rejected; api.js has already cleared it.
      setMe(null)
      setIsAdmin(false)
    }
  }, [])

  useEffect(() => {
    refresh().finally(() => setBooting(false))
  }, [refresh])

  // An expired token now returns the app to the login screen instead of
  // leaving a shell mounted where every action fails. api.js clears the
  // token; this is what tells React about it.
  useEffect(() => {
    api.setUnauthorizedHandler(() => {
      setMe(null)
      setIsAdmin(false)
      setTab('send')
    })
    return () => api.setUnauthorizedHandler(null)
  }, [])

  async function handleLogout() {
    await api.logout()
    setMe(null)
    setIsAdmin(false)
    setTab('send')
  }

  if (booting) {
    return <main className="empty">Loading…</main>
  }

  if (!me) {
    return <Auth onAuthenticated={refresh} />
  }

  const tabs = [
    { id: 'send', label: 'Send money' },
    { id: 'wallet', label: 'Wallet' },
    { id: 'beneficiaries', label: 'Recipients' },
    { id: 'kyc', label: 'KYC' },
    ...(isAdmin ? [{ id: 'admin', label: 'Admin' }] : []),
  ]

  return (
    <>
      <header className="app-header">
        <div className="app-header-top">
          <div className="brand">
            CrossFX<span>ZAR → UCTUSD on XRPL Testnet</span>
          </div>
          <div className="session">
            <span>{me.email}</span>
            <Badge status={me.kyc_status} />
            {isAdmin && <span className="badge info">admin</span>}
            <button className="link" onClick={handleLogout}>
              Log out
            </button>
          </div>
        </div>
        <nav className="tabs">
          {tabs.map((item) => (
            <button
              key={item.id}
              className={tab === item.id ? 'active' : ''}
              onClick={() => setTab(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>
      </header>

      <main>
        {tab === 'send' && <Send me={me} />}
        {tab === 'wallet' && <Wallet />}
        {tab === 'beneficiaries' && <Beneficiaries />}
        {tab === 'kyc' && <Kyc me={me} onReviewed={refresh} />}
        {tab === 'admin' && <Admin me={me} onKycReviewed={refresh} />}
      </main>
    </>
  )
}
