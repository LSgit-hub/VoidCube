import { chromium, expect, test } from '@playwright/test'


test('provider pool and worker assignment panels stay usable across viewports', async () => {
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
  const pageErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.route('**/provider-pool/providers/*/test', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ status: 'ok', latency_ms: 42, model_count: 2 })
  }))
  await page.route('**/provider-pool/providers/*/models', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ status: 'ok', count: 2, models: ['model-a', 'model-b'] })
  }))

  try {
    await page.goto('http://127.0.0.1:6002/ui', { waitUntil: 'domcontentloaded' })
    await page.locator('.dock-btn[data-panel="settings"]').click({ force: true })
    await expect(page.locator('#panelSettings')).toHaveClass(/open/)
    await expect(page.locator('#providerForm')).toBeInViewport()
    await expect(page.locator('#providerPoolList .provider-list-row')).toHaveCount(3)
    await expect(page.locator('#providerKey')).toBeDisabled()
    await expect(page.locator('#providerDelete')).toBeDisabled()
    await expect(page.locator('#providerApiKey')).toHaveValue('')
    await expect(page.locator('#providerApiKey')).toHaveAttribute('type', 'password')
    await expect(page.locator('#providerTest')).toBeEnabled()
    await expect(page.locator('#providerLoadModels')).toBeEnabled()
    await page.locator('#providerTest').click()
    await expect(page.locator('#providerPoolStatus')).toHaveText('连接正常 · 42 ms · 2 个模型')
    await page.locator('#providerLoadModels').click()
    await expect(page.locator('#providerPoolStatus')).toHaveText('已读取 2 个模型')
    await expect(page.locator('#providerModelOptions option')).toHaveCount(2)
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({
      path: 'test-results/provider-pool-desktop.png',
      fullPage: true,
      animations: 'disabled'
    })

    await page.locator('[data-settings-view="workers"]').click()
    await expect(page.locator('#workerRoleList .worker-role-row')).toHaveCount(4)
    await expect(page.locator('[data-worker-provider]').first()).toHaveValue('')
    await expect(page.locator('[data-worker-provider]').first().locator('option')).toHaveCount(4)
    await expect(page.locator('#workerRoleList .worker-role-row').first()).toBeInViewport()
    await page.screenshot({
      path: 'test-results/worker-roles-desktop.png',
      fullPage: true,
      animations: 'disabled'
    })

    await page.setViewportSize({ width: 600, height: 800 })
    await expect(page.locator('#panelSettings')).toBeVisible()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await expect(page.locator('#workerRoleList .worker-role-row').first()).toBeVisible()
    await page.screenshot({
      path: 'test-results/worker-roles-narrow.png',
      fullPage: true,
      animations: 'disabled'
    })
    await page.locator('#workerRoleList .worker-role-row').last().scrollIntoViewIfNeeded()
    await expect(page.locator('#workerRoleList .worker-role-row').last()).toBeInViewport()
    await page.locator('#workerAssignmentSave').scrollIntoViewIfNeeded()
    await expect(page.locator('#workerAssignmentSave')).toBeInViewport()
    await page.screenshot({
      path: 'test-results/worker-roles-narrow-bottom.png',
      fullPage: true,
      animations: 'disabled'
    })
    expect(pageErrors).toEqual([])
  } finally {
    await browser.close()
  }
})
