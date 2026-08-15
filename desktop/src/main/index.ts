import { existsSync, readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { app, BrowserWindow, clipboard, ipcMain, session, shell } from 'electron'
import { findProjectRoot, normalizeMonitorUrl, resolveRuntimePaths } from './runtime-locator'
import { ServiceController } from './service-controller'
import { TerminalSession } from './terminal-session'
import { loginToPlatform } from './platform-login'
import type {
  MonitorProbe,
  ServiceLifecycleAction,
  TerminalBackend,
  TerminalBackendChangeResult
} from '../shared/contracts'

let mainWindow: BrowserWindow | undefined
let terminal: TerminalSession | undefined
let services: ServiceController | undefined
let quitting = false

const APP_ID = 'io.voidcube.desktop'

function windowIconPath(): string | undefined {
  const filename = process.platform === 'win32' ? 'icon.ico' : 'icon.png'
  return [
    join(app.getAppPath(), 'assets', filename),
    join(process.cwd(), 'assets', filename),
    join(process.resourcesPath, filename)
  ].find((candidate) => existsSync(candidate))
}

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
    width: 1060,
    height: 960,
    minWidth: 900,
    minHeight: 640,
    center: true,
    frame: false,
    resizable: true,
    show: false,
    backgroundColor: '#11151b',
    title: 'VoidCube',
    icon: windowIconPath(),
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

  const runtime = resolveRuntimePaths({
    projectRoot: developmentProjectRoot(),
    resourcesPath: process.resourcesPath
  })
  services = new ServiceController(runtime)
  terminal = new TerminalSession(window, runtime)

  const rendererUrl = process.env.ELECTRON_RENDERER_URL
  if (rendererUrl) void window.loadURL(rendererUrl)
  else void window.loadFile(join(__dirname, '../renderer/index.html'))
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

// ── Cookie 注入（账号中心） ──

function getAccountsPath(): string {
  const home = process.env.VOIDCUBE_HOME || join(homedir(), '.VoidCube')
  return join(home, 'accounts.json')
}

async function injectCookies(): Promise<void> {
  const path = getAccountsPath()
  if (!existsSync(path)) return
  try {
    const raw = readFileSync(path, 'utf-8')
    const data = JSON.parse(raw)
    const accounts: Array<Record<string, unknown>> = Array.isArray(data.accounts) ? data.accounts : []
    if (accounts.length === 0) return
    for (const account of accounts) {
      if (account.status !== 'active') continue
      const cookies = Array.isArray(account.parsed_cookies) ? account.parsed_cookies : []
      for (const cookie of cookies) {
        const c = cookie as Record<string, unknown>
        const domain = String(c.domain || '')
        const name = String(c.name || '')
        const value = String(c.value || '')
        if (!domain || !name || !value) continue
        await session.defaultSession.cookies.set({
          url: `https://${domain.replace(/^\./, '')}`,
          name,
          value,
          domain,
          path: String(c.path || '/'),
          secure: c.secure !== false,
          httpOnly: c.http_only === true,
          sameSite: 'no_restriction' as const,
          expirationDate: Math.floor(Date.now() / 1000) + 365 * 86400,
        })
      }
    }
    console.log(`[VoidCube] Injected cookies for ${accounts.length} account(s)`)
  } catch (err) {
    // accounts.json 不存在或格式错误 — 忽略，不影响正常启动
    console.debug('[VoidCube] No accounts found for cookie injection:', err instanceof Error ? err.message : err)
  }
}

function registerIpc(): void {
  ipcMain.handle('monitor:probe', probeMonitor)
  ipcMain.handle('cookies:refresh', () => injectCookies().then(() => ({ ok: true })).catch((err) => ({ ok: false, error: String(err) })))
  ipcMain.handle('clipboard:read-text', () => {
    try {
      return { ok: true, text: clipboard.readText() }
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : String(err) }
    }
  })
  ipcMain.handle('accounts:platform-login', (_event, platform: unknown) => {
    if (!mainWindow) return { ok: false, error: '桌面窗口不可用' }
    if (typeof platform !== 'string') return { ok: false, error: '平台参数无效' }
    return loginToPlatform(mainWindow, platform)
  })
  ipcMain.on('window:minimize', () => mainWindow?.minimize())
  ipcMain.on('window:close', () => mainWindow?.close())
  ipcMain.handle('workspace:open', async () => {
    const path = services?.currentWorkspacePath()
    if (!path) return { ok: false, message: 'Workspace path is unavailable' }
    const message = await shell.openPath(path)
    return message ? { ok: false, message } : { ok: true }
  })
  ipcMain.handle('services:status', () => {
    if (!services) throw new Error('Service control is unavailable')
    return services.status()
  })
  ipcMain.handle('services:control', (_event, action: unknown) => {
    if (action !== 'start' && action !== 'stop' && action !== 'restart') {
      throw new Error('Invalid service lifecycle action')
    }
    if (!services) throw new Error('Service control is unavailable')
    return services.control(action as ServiceLifecycleAction)
  })
  ipcMain.handle('services:set-backend', async (_event, backend: unknown): Promise<TerminalBackendChangeResult> => {
    if (backend !== 'local' && backend !== 'podman') {
      return { ok: false, backend: 'local', error: '不支持的终端后端' }
    }
    if (!services) return { ok: false, backend, error: '服务控制不可用' }

    const configured = await services.setTerminalBackend(backend as TerminalBackend)
    if (!configured.ok) return { ok: false, backend, error: configured.error }

    const serviceResult = await services.control('restart')
    if (!serviceResult.ok) {
      return {
        ok: false,
        backend,
        services: serviceResult,
        error: serviceResult.error || '托管服务重启失败，配置已写入但尚未完全生效'
      }
    }

    const terminalState = terminal?.restart()
    return { ok: true, backend, services: serviceResult, terminal: terminalState }
  })
  ipcMain.handle('terminal:start', () => terminal?.start() ?? { phase: 'error', message: 'Terminal is unavailable' })
  ipcMain.handle('terminal:restart', () => terminal?.restart() ?? { phase: 'error', message: 'Terminal is unavailable' })
  ipcMain.on('terminal:write', (_event, data: unknown) => {
    if (typeof data === 'string' && data.length <= 1_000_000) terminal?.write(data)
  })
  ipcMain.on('terminal:resize', (_event, columns: unknown, rows: unknown) => {
    if (typeof columns === 'number' && typeof rows === 'number') terminal?.resize(columns, rows)
  })
}

app.setName('VoidCube')
app.setAppUserModelId(APP_ID)

app.whenReady().then(async () => {
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false))
  await injectCookies()
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
