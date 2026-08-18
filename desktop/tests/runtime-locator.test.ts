import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { findProjectRoot, normalizeMonitorUrl, resolveRuntimePaths } from '../src/main/runtime-locator'

describe('runtime locator', () => {
  it('finds the first directory with canonical source and project metadata', () => {
    const root = mkdtempSync(join(tmpdir(), 'voidcube-desktop-'))
    try {
      mkdirSync(join(root, 'src', 'voidcube'), { recursive: true })
      writeFileSync(join(root, 'pyproject.toml'), '')
      expect(findProjectRoot([join(root, 'missing'), root])).toBe(root)
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  it('uses an explicitly configured Python executable', () => {
    const runtime = resolveRuntimePaths({
      resourcesPath: 'C:\\voidcube-resources',
      env: {
        VOIDCUBE_DESKTOP_PYTHON: 'C:\\Python314\\python.exe',
        VOIDCUBE_DESKTOP_WORKSPACE: 'C:\\work',
        PATH: ''
      },
      platform: 'win32'
    })
    expect(runtime.pythonCommand).toBe('C:\\Python314\\python.exe')
    expect(runtime.cliArgs).toEqual(['-m', 'voidcube.interfaces.cli.main'])
  })
})

describe('monitor URL policy', () => {
  it('accepts local supervisor URLs', () => {
    expect(normalizeMonitorUrl(undefined)).toBe('http://127.0.0.1:6002/ui')
    expect(normalizeMonitorUrl('http://localhost:7000/ui')).toBe('http://localhost:7000/ui')
  })

  it('rejects remote and HTTPS supervisor URLs', () => {
    expect(() => normalizeMonitorUrl('https://127.0.0.1:6002/ui')).toThrow(/local loopback/)
    expect(() => normalizeMonitorUrl('http://example.com/ui')).toThrow(/local loopback/)
  })
})
