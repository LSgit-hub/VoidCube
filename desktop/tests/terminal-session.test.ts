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
      cliArgs: ['voidcube.py'],
      workingDirectory: 'C:\\workspace'
    }
    const session = new TerminalSession(window, runtime)

    session.resize(116, 24)
    session.start()

    expect(ptyMocks.spawn).toHaveBeenCalledWith(
      'python',
      ['voidcube.py'],
      expect.objectContaining({ cols: 116, rows: 24 })
    )
  })
})
