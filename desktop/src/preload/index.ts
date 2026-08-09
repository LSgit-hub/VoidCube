import { contextBridge, ipcRenderer } from 'electron'
import type { MonitorProbe, TerminalState, VoidCubeDesktopApi } from '../shared/contracts'

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
  }
}

contextBridge.exposeInMainWorld('voidcubeDesktop', api)
