import { chromium, expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { supervisorUiUrl } from './helpers/supervisor-page'

test('room switch controls perception, reflects failures and never changes Auto mode', async () => {
  const browser = await chromium.launch({channel: 'chromium'})
  const page = await browser.newPage({viewport: {width: 1280, height: 800}})
  const pageErrors: string[] = []
  page.on('pageerror', error => pageErrors.push(error.message))
  let enabled = false
  let allowed = true
  let failStart = false
  let stopping = false
  const posts: string[] = []
  const state = () => ({ status: 'ok', allowed, runtime: {
    enabled, state: stopping ? 'stopping' : enabled ? 'running' : 'stopped', last_error: null
  } })
  await page.route('**/ui', route => route.fulfill({contentType: 'text/html',
    body: readFileSync(resolve(__dirname, '../../../src/voidcube/systems/supervisor/web/supervisor.html'), 'utf8')}))
  await page.route('**/runtime/perception/*', async route => {
    const path = new URL(route.request().url()).pathname
    if (route.request().method() === 'POST') {
      posts.push(path)
      if (path.endsWith('/start')) {
        expect(route.request().postDataJSON()).toEqual({consent: true})
        if (failStart) {
          await route.fulfill({status: 503, contentType: 'application/json', body: '{"detail":"perception_start_failed"}'})
          return
        }
        enabled = true
      } else if (path.endsWith('/stop')) {
        enabled = false
        stopping = true
      }
    }
    await route.fulfill({contentType: 'application/json', body: JSON.stringify(state())})
  })
  await page.route('**/autonomous-chain-gate/*', async route => {
    if (route.request().method() === 'POST') posts.push('unexpected-mode-change')
    await route.fulfill({contentType: 'application/json', body: '{}'})
  })
  try {
    await page.goto(supervisorUiUrl)
    const control = page.locator('#perceptionControl')
    const on = control.locator('[data-perception="on"]')
    const off = control.locator('[data-perception="off"]')
    await expect(off).toHaveAttribute('aria-pressed', 'true')
    await expect(on).toHaveText('感知开')
    await expect(page.locator('#stellarModeControl')).toHaveCount(0)
    await on.click()
    await expect(on).toHaveAttribute('aria-pressed', 'true')
    await page.reload()
    await expect(on).toHaveAttribute('aria-pressed', 'true')
    await off.click()
    await expect(off).toHaveText('停止中')
    await expect(on).toBeDisabled()
    stopping = false
    await expect(off).toHaveText('感知关')
    failStart = true
    await on.click()
    await expect(off).toHaveAttribute('aria-pressed', 'true')
    await expect(on).toBeEnabled()
    allowed = false
    await expect(on).toBeDisabled()
    expect(posts).toEqual(['/runtime/perception/start', '/runtime/perception/stop', '/runtime/perception/start'])
    expect(pageErrors).toEqual([])
    allowed = true
    await expect(on).toBeEnabled()
    await control.screenshot({path: 'test-results/perception-switch.png'})
    const size = await control.boundingBox()
    expect(size?.width).toBeLessThan(150)
  } finally {
    await browser.close()
  }
})
