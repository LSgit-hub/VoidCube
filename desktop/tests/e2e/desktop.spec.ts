import { _electron as electron, expect, test } from '@playwright/test'
import { join, resolve } from 'node:path'

function electronExecutable(): string {
  if (process.env.VOIDCUBE_DESKTOP_EXECUTABLE) return process.env.VOIDCUBE_DESKTOP_EXECUTABLE
  const root = join(process.cwd(), 'node_modules', 'electron', 'dist')
  if (process.platform === 'win32') return join(root, 'electron.exe')
  if (process.platform === 'darwin') return join(root, 'Electron.app', 'Contents', 'MacOS', 'Electron')
  return join(root, 'electron')
}

test('opens the supervisor and a real VoidCube PTY', async () => {
  const projectRoot = resolve(process.cwd(), '..')
  const env = Object.fromEntries(
    Object.entries(process.env).filter((entry): entry is [string, string] => typeof entry[1] === 'string')
  )
  delete env.ELECTRON_RUN_AS_NODE
  env.VOIDCUBE_PROJECT_ROOT = projectRoot
  env.VOIDCUBE_DESKTOP_WORKSPACE = projectRoot

  const application = await electron.launch({
    executablePath: electronExecutable(),
    args: process.env.VOIDCUBE_DESKTOP_EXECUTABLE ? [] : [process.cwd()],
    cwd: process.cwd(),
    env
  })

  try {
    const window = await application.firstWindow()
    const pageErrors: string[] = []
    window.on('pageerror', (error) => pageErrors.push(error.message))
    await expect(window.locator('.app-shell')).toBeVisible()
    await expect(window.locator('#terminal-state')).toHaveClass(/good/)
    await expect(window.locator('#monitor-state')).toHaveClass(/good/)
    await expect(window.locator('#monitor-frame')).toHaveAttribute('src', /127\.0\.0\.1:6002\/ui/)
    await expect(window.locator('#monitor-overlay')).toBeHidden()
    await expect(window.frameLocator('#monitor-frame').locator('.room')).toBeVisible()
    await expect.poll(async () => window.locator('.xterm-rows').textContent()).toContain('❯')
    await window.locator('#terminal').click()
    await window.keyboard.type('/help')
    await expect.poll(async () => window.locator('.xterm-rows').textContent()).toContain('/help')
    await window.keyboard.press('Enter')
    await window.screenshot({ path: 'test-results/voidcube-desktop.png' })
    expect(pageErrors).toEqual([])
  } finally {
    await application.close()
  }
})
