import { describe, expect, it } from 'vitest'

import { amountIn, money, rate, token, when } from './ui.jsx'

/**
 * The formatters are the only place the frontend does arithmetic on money,
 * so they are the only place it can get money wrong.
 *
 * The regression these pin down: the three functions used to call Number()
 * under a comment promising they did not, and the screens had drifted into
 * two different separator conventions for the same data.
 */
describe('money', () => {
  it('formats a rand amount with grouping and two places', () => {
    expect(money('1000.00')).toBe('R1 000.00')
    expect(money('1234567.89')).toBe('R1 234 567.89')
    expect(money('0.00')).toBe('R0.00')
  })

  it('uses the right symbol per currency', () => {
    expect(money('50.84', 'USD')).toBe('$50.84')
    expect(money('50.84', 'EUR')).toBe('50.84 EUR')
  })

  it('rounds to two places rather than truncating', () => {
    expect(money('2.345')).toBe('R2.35')
    expect(money('2.344')).toBe('R2.34')
  })

  it('never converts through a JS number', () => {
    // 0.1 + 0.2 in IEEE-754 is 0.30000000000000004. A value with more
    // significant digits than a double can hold must still round exactly.
    expect(money('9007199254740993.45')).toBe(
      'R9 007 199 254 740 993.45',
    )
  })

  it('renders an em dash for anything it cannot read', () => {
    expect(money(null)).toBe('—')
    expect(money(undefined)).toBe('—')
    expect(money('')).toBe('—')
    expect(money('not a number')).toBe('—')
  })
})

describe('token and rate', () => {
  it('shows UCTUSD at the six places the ledger column holds', () => {
    expect(token('51.351351')).toBe('51.351351 UCTUSD')
    expect(token('0')).toBe('0.000000 UCTUSD')
  })

  it('shows a rate at six places', () => {
    expect(rate('18.5')).toBe('18.500000')
  })

  it('agrees with money on the decimal separator', () => {
    // The bug this replaces: toLocaleString('en-ZA') gave "1 000,00" while
    // toFixed(6) gave "18.500000", so adjacent rows of one table disagreed.
    expect(money('1000').includes(',')).toBe(false)
    expect(rate('1000').includes(',')).toBe(false)
    expect(token('1000').includes(',')).toBe(false)
  })
})

describe('amountIn', () => {
  it('uses the precision the currency is held to', () => {
    expect(amountIn('12.5', 'UCTUSD')).toBe('12.500000')
    expect(amountIn('12.5', 'ZAR')).toBe('12.50')
    expect(amountIn('12.5', 'USD')).toBe('12.50')
  })
})

describe('when', () => {
  it('reads a UTC-tagged timestamp as UTC', () => {
    // The API tags its timestamps now. Rendering is locale- and
    // timezone-dependent, so the assertion is on the instant, not the text.
    const tagged = when('2026-09-24T12:00:00Z')
    const untagged = when('2026-09-24T12:00:00')
    expect(untagged).toBe(tagged)
  })

  it('renders an em dash for a missing timestamp', () => {
    expect(when(null)).toBe('—')
  })
})
