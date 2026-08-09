export type TerminalPhase = 'stopped' | 'starting' | 'running' | 'exited' | 'error'

export interface TerminalState {
  phase: TerminalPhase
  pid?: number
  exitCode?: number
  message?: string
}

export interface MonitorProbe {
  ready: boolean
  url: string
  message?: string
}

export interface RuntimeInfo {
  platform: NodeJS.Platform
  versions: {
    app: string
    electron: string
    chrome: string
  }
}

export interface VoidCubeDesktopApi {
  runtime: RuntimeInfo
  monitor: {
    probe: () => Promise<MonitorProbe>
  }
  terminal: {
    start: () => Promise<TerminalState>
    restart: () => Promise<TerminalState>
    write: (data: string) => void
    resize: (columns: number, rows: number) => void
    onData: (listener: (data: string) => void) => () => void
    onState: (listener: (state: TerminalState) => void) => () => void
  }
}
