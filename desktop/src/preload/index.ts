import { contextBridge, ipcRenderer } from 'electron'
import type {
  MonitorProbe,
  ServiceControlResult,
  ServiceLifecycleAction,
  TerminalBackend,
  TerminalBackendChangeResult,
  TerminalState,
  VoidCubeDesktopApi,
  WorkspaceOpenResult
} from '../shared/contracts'

const api: VoidCubeDesktopApi = {
  runtime: {
    platform: process.platform,
    versions: {
      app: process.env.npm_package_version ?? '1.0.0',
      electron: process.versions.electron,
      chrome: process.versions.chrome
    }
  },
  monitor: {
    probe: () => ipcRenderer.invoke('monitor:probe') as Promise<MonitorProbe>
  },
  services: {
    status: () => ipcRenderer.invoke('services:status') as Promise<ServiceControlResult>,
    control: (action: ServiceLifecycleAction) =>
      ipcRenderer.invoke('services:control', action) as Promise<ServiceControlResult>,
    setBackend: (backend: TerminalBackend) =>
      ipcRenderer.invoke('services:set-backend', backend) as Promise<TerminalBackendChangeResult>
  },
  window: {
    minimize: () => ipcRenderer.send('window:minimize'),
    close: () => ipcRenderer.send('window:close')
  },
  workspace: {
    open: () => ipcRenderer.invoke('workspace:open') as Promise<WorkspaceOpenResult>
  },
  terminal: {
    start: () => ipcRenderer.invoke('terminal:start') as Promise<TerminalState>,
    restart: () => ipcRenderer.invoke('terminal:restart') as Promise<TerminalState>,
    write: (data) => ipcRenderer.send('terminal:write', data),
    resize: (columns, rows) => ipcRenderer.send('terminal:resize', columns, rows),
    onData: (listener) => {
      const handler = (_event: Electron.IpcRendererEvent, data: string): void => listener(data)
      ipcRenderer.on('terminal:data', handler)
      return () => ipcRenderer.removeListener('terminal:data', handler)
    },
    onState: (listener) => {
      const handler = (_event: Electron.IpcRendererEvent, state: TerminalState): void => listener(state)
      ipcRenderer.on('terminal:state', handler)
      return () => ipcRenderer.removeListener('terminal:state', handler)
    }
  },
  cookiesRefresh: () =>
    ipcRenderer.invoke('cookies:refresh') as Promise<{ ok: boolean }>,
  clipboardReadText: () =>
    ipcRenderer.invoke('clipboard:read-text') as Promise<{ ok: boolean; text?: string; error?: string }>,
  platformLogin: (platform) =>
    ipcRenderer.invoke('accounts:platform-login', platform)
}

contextBridge.exposeInMainWorld('voidcubeDesktop', api)
