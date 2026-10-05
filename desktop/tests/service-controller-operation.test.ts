import { EventEmitter } from 'node:events'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('node:child_process', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:child_process')>()
  const mockedSpawn = vi.fn()
  return {
    ...actual,
    default: { spawn: mockedSpawn },
    spawn: mockedSpawn
  }
})

import { spawn } from 'node:child_process'
import { ServiceController } from '../src/main/service-controller'
import type { RuntimePaths } from '../src/main/runtime-locator'

function childProcess(): EventEmitter & {
  stdout: EventEmitter & { setEncoding: (encoding: string) => void }
  stderr: EventEmitter & { setEncoding: (encoding: string) => void }
  kill: () => void
} {
  const child = new EventEmitter() as ReturnType<typeof childProcess>
  child.stdout = Object.assign(new EventEmitter(), { setEncoding: (_encoding: string) => undefined })
  child.stderr = Object.assign(new EventEmitter(), { setEncoding: (_encoding: string) => undefined })
  child.kill = () => undefined
  return child
}

const runtime: RuntimePaths = {
  pythonCommand: 'python',
  pythonPrefixArgs: [],
  cliArgs: [],
  workingDirectory: 'C:\\workspace'
}

describe('service controller operation ownership', () => {
  beforeEach(() => {
    vi.mocked(spawn).mockReset()
  })

  it('keeps backend configuration and restart in one exclusive operation', async () => {
    vi.mocked(spawn).mockImplementation((_command, args) => {
      const child = childProcess()
      queueMicrotask(() => {
        if (args.includes('terminal.backend')) {
          child.emit('close', 0)
          return
        }
        child.stdout.emit('data', JSON.stringify({
          schemaVersion: 1,
          action: 'restart',
          ok: true,
          generatedAt: '2026-09-30T00:00:00+00:00',
          services: []
        }))
        child.emit('close', 0)
      })
      return child as never
    })

    const controller = new ServiceController(runtime)
    const changing = controller.setTerminalBackendAndRestart('podman')
    const competing = await controller.control('stop')
    const result = await changing

    expect(competing.ok).toBe(false)
    expect(competing.error).toBe('服务控制操作正在进行中')
    expect(result.ok).toBe(true)
    expect(spawn).toHaveBeenCalledTimes(2)
  })
})
