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
        {
          name: 'gateway',
          port: 6000,
          pid: 1234,
          state: 'healthy',
          registered: true,
          controlPlaneHealthy: true
        },
        { name: 'memory', port: 6001, pid: null, state: 'stopped' }
      ],
      plugins: [{
        name: 'goal_manager',
        displayName: '目标管理器',
        version: '0.1.0',
        description: '软件开发目标管理',
        enabled: true,
        capabilities: ['tools', 'service', 'web'],
        uiPath: '/ui/goal-manager/',
        service: { port: 6003, pid: 5678, state: 'healthy' }
      }],
      executionContext: {
        mode: 'sandbox',
        backend: 'podman',
        hostPlatform: 'windows',
        hostWorkingDirectory: 'C:\\repo',
        backendWorkingDirectory: '/workspace',
        workspaceName: 'repo',
        branch: 'main',
        worktree: false,
        workspaceMounted: true,
        fallbackToLocal: false
      }
    }))

    expect(result.services[0]).toEqual({
      name: 'gateway',
      port: 6000,
      pid: 1234,
      state: 'healthy',
      registered: true,
      controlPlaneHealthy: true
    })
    expect(result.executionContext?.backend).toBe('podman')
    expect(result.plugins?.[0]?.uiPath).toBe('/ui/goal-manager/')
  })

  it('accepts a structured restart-block reason from the service owner', () => {
    const result = parseServiceControlResult(JSON.stringify({
      schemaVersion: 1,
      action: 'restart',
      ok: false,
      generatedAt: '2026-08-09T00:00:00+00:00',
      error: 'service restart blocked: port release timeout',
      services: [{
        name: 'supervisor',
        port: 6002,
        pid: 123,
        state: 'unhealthy',
        restartBlocked: 'port_release_timeout'
      }]
    }))
    expect(result.ok).toBe(false)
    expect(result.services[0]?.restartBlocked).toBe('port_release_timeout')
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
      action: 'status',
      ok: false,
      generatedAt: 'now',
      services: [{ name: 'gateway', port: 6000, state: 'healthy', controlPlaneHealthy: 'yes' }]
    }))).toThrow(/unsupported response/)

    expect(() => parseServiceControlResult(JSON.stringify({
      schemaVersion: 1,
      action: 'status',
      ok: true,
      generatedAt: 'now',
      services: [{ name: 'gateway', port: 0, state: 'healthy' }]
    }))).toThrow(/unsupported response/)

    expect(() => parseServiceControlResult(JSON.stringify({
      schemaVersion: 1,
      action: 'status',
      ok: true,
      generatedAt: 'now',
      services: [{ name: 'gateway', port: 6000.5, state: 'healthy' }]
    }))).toThrow(/unsupported response/)

    for (const pid of [0, -1, 12.5]) {
      expect(() => parseServiceControlResult(JSON.stringify({
        schemaVersion: 1,
        action: 'status',
        ok: true,
        generatedAt: 'now',
        services: [{ name: 'gateway', port: 6000, pid, state: 'healthy' }]
      }))).toThrow(/unsupported response/)
    }

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

    expect(() => parseServiceControlResult(JSON.stringify({
      schemaVersion: 1,
      action: 'status',
      ok: false,
      generatedAt: 'now',
      services: [{ name: 'gateway', port: 6000, state: 'healthy', registered: 'yes' }]
    }))).toThrow(/unsupported response/)

    expect(() => parseServiceControlResult(JSON.stringify({
      schemaVersion: 1,
      action: 'status',
      ok: true,
      generatedAt: 'now',
      services: [],
      plugins: [{ name: 'broken' }]
    }))).toThrow(/unsupported response/)
  })
})
