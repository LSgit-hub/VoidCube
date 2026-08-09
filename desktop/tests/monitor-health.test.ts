import { describe, expect, it } from 'vitest'
import { MonitorHealthGate } from '../src/renderer/src/monitor-health'

describe('monitor health gate', () => {
  it('keeps the current monitor through transient probe failures', () => {
    const gate = new MonitorHealthGate(3)

    expect(gate.observe(false)).toBe('keep')
    expect(gate.observe(true)).toBe('keep')
    expect(gate.observe(false)).toBe('keep')
    expect(gate.observe(false)).toBe('keep')
  })

  it('replaces the monitor only after consecutive failures reach the limit', () => {
    const gate = new MonitorHealthGate(3)

    expect(gate.observe(false)).toBe('keep')
    expect(gate.observe(false)).toBe('keep')
    expect(gate.observe(false)).toBe('replace')
  })

  it('rejects an invalid failure limit', () => {
    expect(() => new MonitorHealthGate(0)).toThrow(/positive integer/)
  })
})
