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

export type ServiceLifecycleAction = 'start' | 'stop' | 'restart'
export type ServiceControlAction = 'status' | ServiceLifecycleAction
export type ServicePhase = 'healthy' | 'unhealthy' | 'stopped'

export interface ServiceInfo {
  name: string
  port: number
  pid?: number | null
  state: ServicePhase
}

export type ExecutionMode = 'system' | 'sandbox' | 'remote'

export interface ExecutionContext {
  mode: ExecutionMode
  backend: string
  hostPlatform: string
  hostWorkingDirectory: string
  backendWorkingDirectory: string
  workspaceName: string
  branch: string
  worktree: boolean
  workspaceMounted: boolean
  fallbackToLocal: boolean
  pid?: number
  updatedAt?: string
}

export interface ServiceControlResult {
  schemaVersion: 1
  action: ServiceControlAction
  ok: boolean
  generatedAt: string
  services: ServiceInfo[]
  executionContext?: ExecutionContext
  error?: string
}

export interface RuntimeInfo {
  platform: NodeJS.Platform
  versions: {
    app: string
    electron: string
    chrome: string
  }
}

export interface WorkspaceOpenResult {
  ok: boolean
  message?: string
}

export interface VoidCubeDesktopApi {
  runtime: RuntimeInfo
  monitor: {
    probe: () => Promise<MonitorProbe>
  }
  services: {
    status: () => Promise<ServiceControlResult>
    control: (action: ServiceLifecycleAction) => Promise<ServiceControlResult>
  }
  window: {
    minimize: () => void
    close: () => void
  }
  workspace: {
    open: () => Promise<WorkspaceOpenResult>
  }
  terminal: {
    start: () => Promise<TerminalState>
    restart: () => Promise<TerminalState>
    write: (data: string) => void
    resize: (columns: number, rows: number) => void
    onData: (listener: (data: string) => void) => () => void
    onState: (listener: (state: TerminalState) => void) => () => void
  }
  cookiesRefresh: () => Promise<{ ok: boolean }>
  clipboardReadText: () => Promise<{ ok: boolean; text?: string; error?: string }>
}
