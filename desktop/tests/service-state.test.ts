import { describe, expect, it } from 'vitest'
import { serviceLabel } from '../src/renderer/src/service-state'

describe('desktop service state presentation', () => {
  it('shows a concrete port release failure instead of a generic stopped state', () => {
    expect(serviceLabel({
      name: 'supervisor',
      port: 6002,
      pid: 123,
      state: 'unhealthy',
      restartBlocked: 'port_release_timeout'
    })).toBe('端口未释放，重启已阻止')
  })

  it('preserves unknown structured restart reasons for operators', () => {
    expect(serviceLabel({
      name: 'gateway',
      port: 6000,
      state: 'unhealthy',
      restartBlocked: 'owner_conflict'
    })).toBe('重启已阻止：owner_conflict')
  })

  it('distinguishes blocked restarts from generic control errors', () => {
    expect(serviceLabel({
      name: 'memory', port: 6001, state: 'unhealthy', restartBlocked: 'owner_conflict'
    })).toContain('owner_conflict')
  })
})
