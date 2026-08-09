import { join } from 'node:path'
import { app, BrowserWindow, ipcMain, session, shell } from 'electron'
import { findProjectRoot, normalizeMonitorUrl, resolveRuntimePaths } from './runtime-locator'
import { TerminalSession } from './terminal-session'
import type { MonitorProbe } from '../shared/contracts'

let mainWindow: BrowserWindow | undefined
let terminal: TerminalSession | undefined
let quitting = false

function developmentProjectRoot(): string | undefined {
  return findProjectRoot([
    process.env.VOIDCUBE_PROJECT_ROOT,
    process.cwd(),
    join(process.cwd(), '..'),
    join(app.getAppPath(), '..')
  ])
}

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 900,
    minHeight: 640,
    show: false,
    backgroundColor: '#11151b',
    title: 'VoidCube',
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  })

  window.once('ready-to-show', () => window.show())
  window.webContents.setWindowOpenHandler(({ url }) => {
    const protocol = new URL(url).protocol
    if (protocol === 'https:' || protocol === 'http:') void shell.openExternal(url)
    return { action: 'deny' }
  })

  const rendererUrl = process.env.ELECTRON_RENDERER_URL
  if (rendererUrl) void window.loadURL(rendererUrl)
  else void window.loadFile(join(__dirname, '../renderer/index.html'))

  const runtime = resolveRuntimePaths({
    projectRoot: developmentProjectRoot(),
    resourcesPath: process.resourcesPath
  })
  terminal = new TerminalSession(window, runtime)
  window.on('closed', () => {
    if (mainWindow === window) mainWindow = undefined
    if (process.platform !== 'darwin' || !terminal) return
    const closedTerminal = terminal
    terminal = undefined
    closedTerminal.requestGracefulExit()
    setTimeout(() => closedTerminal.kill(), 1600)
  })
  return window
}

async function probeMonitor(): Promise<MonitorProbe> {
  let url: string
  try {
    url = normalizeMonitorUrl(process.env.VOIDCUBE_SUPERVISOR_URL)
  } catch (error) {
    return { ready: false, url: '', message: error instanceof Error ? error.message : String(error) }
  }

  try {
    const response = await fetch(url, {
      cache: 'no-store',
      signal: AbortSignal.timeout(1800)
    })
    if (!response.ok) return { ready: false, url, message: `HTTP ${response.status}` }
    return { ready: true, url }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    return { ready: false, url, message }
  }
}

function registerIpc(): void {
  ipcMain.handle('monitor:probe', probeMonitor)
  ipcMain.handle('terminal:start', () => terminal?.start() ?? { phase: 'error', message: 'Terminal is unavailable' })
  ipcMain.handle('terminal:restart', () => terminal?.restart() ?? { phase: 'error', message: 'Terminal is unavailable' })
  ipcMain.on('terminal:write', (_event, data: unknown) => {
    if (typeof data === 'string' && data.length <= 1_000_000) terminal?.write(data)
  })
  ipcMain.on('terminal:resize', (_event, columns: unknown, rows: unknown) => {
    if (typeof columns === 'number' && typeof rows === 'number') terminal?.resize(columns, rows)
  })
}

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false))
  registerIpc()
  mainWindow = createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) mainWindow = createWindow()
  })
})

app.on('before-quit', (event) => {
  if (quitting || !terminal?.isActive()) return
  event.preventDefault()
  quitting = true
  terminal.requestGracefulExit()
  setTimeout(() => {
    terminal?.kill()
    app.quit()
  }, 1600)
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
