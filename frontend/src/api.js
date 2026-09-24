/**
 * The CrossFX API client — the single place the frontend talks to the backend.
 *
 * Every screen imports from here rather than calling fetch() itself, so the
 * base URL, the JWT and the error shape are defined once. If the API moves,
 * this file changes and nothing else does.
 *
 * The backend is FastAPI (backend/app/main.py) and its OpenAPI docs at
 * http://127.0.0.1:8000/docs are the authoritative contract for every shape
 * below.
 */

// The API's own origin. The backend allows this frontend cross-origin
// (CORS_ORIGINS in backend/.env), so we call it directly with no dev proxy.
export const API_BASE = 'http://127.0.0.1:8000'

const TOKEN_KEY = 'crossfx.token'

// -- the token -------------------------------------------------------------

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

export function isLoggedIn() {
  return Boolean(getToken())
}

// -- the request layer -----------------------------------------------------

/**
 * An API call that failed. `status` is the HTTP code and `detail` is the
 * backend's own message, which the screens show verbatim — the API writes
 * genuinely useful ones ("R3200 exceeds the daily limit of R3000: R3000
 * remaining"), and paraphrasing them would only lose information.
 */
export class ApiError extends Error {
  constructor(status, detail) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

/**
 * Pulls a readable message out of a FastAPI error body.
 *
 * `detail` is a string for the errors we raise deliberately (HTTPException),
 * but a list of field objects for a 422 from Pydantic validation, so both
 * shapes have to be handled or a validation error renders as "[object Object]".
 */
function messageFrom(body, status) {
  const detail = body?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : null
        return field ? `${field}: ${item.msg}` : item.msg
      })
      .join('; ')
  }
  return `Request failed (${status})`
}

async function request(path, { method = 'GET', body, auth = true } = {}) {
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth) {
    const token = getToken()
    if (token) headers.Authorization = `Bearer ${token}`
  }

  let response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (networkError) {
    // fetch() only rejects when the request never completed — the API is
    // down, or CORS refused it before a response existed.
    throw new ApiError(
      0,
      `Could not reach the API at ${API_BASE}. Is uvicorn running?`,
    )
  }

  if (response.status === 204) return null

  const text = await response.text()
  const payload = text ? JSON.parse(text) : null

  if (!response.ok) {
    if (response.status === 401 && auth) {
      // The token is expired or invalid; drop it so the app returns to login
      // rather than looping on 401s with a token it will never use again.
      clearToken()
    }
    throw new ApiError(response.status, messageFrom(payload, response.status))
  }

  return payload
}

// -- auth (backend/app/routers/auth.py) ------------------------------------

export function register({ email, password, fullName }) {
  return request('/auth/register', {
    method: 'POST',
    auth: false,
    body: { email, password, full_name: fullName },
  })
}

/**
 * Logs in and stores the token.
 *
 * Note this posts JSON to /auth/login. The API also exposes /auth/token, but
 * that is an OAuth2 form shim that exists only so Swagger's Authorize button
 * works — it is not the endpoint a frontend should call.
 */
export async function login({ email, password }) {
  const data = await request('/auth/login', {
    method: 'POST',
    auth: false,
    body: { email, password },
  })
  setToken(data.access_token)
  return data
}

export function logout() {
  // JWTs are stateless and the backend has nothing to revoke, so the real
  // logout is dropping the token here. The call is made anyway because the
  // endpoint exists and a server-side session store would need it.
  const done = request('/auth/logout', { method: 'POST' }).catch(() => null)
  clearToken()
  return done
}

/** Profile, KYC status and the caller's applicable transaction limits. */
export function getMe() {
  return request('/auth/me')
}

// -- KYC (backend/app/routers/kyc.py) --------------------------------------

/** The eight fields the brief's mock-KYC section requires. */
export function applyForKyc({
  fullName,
  dateOfBirth,
  nationality,
  identificationNumber,
  residentialAddress,
  mobileNumber,
  email,
  sourceOfFunds,
}) {
  return request('/kyc/apply', {
    method: 'POST',
    body: {
      full_name: fullName,
      date_of_birth: dateOfBirth,
      nationality,
      identification_number: identificationNumber,
      residential_address: residentialAddress,
      mobile_number: mobileNumber,
      email,
      source_of_funds: sourceOfFunds,
    },
  })
}

export function getKycStatus() {
  return request('/kyc/status')
}

// -- beneficiaries (backend/app/routers/beneficiaries.py) ------------------
//
// The trailing slashes below are deliberate: these routes are registered as
// "/" under a prefix, so /beneficiaries without it answers with a 307 to
// /beneficiaries/ — and a cross-origin redirect drops the Authorization
// header, which would turn into a confusing 401.

