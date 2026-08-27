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
export type TerminalBackend = 'local' | 'podman'
export type PluginControlAction = ServiceLifecycleAction

export interface ServiceInfo {
  name: string
  port: number
  pid?: number | null
  state: ServicePhase
}

export interface PluginServiceInfo {
  port: number
  pid?: number | null
  state: ServicePhase
}

export interface PluginInfo {
  name: string
  displayName: string
  version: string
  description: string
  enabled: boolean
  capabilities: string[]
  uiPath?: string | null
  service?: PluginServiceInfo | null
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
  bodyImprovementBackend?: string
  pid?: number
  updatedAt?: string
}

export interface ServiceControlResult {
  schemaVersion: 1
  action: ServiceControlAction
  ok: boolean
  generatedAt: string
  services: ServiceInfo[]
  plugins?: PluginInfo[]
  executionContext?: ExecutionContext
  error?: string
}

export interface TerminalBackendChangeResult {
  ok: boolean
  backend: TerminalBackend
  services?: ServiceControlResult
  terminal?: TerminalState
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

export interface PlatformLoginResult {
  ok: boolean
  cookiesRaw?: string
  cookieCount?: number
  source?: string
  error?: string
}

export interface VoidCubeDesktopApi {
  runtime: RuntimeInfo
  monitor: {
    probe: () => Promise<MonitorProbe>
  }
  services: {
    status: () => Promise<ServiceControlResult>
    control: (action: ServiceLifecycleAction) => Promise<ServiceControlResult>
    setBackend: (backend: TerminalBackend) => Promise<TerminalBackendChangeResult>
  }
  plugins: {
    control: (name: string, action: PluginControlAction) => Promise<ServiceControlResult>
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
  platformLogin: (platform: string) => Promise<PlatformLoginResult>
}
