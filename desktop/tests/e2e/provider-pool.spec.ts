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
  await page.route(/\/provider-pool\/worker-tests(?:\/[^/?]+)?(?:\?.*)?$/, (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    let body: object
    if (path === '/provider-pool/worker-tests') body = {
      status: 'ok',
      tests: [{
        status: 'completed',
        test_id: 'previous-worker-test',
        worker_role: 'research',
        worker_label: '调研员工',
        provider: 'openrouter',
        model: 'history-model',
        elapsed_ms: 1200,
        result: '历史测试成功',
        error: '',
        recorded_at: '2026-08-10T01:00:00+00:00'
      }],
      provider_health: [{
        provider: 'openrouter',
        status: 'healthy',
        model: 'history-model',
        elapsed_ms: 1200,
        tested_at: '2026-08-10T01:00:00+00:00',
        worker_role: 'research'
      }]
    }
    else if (request.method() === 'POST') body = {
      status: 'queued',
      test_id: 'worker-test-1',
      worker_role: 'general',
      worker_label: '通用员工',
      provider: 'actual-provider',
      model: 'actual-model'
    }
    else body = {
      status: 'completed',
      test_id: 'worker-test-1',
      worker_role: 'general',
      worker_label: '通用员工',
      provider: 'actual-provider',
      model: 'actual-model',
      elapsed_ms: 845,
      result: '员工测试成功',
      error: ''
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
  })

  try {
    await page.goto('http://127.0.0.1:6002/ui', { waitUntil: 'domcontentloaded' })
    await page.locator('.dock-btn[data-panel="settings"]').click({ force: true })
    await expect(page.locator('#panelSettings')).toHaveClass(/open/)
    await expect(page.locator('#providerForm')).toBeInViewport()
    await expect(page.locator('#providerPoolList .provider-list-row')).toHaveCount(3)
    await expect(page.locator('#providerPoolList')).toContainText('员工验证正常')
    const providerRows = page.locator('#providerPoolList .provider-list-row')
    await providerRows.nth(1).click()
    await expect(page.locator('#panelSettings')).toHaveClass(/open/)
    await expect(providerRows.nth(1)).toHaveClass(/active/)
    await providerRows.first().click()
    await expect(page.locator('#panelSettings')).toHaveClass(/open/)
    const loadedProviderKey = await providerRows.first().getAttribute('data-provider-key')
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
    await expect(page.locator('[data-worker-role="research"] [data-worker-test-state]')).toHaveText(
      '已验证 · 1.2 s · history-model'
    )
    await expect(page.locator('[data-worker-model]').first()).toHaveJSProperty('tagName', 'SELECT')
    await page.locator('#workerRecommendedApply').click()
    await expect(page.locator('[data-worker-role="general"] .worker-toolsets summary')).toHaveText('推荐 · 4 个')
    await expect(page.locator('[data-worker-role="research"] .worker-toolsets summary')).toHaveText('推荐 · 1 个')
    await expect(page.locator('[data-worker-role="coding"] .worker-toolsets summary')).toHaveText('推荐 · 5 个')
    await expect(page.locator('[data-worker-role="media"] .worker-toolsets summary')).toHaveText('推荐 · 1 个')
    await expect(page.locator('#workerAssignmentStatus')).toHaveText('已应用推荐，请保存')
    const firstProviderSelect = page.locator('[data-worker-provider]').first()
    await expect(firstProviderSelect.locator('option')).toHaveCount(4)
    const selectedProvider = await firstProviderSelect.inputValue()
    const providerOptions = await firstProviderSelect.locator('option').evaluateAll(
      (options) => options.map((option) => (option as HTMLOptionElement).value)
    )
    expect(providerOptions).toContain(selectedProvider)
    await firstProviderSelect.selectOption(String(loadedProviderKey))
    const modelOptions = await page.locator('[data-worker-model]').first().locator('option').evaluateAll(
      (options) => options.map((option) => (option as HTMLOptionElement).value)
    )
    expect(modelOptions).toContain('model-a')
    expect(modelOptions).toContain('model-b')
    await page.locator('[data-worker-test]').first().click()
    await expect(page.locator('#workerTestStatus')).toHaveText(
      '完成 · 845 ms · actual-provider · actual-model',
      { timeout: 5000 }
    )
    await expect(page.locator('#workerTestResult')).toHaveText('员工测试成功')
    await expect(page.locator('[data-worker-test-state]').first()).toHaveText(
      '已验证 · 845 ms · actual-model'
    )
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
