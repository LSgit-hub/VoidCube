import { BrowserWindow, session, shell } from 'electron'
import type { Cookie, Session } from 'electron'
import type { PlatformLoginResult } from '../shared/contracts'

interface PlatformLoginSpec {
  name: string
  loginUrl: string
  rootDomain: string
  requiredCookies: string[]
}

const PLATFORM_LOGIN_SPECS: Record<string, PlatformLoginSpec> = {
  bilibili: {
    name: 'B站',
    loginUrl: 'https://passport.bilibili.com/login',
    rootDomain: 'bilibili.com',
    requiredCookies: ['SESSDATA']
  },
  netease_music: {
    name: '网易云音乐',
    loginUrl: 'https://music.163.com/#/login',
    rootDomain: 'music.163.com',
    requiredCookies: ['MUSIC_U']
  }
}

function domainMatches(cookieDomain: string | undefined, rootDomain: string): boolean {
  const domain = (cookieDomain || '').toLowerCase().replace(/^\./, '')
  return domain === rootDomain || domain.endsWith(`.${rootDomain}`)
}

export function cookieHeaderForPlatform(cookies: Cookie[], platform: string): string {
  const spec = PLATFORM_LOGIN_SPECS[platform]
  if (!spec) return ''
  const values = new Map<string, string>()
  for (const cookie of cookies) {
    if (cookie.value && domainMatches(cookie.domain, spec.rootDomain)) {
      values.set(cookie.name, cookie.value)
    }
  }
  return Array.from(values, ([name, value]) => `${name}=${value}`).join('; ')
}

async function collectPlatformLogin(
  loginSession: Session,
  platform: string,
  spec: PlatformLoginSpec
): Promise<PlatformLoginResult> {
  const cookies = await loginSession.cookies.get({})
  const cookiesRaw = cookieHeaderForPlatform(cookies, platform)
  const names = new Set(
    cookies
      .filter((cookie) => domainMatches(cookie.domain, spec.rootDomain) && cookie.value)
      .map((cookie) => cookie.name)
  )
  const missing = spec.requiredCookies.filter((name) => !names.has(name))
  if (missing.length > 0) {
    return {
      ok: false,
      error: `登录窗口中没有检测到 ${missing.join(', ')}，请完成登录后再关闭窗口。`
    }
  }
  return {
    ok: true,
    cookiesRaw,
    cookieCount: names.size,
    source: `VoidCube ${spec.name}登录会话`
  }
}

export function loginToPlatform(
  parent: BrowserWindow,
  platform: string
): Promise<PlatformLoginResult> {
  const spec = PLATFORM_LOGIN_SPECS[platform]
  if (!spec) return Promise.resolve({ ok: false, error: `不支持的平台: ${platform}` })

  const partition = `persist:voidcube-account-${platform}`
  const loginSession = session.fromPartition(partition)
  loginSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false))

  return new Promise((resolve) => {
    const loginWindow = new BrowserWindow({
      parent,
      modal: true,
      width: 1060,
      height: 780,
      minWidth: 760,
      minHeight: 600,
      show: false,
      autoHideMenuBar: true,
      title: `登录 ${spec.name} - 完成后将自动保存登录状态`,
      webPreferences: {
        partition,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true
      }
    })

    let completed = false
    const onCookieChanged = (
      _event: Electron.Event,
      cookie: Cookie,
      _cause: string,
      removed: boolean
    ): void => {
      if (removed || !spec.requiredCookies.includes(cookie.name)) return
      if (!domainMatches(cookie.domain, spec.rootDomain)) return
      setTimeout(() => void complete(), 800)
    }
    const complete = async (loadError?: string): Promise<void> => {
      if (completed) return
      completed = true
      loginSession.cookies.removeListener('changed', onCookieChanged)
      const result = loadError
        ? { ok: false, error: loadError }
        : await collectPlatformLogin(loginSession, platform, spec).catch((error: unknown) => ({
            ok: false,
            error: error instanceof Error ? error.message : String(error)
          }))
      if (!loginWindow.isDestroyed()) loginWindow.destroy()
      resolve(result)
    }

    loginSession.cookies.on('changed', onCookieChanged)
    loginWindow.once('ready-to-show', () => loginWindow.show())
    loginWindow.once('closed', () => void complete())
    loginWindow.webContents.setWindowOpenHandler(({ url }) => {
      try {
        const hostname = new URL(url).hostname.toLowerCase()
        if (hostname === spec.rootDomain || hostname.endsWith(`.${spec.rootDomain}`)) {
          void loginWindow.loadURL(url)
        } else {
          void shell.openExternal(url)
        }
      } catch {
        // Ignore malformed popup URLs.
      }
      return { action: 'deny' }
    })
    void loginWindow.loadURL(spec.loginUrl).catch((error: unknown) => {
      void complete(`无法打开 ${spec.name} 登录页: ${error instanceof Error ? error.message : String(error)}`)
    })
  })
}