export function listBeneficiaries() {
  return request('/beneficiaries/')
}

export function createBeneficiary({
  fullName,
  contact,
  country,
  preferredPayoutCurrency,
  relationshipToSender,
}) {
  return request('/beneficiaries/', {
    method: 'POST',
    body: {
      full_name: fullName,
      contact,
      country,
      preferred_payout_currency: preferredPayoutCurrency,
      relationship_to_sender: relationshipToSender,
    },
  })
}

export function deleteBeneficiary(beneficiaryId) {
  return request(`/beneficiaries/${beneficiaryId}`, { method: 'DELETE' })
}

// -- remittances (backend/app/routers/remittances.py) ----------------------

/**
 * Prices a send and persists it as a `quoted` remittance.
 *
 * The response carries every figure the brief requires a quotation to
 * disclose — rate, transaction fee, FX margin, net converted, UCTUSD
 * received, cash-out fee, estimated payout — plus `effective_rate` (the
 * all-in ZAR per UCTUSD) and a `limits` block with the sender's remaining
 * headroom. The quote screen should show all of it.
 */
export function createQuote({ beneficiaryId, zarSendAmount }) {
  return request('/remittances/quote', {
    method: 'POST',
    body: {
      beneficiary_id: beneficiaryId,
      zar_send_amount: zarSendAmount,
    },
  })
}

/**
 * Confirms the simulated ZAR cash-in, which queues the remittance for
 * settlement. `method` is one of agent_cash | bank_transfer | card.
 *
 * Read `queued` on the response before showing anything: a 200 with
 * `queued: false` means the cash-in WAS confirmed but the settlement queue
 * was unreachable. That is not a failure and the sender must not be told to
 * pay again — it settles once the queue recovers.
 */
export function confirmCashIn(remittanceId, method) {
  return request(`/remittances/${remittanceId}/confirm-cash-in`, {
    method: 'POST',
    body: { cash_in_method: method },
  })
}

export function listRemittances() {
  return request('/remittances/')
}

/** Status, and the XRPL transaction hash once settled. Poll this. */
export function getRemittance(remittanceId) {
  return request(`/remittances/${remittanceId}`)
}

// -- wallet (backend/app/routers/wallet.py) --------------------------------

/** UCTUSD balance plus the full multi-currency ledger view. */
export function getBalance() {
  return request('/wallet/balance')
}

/** Incoming/outgoing history: currency, amount, status, date, XRPL hash. */
export function getTransactions() {
  return request('/wallet/transactions')
}

/**
 * Requests a fiat payout. The UCTUSD leaves the balance immediately; the
 * fiat arrives when an admin approves it.
 */
export function requestCashOut({ uctusdAmount, payoutCurrency }) {
  return request('/wallet/cash-out', {
    method: 'POST',
    body: { uctusd_amount: uctusdAmount, payout_currency: payoutCurrency },
  })
}

export function listCashOuts() {
  return request('/wallet/cash-outs')
}

// -- admin (backend/app/routers/admin.py) ----------------------------------
//
// Every route here needs an account with is_admin set, which no API route can
// grant — it is created with `python -m scripts.create_admin`.

export function listKycApplications(status) {
  const query = status ? `?status=${encodeURIComponent(status)}` : ''
  return request(`/admin/kyc/applications${query}`)
}

export function approveKyc(applicationId) {
  return request(`/admin/kyc/${applicationId}/approve`, { method: 'POST' })
}

export function rejectKyc(applicationId, reason) {
  return request(`/admin/kyc/${applicationId}/reject`, {
    method: 'POST',
    body: { reason },
  })
}

/**
 * The mock payment-service cash-in confirmation, and the manual retry for a
 * remittance whose settlement message never reached the queue.
 */
export function confirmPayment(remittanceId, method = 'bank_transfer') {
  return request(`/admin/remittances/${remittanceId}/confirm-payment`, {
    method: 'POST',
    body: { cash_in_method: method },
  })
}

export function listAllCashOuts(status) {
  const query = status ? `?status=${encodeURIComponent(status)}` : ''
  return request(`/admin/cash-outs${query}`)
}

export function approveCashOut(cashOutId) {
  return request(`/admin/cash-outs/${cashOutId}/approve`, { method: 'POST' })
}

export function rejectCashOut(cashOutId, reason) {
  return request(`/admin/cash-outs/${cashOutId}/reject`, {
    method: 'POST',
    body: { reason },
  })
}
