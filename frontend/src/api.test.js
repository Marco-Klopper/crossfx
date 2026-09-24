import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from './api.js'

/**
 * The request layer, which every screen goes through.
 *
 * These pin the three things the audit found wrong with it: a non-JSON body
 * escaping as a raw SyntaxError, a 401 clearing the token without telling the
 * app, and a hung request with nothing to time it out.
 */
function reply(body, { status = 200, json = true } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => (json ? JSON.stringify(body) : body),
  }
}

beforeEach(() => {
  localStorage.clear()
  api.setUnauthorizedHandler(null)
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('error bodies', () => {
  it('reads FastAPI’s string detail', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => reply({ detail: 'Beneficiary not found' }, { status: 404 })),
    )
    await expect(api.listBeneficiaries()).rejects.toMatchObject({
      status: 404,
      detail: 'Beneficiary not found',
    })
  })

  it('flattens a 422 validation body into one message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        reply(
          {
            detail: [
              { loc: ['body', 'zar_send_amount'], msg: 'Input should be greater than 0' },
            ],
          },
          { status: 422 },
        ),
      ),
    )
    await expect(api.listBeneficiaries()).rejects.toMatchObject({
      status: 422,
      detail: 'zar_send_amount: Input should be greater than 0',
    })
  })

  it('turns a non-JSON body into an ApiError, not a SyntaxError', async () => {
    // A 502 HTML page from a proxy used to throw out of JSON.parse, so the
    // screens rendered "Unexpected token '<'" at the user.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        reply('<html><body>502 Bad Gateway</body></html>', { status: 502, json: false }),
      ),
    )
    const error = await api.listBeneficiaries().catch((e) => e)
    expect(error).toBeInstanceOf(api.ApiError)
    expect(error.status).toBe(502)
    expect(error.detail).toContain('502 Bad Gateway')
  })

  it('reports an unreachable API rather than a bare TypeError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    const error = await api.listBeneficiaries().catch((e) => e)
    expect(error).toBeInstanceOf(api.ApiError)
    expect(error.status).toBe(0)
    expect(error.detail).toContain('Is uvicorn running?')
  })
})

describe('an expired token', () => {
  it('clears the token and notifies the app', async () => {
    api.setToken('stale-token')
    const onUnauthorized = vi.fn()
    api.setUnauthorizedHandler(onUnauthorized)

    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        reply({ detail: 'Could not validate credentials' }, { status: 401 }),
      ),
    )

    await expect(api.getMe()).rejects.toMatchObject({ status: 401 })

    // Both halves matter. Clearing alone was the old behaviour, and it left
    // App with `me` still set: the logged-in shell stayed mounted and every
    // click failed until the user reloaded by hand.
    expect(api.getToken()).toBeNull()
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('does not fire the handler for other failures', async () => {
    api.setToken('good-token')
    const onUnauthorized = vi.fn()
    api.setUnauthorizedHandler(onUnauthorized)

    vi.stubGlobal('fetch', vi.fn(async () => reply({ detail: 'nope' }, { status: 403 })))

    await expect(api.getMe()).rejects.toMatchObject({ status: 403 })
    expect(api.getToken()).toBe('good-token')
    expect(onUnauthorized).not.toHaveBeenCalled()
  })
})

describe('requests', () => {
  it('sends the bearer token on authenticated calls only', async () => {
    api.setToken('abc123')
    const fetchMock = vi.fn(async () => reply([]))
    vi.stubGlobal('fetch', fetchMock)

    await api.listBeneficiaries()
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe('Bearer abc123')

    await api.register({ email: 'a@b.com', password: 'password123', fullName: 'A' })
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBeUndefined()
  })

  it('passes an Idempotency-Key through on cash-out', async () => {
    api.setToken('abc123')
    const fetchMock = vi.fn(async () => reply({}))
    vi.stubGlobal('fetch', fetchMock)

    await api.requestCashOut({
      uctusdAmount: '10.000000',
      payoutCurrency: 'USD',
      idempotencyKey: 'key-1',
    })
    expect(fetchMock.mock.calls[0][1].headers['Idempotency-Key']).toBe('key-1')
  })

  it('keeps the trailing slash on collection routes', async () => {
    // Without it the API answers 307, and a cross-origin redirect drops the
    // Authorization header - which surfaces as a confusing 401.
    api.setToken('abc123')
    const fetchMock = vi.fn(async () => reply([]))
    vi.stubGlobal('fetch', fetchMock)

    await api.listBeneficiaries()
    await api.listRemittances()

    expect(fetchMock.mock.calls[0][0]).toMatch(/\/beneficiaries\/$/)
    expect(fetchMock.mock.calls[1][0]).toMatch(/\/remittances\/$/)
  })

  it('gives up on a request that never responds', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url, options) =>
          new Promise((_resolve, reject) => {
            options.signal.addEventListener('abort', () => {
              const error = new Error('aborted')
              error.name = 'AbortError'
              reject(error)
            })
          }),
      ),
    )

    const pending = api.listBeneficiaries().catch((error) => error)
    // Without this timeout a hung backend left `busy` true forever and the
    // button read "Pricing..." until someone reloaded the page.
    await vi.advanceTimersByTimeAsync(20000)

    const error = await pending
    expect(error).toBeInstanceOf(api.ApiError)
    expect(error.status).toBe(0)
    expect(error.detail).toContain('did not respond')
  })
})
