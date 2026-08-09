import type { BrowserWindow } from 'electron'
import * as pty from 'node-pty'
import type { RuntimePaths } from './runtime-locator'
import type { TerminalState } from '../shared/contracts'

const DEFAULT_COLUMNS = 120
const DEFAULT_ROWS = 30

export class TerminalSession {
  private process?: pty.IPty
  private state: TerminalState = { phase: 'stopped' }
  private columns = DEFAULT_COLUMNS
  private rows = DEFAULT_ROWS

  constructor(
    private readonly window: BrowserWindow,
    private readonly runtime: RuntimePaths
  ) {}

  currentState(): TerminalState {
    return { ...this.state }
  }

  isActive(): boolean {
    return this.process !== undefined
  }

  start(): TerminalState {
    if (this.process) return this.currentState()
    this.publish({ phase: 'starting' })

    try {
      const args = [...this.runtime.pythonPrefixArgs, ...this.runtime.cliArgs]
      const child = pty.spawn(this.runtime.pythonCommand, args, {
        name: process.platform === 'win32' ? 'xterm-256color' : 'xterm-256color',
        cols: this.columns,
        rows: this.rows,
        cwd: this.runtime.workingDirectory,
        env: {
          ...process.env,
          TERM: 'xterm-256color',
          COLORTERM: 'truecolor',
          PYTHONUTF8: '1',
          VOIDCUBE_DESKTOP: '1',
          VOIDCUBE_DESKTOP_MANAGED_SERVICES: '1'
        } as Record<string, string>
      })
      this.process = child
      child.onData((data) => this.send('terminal:data', data))
      child.onExit(({ exitCode }) => {
        if (this.process !== child) return
        this.process = undefined
        this.publish({ phase: 'exited', exitCode, message: `CLI exited with code ${exitCode}` })
      })
      this.publish({ phase: 'running', pid: child.pid })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      this.process = undefined
      this.publish({ phase: 'error', message })
    }
    return this.currentState()
  }

  restart(): TerminalState {
    this.kill()
    return this.start()
  }

  write(data: string): void {
    this.process?.write(data)
  }

  resize(columns: number, rows: number): void {
    this.columns = Math.max(20, Math.min(500, Math.floor(columns)))
    this.rows = Math.max(5, Math.min(200, Math.floor(rows)))
    this.process?.resize(this.columns, this.rows)
  }

  requestGracefulExit(): void {
    if (!this.process) return
    this.process.write('\x03')
    setTimeout(() => this.process?.write('/quit\r'), 120)
  }

  kill(): void {
    const child = this.process
    this.process = undefined
    if (!child) return
    try {
      child.kill()
    } catch {
      // The child may have exited between the state check and kill request.
    }
    this.publish({ phase: 'stopped' })
  }

  private publish(state: TerminalState): void {
    this.state = state
    this.send('terminal:state', state)
  }

  private send(channel: string, payload: unknown): void {
    if (!this.window.isDestroyed()) this.window.webContents.send(channel, payload)
  }
}
