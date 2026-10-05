import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BrowserWindow } from 'electron'
import type { RuntimePaths } from '../src/main/runtime-locator'

const ptyMocks = vi.hoisted(() => ({
  spawn: vi.fn(),
  resize: vi.fn(),
  write: vi.fn(),
  kill: vi.fn()
}))

vi.mock('node-pty', () => ({
  spawn: ptyMocks.spawn
}))

import { TerminalSession } from '../src/main/terminal-session'

describe('terminal session sizing', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ptyMocks.spawn.mockReturnValue({
      pid: 4321,
      onData: vi.fn(),
      onExit: vi.fn(),
      resize: ptyMocks.resize,
      write: ptyMocks.write,
      kill: ptyMocks.kill
    })
  })

  it('starts the PTY with the renderer size received before process startup', () => {
    const window = {
      isDestroyed: () => false,
      webContents: { send: vi.fn() }
    } as unknown as BrowserWindow
    const runtime: RuntimePaths = {
      pythonCommand: 'python',
      pythonPrefixArgs: [],
      cliArgs: ['-m', 'voidcube.interfaces.cli.main'],
      workingDirectory: 'C:\\workspace'
    }
    const session = new TerminalSession(window, runtime)

    session.resize(116, 24)
    session.start()

    expect(ptyMocks.spawn).toHaveBeenCalledWith(
      'python',
      ['-m', 'voidcube.interfaces.cli.main'],
      expect.objectContaining({ cols: 116, rows: 24 })
    )
  })

  it('starts the bundled sidecar directly without Python module arguments', () => {
    const window = {
      isDestroyed: () => false,
      webContents: { send: vi.fn() }
    } as unknown as BrowserWindow
    const runtime: RuntimePaths = {
      pythonCommand: 'python',
      pythonPrefixArgs: [],
      cliArgs: ['-m', 'voidcube.interfaces.cli.main'],
      cliExecutable: 'C:\\app\\resources\\voidcube\\voidcube.exe',
      workingDirectory: 'C:\\workspace'
    }
    const session = new TerminalSession(window, runtime)

    session.start()

    expect(ptyMocks.spawn).toHaveBeenCalledWith(
      'C:\\app\\resources\\voidcube\\voidcube.exe',
      [],
      expect.objectContaining({
        cwd: 'C:\\workspace',
        env: expect.objectContaining({
          VOIDCUBE_DESKTOP: '1',
          VOIDCUBE_DESKTOP_MANAGED_SERVICES: '1'
        })
      })
    )
  })

  it('does not send the delayed quit command to a replacement PTY', () => {
    vi.useFakeTimers()
    try {
      const first = {
        pid: 4321,
        onData: vi.fn(),
        onExit: vi.fn(),
        resize: ptyMocks.resize,
        write: vi.fn(),
        kill: ptyMocks.kill
      }
      const second = {
        pid: 4322,
        onData: vi.fn(),
        onExit: vi.fn(),
        resize: ptyMocks.resize,
        write: vi.fn(),
        kill: ptyMocks.kill
      }
      ptyMocks.spawn.mockReturnValueOnce(first).mockReturnValueOnce(second)
      const window = {
        isDestroyed: () => false,
        webContents: { send: vi.fn() }
      } as unknown as BrowserWindow
      const runtime: RuntimePaths = {
        pythonCommand: 'python',
        pythonPrefixArgs: [],
        cliArgs: ['-m', 'voidcube.interfaces.cli.main'],
        workingDirectory: 'C:\\workspace'
      }
      const session = new TerminalSession(window, runtime)

      session.start()
      session.requestGracefulExit()
      session.kill()
      session.start()
      vi.advanceTimersByTime(120)

      expect(first.write).toHaveBeenCalledWith('\x03')
      expect(first.write).not.toHaveBeenCalledWith('/quit\r')
      expect(second.write).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('makes repeated graceful exit requests idempotent', () => {
    vi.useFakeTimers()
    try {
      const window = {
        isDestroyed: () => false,
        webContents: { send: vi.fn() }
      } as unknown as BrowserWindow
      const runtime: RuntimePaths = {
        pythonCommand: 'python',
        pythonPrefixArgs: [],
        cliArgs: ['-m', 'voidcube.interfaces.cli.main'],
        workingDirectory: 'C:\\workspace'
      }
      const session = new TerminalSession(window, runtime)

      session.start()
      session.requestGracefulExit()
      session.requestGracefulExit()
      vi.advanceTimersByTime(120)

      expect(ptyMocks.write).toHaveBeenCalledTimes(2)
      expect(ptyMocks.write).toHaveBeenNthCalledWith(1, '\x03')
      expect(ptyMocks.write).toHaveBeenNthCalledWith(2, '/quit\r')
    } finally {
      vi.useRealTimers()
    }
  })
})
