import { describe, expect, it } from 'vitest'
import { parseServiceControlResult } from '../src/main/service-controller'

describe('service control protocol', () => {
  it('accepts the versioned Python service snapshot', () => {
    const result = parseServiceControlResult(JSON.stringify({
      schemaVersion: 1,
      action: 'status',
      ok: true,
      generatedAt: '2026-08-09T00:00:00+00:00',
      services: [
        { name: 'gateway', port: 6000, pid: 1234, state: 'healthy' },
        { name: 'memory', port: 6001, pid: null, state: 'stopped' }
      ]
    }))

    expect(result.services[0]).toEqual({
      name: 'gateway',
      port: 6000,
      pid: 1234,
      state: 'healthy'
    })
  })

  it('rejects unknown protocol versions and service states', () => {
    expect(() => parseServiceControlResult(JSON.stringify({
      schemaVersion: 2,
      action: 'status',
      ok: true,
      generatedAt: 'now',
      services: []
    }))).toThrow(/unsupported response/)

    expect(() => parseServiceControlResult(JSON.stringify({
      schemaVersion: 1,
      action: 'remove',
      ok: false,
      generatedAt: 'now',
      services: []
    }))).toThrow(/unsupported response/)

    expect(() => parseServiceControlResult(JSON.stringify({
      schemaVersion: 1,
      action: 'status',
      ok: false,
      generatedAt: 'now',
      services: [{ name: 'gateway', port: 6000, state: 'unknown' }]
    }))).toThrow(/unsupported response/)
  })
})
