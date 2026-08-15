import { _electron as electron, expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'

function electronExecutable(): string {
  if (process.env.VOIDCUBE_DESKTOP_EXECUTABLE) return process.env.VOIDCUBE_DESKTOP_EXECUTABLE
  const root = join(process.cwd(), 'node_modules', 'electron', 'dist')
  if (process.platform === 'win32') return join(root, 'electron.exe')
  if (process.platform === 'darwin') return join(root, 'Electron.app', 'Contents', 'MacOS', 'Electron')
  return join(root, 'electron')
}

function projectPython(projectRoot: string): string {
  const candidates = process.platform === 'win32'
    ? [join(projectRoot, '.venv', 'Scripts', 'python.exe')]
    : [join(projectRoot, '.venv', 'bin', 'python3'), join(projectRoot, '.venv', 'bin', 'python')]
  const python = candidates.find((candidate) => existsSync(candidate))
  if (!python) throw new Error('Project Python is unavailable')
  return python
}

test('opens the supervisor and a real VoidCube PTY', async () => {
  const projectRoot = resolve(process.cwd(), '..')
  const env = Object.fromEntries(
    Object.entries(process.env).filter((entry): entry is [string, string] => typeof entry[1] === 'string')
  )
  delete env.ELECTRON_RUN_AS_NODE
  env.VOIDCUBE_PROJECT_ROOT = projectRoot
  env.VOIDCUBE_DESKTOP_WORKSPACE = projectRoot
  // Keep this smoke test deterministic; it asserts the container execution
  // context and must not inherit the developer's persisted backend choice.
  env.TERMINAL_ENV = 'podman'
  env.TERMINAL_FALLBACK_TO_LOCAL = 'false'

  const application = await electron.launch({
    executablePath: electronExecutable(),
    args: process.env.VOIDCUBE_DESKTOP_EXECUTABLE ? [] : [process.cwd()],
    cwd: process.cwd(),
    env
  })
  const originalClipboard = await application.evaluate(({ clipboard }) => clipboard.readText())

  try {
    const window = await application.firstWindow()
    const pageErrors: string[] = []
    window.on('pageerror', (error) => pageErrors.push(error.message))
    await expect(window.locator('.app-shell')).toBeVisible()
    await expect(window.locator('#minimize-window')).toBeVisible()
    await expect(window.locator('#close-window')).toBeVisible()
    await expect.poll(() => window.locator('#workspace').evaluate(
      (element) => element.style.getPropertyValue('--monitor-size')
    )).toBe('54%')
    await expect(window.locator('#execution-mode')).toHaveText('Podman 沙箱')
    await expect(window.locator('#execution-workspace')).toHaveText('VoidCube · master')
    await expect(window.locator('#open-workspace')).toHaveAttribute(
      'title',
      '在文件管理器中打开工作区'
    )
    await expect(window.locator('#execution-context')).toHaveAttribute('title', /Agent 目录：\/workspace/)
    await expect(window.locator('#services-state, #monitor-state, #terminal-state')).toHaveCount(0)
    await expect(window.locator('#terminal-meta')).toHaveText(/PID \d+/)
    const terminalScrollbar = window.locator('#terminal .scrollbar.vertical')
    await expect(terminalScrollbar).toHaveCount(1)
    await expect(terminalScrollbar).toBeHidden()
    await expect(window.locator('#monitor-frame')).toHaveAttribute('src', /127\.0\.0\.1:6002\/ui/)
    await expect(window.locator('#monitor-overlay')).toBeHidden()
    await expect(window.frameLocator('#monitor-frame').locator('.room')).toBeVisible()
    await expect.poll(async () => window.locator('.xterm-rows').textContent()).toContain('❯')
    await window.locator('#terminal').click()
    await application.evaluate(({ clipboard }) => clipboard.writeText('/help'))
    await window.keyboard.press('Control+V')
    await expect.poll(async () => window.locator('.xterm-rows').textContent()).toContain('/help')
    await window.keyboard.press('Enter')

    await application.evaluate(({ BrowserWindow }) => {
      const activeWindow = BrowserWindow.getAllWindows()[0]
      if (!activeWindow) throw new Error('Desktop window is unavailable')
      activeWindow.setSize(920, 640)
    })
    await expect.poll(async () => {
      const text = await window.locator('.xterm-rows').textContent() ?? ''
      return (text.match(/Git <[^>]+>/g) ?? []).length
    }).toBe(0)
    await expect.poll(async () => window.locator('.xterm-rows').textContent()).toContain('❯')
    await expect(
      window.locator('.xterm-rows > div').filter({ hasText: '❯' }).last()
    ).toBeInViewport()
    await window.screenshot({ path: 'test-results/voidcube-desktop-narrow.png' })
    await application.evaluate(({ BrowserWindow }) => {
      const activeWindow = BrowserWindow.getAllWindows()[0]
      if (!activeWindow) throw new Error('Desktop window is unavailable')
      activeWindow.setSize(1060, 960)
    })
    await expect.poll(async () => {
      const text = await window.locator('.xterm-rows').textContent() ?? ''
      return (text.match(/Git <[^>]+>/g) ?? []).length
    }).toBe(1)
    const statusRow = window.locator('.xterm-rows > div').filter({ hasText: 'Git <' }).last()
    await expect(statusRow).not.toContainText(/[💤🤔🧠💡📤🔧🔄🧩]/u)
    const statusRowMetrics = await statusRow.evaluate((element) => ({
      height: element.getBoundingClientRect().height,
      lineHeight: Number.parseFloat(getComputedStyle(element).lineHeight)
    }))
    expect(statusRowMetrics.height).toBeGreaterThanOrEqual(17)
    expect(statusRowMetrics.lineHeight).toBeGreaterThanOrEqual(17)

    const supervisorFrame = window.frameLocator('#monitor-frame')
    await supervisorFrame.locator('#mediaDockButton').click()
    await expect(supervisorFrame.locator('#panelMedia')).toHaveClass(/open/)
    await supervisorFrame.locator('#deliveryFullscreen').click()
    await expect(supervisorFrame.locator('#panelMedia')).toHaveClass(/desktop-maximized/)
    await expect(window.locator('#workspace')).toHaveAttribute('data-layout', 'split')
    await expect(window.locator('.terminal-pane')).toBeVisible()
    const maximizedPanelGeometry = await supervisorFrame.locator('#panelMedia').evaluate((element) => {
      const bounds = element.getBoundingClientRect()
      return {
        x: bounds.x,
        y: bounds.y,
        width: bounds.width,
        height: bounds.height,
        viewportWidth: innerWidth,
        viewportHeight: innerHeight
      }
    })
    expect(maximizedPanelGeometry.x).toBe(0)
    expect(maximizedPanelGeometry.y).toBe(0)
    expect(maximizedPanelGeometry.width).toBe(maximizedPanelGeometry.viewportWidth)
    expect(maximizedPanelGeometry.height).toBe(maximizedPanelGeometry.viewportHeight)
    await expect.poll(() => supervisorFrame.locator('html').evaluate(
      () => document.fullscreenElement?.id ?? ''
    )).toBe('')
    await expect.poll(() => application.evaluate(({ BrowserWindow }) => (
      BrowserWindow.getAllWindows()[0]?.isFullScreen() ?? false
    ))).toBe(false)
    await window.screenshot({ path: 'test-results/voidcube-delivery-web-maximized.png' })
    await supervisorFrame.locator('#deliveryFullscreen').click()
    await expect(supervisorFrame.locator('#panelMedia')).not.toHaveClass(/desktop-maximized/)
    await expect(window.locator('#workspace')).toHaveAttribute('data-layout', 'split')

    await window.locator('#service-menu > summary').click()
    await expect(window.locator('#services-summary')).toHaveText('3/3 正常')
    await expect(window.locator('.service-row.healthy')).toHaveCount(3)
    await window.screenshot({ path: 'test-results/voidcube-service-menu.png' })
    await window.locator('#service-menu > summary').click()

    await window.locator('[data-layout-mode="monitor"]').click()
    await expect(window.locator('#workspace')).toHaveAttribute('data-layout', 'monitor')
    await expect(window.locator('.monitor-pane')).toBeVisible()
    await expect(window.locator('.terminal-pane')).toBeHidden()

    await window.locator('[data-layout-mode="terminal"]').click()
    await expect(window.locator('#workspace')).toHaveAttribute('data-layout', 'terminal')
    await expect(window.locator('.monitor-pane')).toBeHidden()
    await expect(window.locator('.terminal-pane')).toBeVisible()
    await expect.poll(() => window.evaluate(() => localStorage.getItem('voidcube.desktop.layout')))
      .toBe('terminal')

    await window.reload()
    await expect(window.locator('#workspace')).toHaveAttribute('data-layout', 'terminal')
    await window.locator('[data-layout-mode="split"]').click()
    await expect(window.locator('#workspace')).toHaveAttribute('data-layout', 'split')
    await expect(window.locator('.monitor-pane')).toBeVisible()
    await expect(window.locator('.terminal-pane')).toBeVisible()
    await expect(window.locator('#monitor-overlay')).toBeHidden()
    await window.screenshot({ path: 'test-results/voidcube-desktop.png' })
    expect(pageErrors).toEqual([])
  } finally {
    await application.evaluate(({ clipboard }, text) => clipboard.writeText(text), originalClipboard)
    await application.close()
  }

  const status = JSON.parse(execFileSync(
    projectPython(projectRoot),
    ['-m', 'VoidCube_cli.desktop_control', 'status'],
    { cwd: projectRoot, encoding: 'utf8' }
  )) as { ok: boolean }
  expect(status.ok).toBe(true)
})
