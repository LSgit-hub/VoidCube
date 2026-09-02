import { expect, test } from '@playwright/test'
import {
  installAccountsRoute,
  launchSupervisorPage,
  supervisorUiUrl
} from './helpers/supervisor-page'


test('provider pool and worker assignment panels stay usable across viewports', async () => {
  const { browser, page, pageErrors } = await launchSupervisorPage({ width: 1280, height: 800 })
  await installAccountsRoute(page, {
    accounts: [
      {
        id: 'test-bilibili',
        platform: 'bilibili',
        platform_name: 'B站',
        label: '这是用于验证组件边界的超长账号标签不会推动相邻操作按钮离开面板',
        cookies_count: 24,
        status: 'active'
      },
      {
        id: 'test-netease',
        platform: 'netease_music',
        platform_name: '网易云音乐',
        label: '',
        cookies_count: 19,
        status: 'active'
      }
    ],
    supported_platforms: ['bilibili', 'netease_music'],
    accounts_revision: 0
  })
  await page.route(/\/provider-pool(?:\?.*)?$/, async (route) => {
    const response = await route.fetch()
    const body = await response.json()
    const providerKeys = Array.isArray(body.providers)
      ? body.providers.map((provider: { key?: unknown }) => String(provider.key || ''))
      : []
    if (!providerKeys.includes('backup') || !providerKeys.includes('openrouter')) {
      body.managed = false
      body.active_provider = 'openrouter'
      body.providers = [
        {
          key: 'backup',
          label: '备用 Provider',
          type: 'openai_compatible',
          base_url: 'https://example.invalid/v1',
          selected_model: 'cached-model',
          concurrency_limit: 2,
          model_catalog: { models: ['cached-model'], updated_at: '2026-08-10T01:02:03+00:00' },
          auth_mode: 'env',
          api_key_env: 'BACKUP_API_KEY',
          credential_configured: true,
          active: false,
          references: ['员工角色 general']
        },
        {
          key: 'openrouter',
          label: 'OpenRouter',
          type: 'openrouter',
          base_url: 'https://openrouter.ai/api/v1',
          selected_model: 'history-model',
          concurrency_limit: 2,
          model_catalog: { models: ['history-model'], updated_at: '2026-08-10T01:02:03+00:00' },
          auth_mode: 'env',
          api_key_env: 'OPENROUTER_API_KEY',
          credential_configured: true,
          active: true,
          references: ['API-A 当前 Provider']
        }
      ]
      body.provider_presets = [
        { type: 'openai_compatible', label: 'OpenAI Compatible', base_url: '', api_key_env: '', auth_mode: 'env' },
        { type: 'openrouter', label: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1', api_key_env: 'OPENROUTER_API_KEY', auth_mode: 'env' }
      ]
      body.default_role = 'general'
      body.max_concurrent = 4
      body.roles = [
        {
          role: 'general', label: '通用员工', description: '综合工作', enabled: true,
          provider: 'backup', model: 'model-a', concurrency_limit: 1,
          toolsets: ['web'], recommended_toolsets: ['web', 'file', 'skills', 'todo']
        },
        {
          role: 'research', label: '调研员工', description: '外部信息', enabled: true,
          provider: 'openrouter', model: 'model-b', concurrency_limit: 1,
          toolsets: ['learn'], recommended_toolsets: ['learn']
        },
        {
          role: 'coding', label: '工程员工', description: '代码工作', enabled: true,
          provider: 'backup', model: 'model-a', concurrency_limit: 1,
          toolsets: ['file'], recommended_toolsets: ['file', 'terminal', 'code_execution', 'skills', 'todo']
        },
        {
          role: 'media', label: '媒体员工', description: '媒体工作', enabled: true,
          provider: 'openrouter', model: 'model-b', concurrency_limit: 1,
          toolsets: ['web'], recommended_toolsets: ['media', 'web']
        }
      ]
      body.toolsets = [
        'assistant', 'browser', 'code_execution', 'file', 'learn', 'media', 'skills', 'terminal', 'todo', 'web'
      ].map(name => ({ name }))
    }
    if (Array.isArray(body.providers) && body.providers[0]) {
      body.providers[0].model_catalog = {
        models: ['cached-model'],
        updated_at: '2026-08-10T01:02:03+00:00'
      }
    }
    if (Array.isArray(body.roles)) {
      const limits = { general: 1, research: 1, coding: 1, media: 1 }
      for (const role of body.roles) {
        if (typeof role.role === 'string' && role.role in limits) {
          role.concurrency_limit = limits[role.role as keyof typeof limits]
        }
      }
    }
    await route.fulfill({ response, contentType: 'application/json', body: JSON.stringify(body) })
  })
  await page.route('**/provider-pool/providers/*/test', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ status: 'ok', latency_ms: 42, model_count: 2 })
  }))
  await page.route('**/provider-pool/providers/*/models', (route) => {
    expect(route.request().method()).toBe('POST')
    return route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'refreshed',
        count: 2,
        models: ['model-a', 'model-b'],
        updated_at: '2026-08-10T02:03:04+00:00'
      })
    })
  })
  await page.route('**/provider-pool/scheduler', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      status: 'ok',
      max_concurrent: 4,
      active_count: 1,
      queued_count: 2,
      roles: [
        { role: 'general', active: 0, queued: 1, limit: 1 },
        { role: 'research', active: 1, queued: 1, limit: 1 },
        { role: 'coding', active: 0, queued: 0, limit: 1 },
        { role: 'media', active: 0, queued: 0, limit: 1 }
      ],
      providers: [{
        provider: 'openrouter',
        active: 1,
        queued: 2,
        limit: 2,
        cooldown_until: '2026-08-10T02:04:00+00:00',
        cooldown_remaining_seconds: 38,
        failure_count: 1,
        last_status: 429,
        metrics: {
          sample_size: 12,
          success_count: 9,
          success_rate_percent: 75,
          average_elapsed_ms: 1500,
          rate_limit_count: 2,
          last_completed_at: '2026-08-10T02:03:04+00:00'
        }
      }]
    })
  }))
  await page.route('**/provider-pool/providers/*/cooldown/reset', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      status: 'reset',
      provider: 'openrouter',
      cleared: true,
      scheduler: {
        max_concurrent: 4,
        active_count: 1,
        queued_count: 2,
        roles: [],
        providers: [{
          provider: 'openrouter', active: 1, queued: 2, limit: 2,
          cooldown_until: '', cooldown_remaining_seconds: 0,
          failure_count: 0, last_status: null,
          metrics: {
            sample_size: 12, success_count: 9, success_rate_percent: 75,
            average_elapsed_ms: 1500, rate_limit_count: 2,
            last_completed_at: '2026-08-10T02:03:04+00:00'
          }
        }]
      }
    })
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
    await page.goto(supervisorUiUrl, { waitUntil: 'domcontentloaded' })
    await page.setViewportSize({ width: 1024, height: 768 })
    await page.locator('.dock-btn[data-panel="account"]').click({ force: true })
    await expect(page.locator('#panelAccount')).toHaveClass(/open/)
    await expect(page.locator('#accountDesktopLogin')).toHaveText('在桌面应用中登录')
    await expect(page.locator('#panelAccountBody')).not.toContainText('从浏览器导入 Cookie')
    await expect(page.locator('#panelAccountBody')).not.toContainText(/SESSDATA|bili_jct|DedeUserID/)
    await expect(page.locator('#accountPlatform')).toBeInViewport()
    await expect(page.locator('#accountLabel')).toBeInViewport()
    const accountPanelBox = await page.locator('#panelAccount').boundingBox()
    expect(accountPanelBox).not.toBeNull()
    expect(accountPanelBox!.x).toBeGreaterThanOrEqual(0)
    expect(accountPanelBox!.x + accountPanelBox!.width).toBeLessThanOrEqual(1024)
    const accountOverflow = await page.locator('#panelAccount').evaluate((panel) => {
      const panelRect = panel.getBoundingClientRect()
      const selectors = [
        '.panel-header',
        '.panel-body',
        '.account-card',
        '.account-add-bar',
        '.account-fields',
        '.provider-field',
        '.provider-action'
      ]
      return Array.from(panel.querySelectorAll<HTMLElement>(selectors.join(',')))
        .filter((element) => {
          const rect = element.getBoundingClientRect()
          return rect.left < panelRect.left - 1 || rect.right > panelRect.right + 1
        })
        .map((element) => element.className || element.id || element.tagName)
    })
    expect(accountOverflow).toEqual([])
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({
      path: 'test-results/account-center-desktop.png',
      animations: 'disabled'
    })
    await page.locator('#panelAccount .panel-close').click()
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.locator('.dock-btn[data-panel="settings"]').dispatchEvent('click')
    await expect(page.locator('#panelSettings')).toHaveClass(/open/)
    await expect(page.locator('#providerForm')).toBeInViewport()
    const providerRows = page.locator('#providerPoolList .provider-list-row')
    await expect.poll(() => providerRows.count()).toBeGreaterThanOrEqual(2)
    const providerCount = await providerRows.count()
    await expect(page.locator('#providerPoolList')).toContainText('员工验证正常')
    await expect(page.locator('#providerPoolList')).toContainText('冷却 38 秒')
    await providerRows.nth(1).click()
    await expect(page.locator('#panelSettings')).toHaveClass(/open/)
    await expect(providerRows.nth(1)).toHaveClass(/active/)
    await expect(page.locator('#providerRuntimeMetrics')).toHaveText(
      '近 12 次 · 成功率 75% · 平均 1.5 s · 429 2 次 · 冷却剩余 38 秒'
    )
    await expect(page.locator('#providerCooldownReset')).toBeEnabled()
    await page.locator('#providerCooldownReset').click()
    await expect(page.locator('#providerPoolStatus')).toHaveText('已解除冷却')
    await expect(page.locator('#providerRuntimeMetrics')).not.toContainText('冷却剩余')
    await expect(page.locator('#providerCooldownReset')).toBeDisabled()
    await providerRows.first().click()
    await expect(page.locator('#panelSettings')).toHaveClass(/open/)
    const loadedProviderKey = await providerRows.first().getAttribute('data-provider-key')
    await expect(page.locator('#providerKey')).toBeDisabled()
    await expect(page.locator('#providerDelete')).toBeDisabled()
    await expect(page.locator('#providerApiKey')).toHaveValue('')
    await expect(page.locator('#providerApiKey')).toHaveAttribute('type', 'password')
    await expect(page.locator('#providerConcurrency')).toHaveValue('2')
    await expect(page.locator('#providerTest')).toBeEnabled()
    await expect(page.locator('#providerLoadModels')).toBeEnabled()
    await expect(page.locator('#providerModelOptions option')).toHaveAttribute('value', 'cached-model')
    await expect(page.locator('#providerModelCatalogMeta')).toContainText('目录 1 个')
    await page.locator('#providerTest').click()
    await expect(page.locator('#providerPoolStatus')).toHaveText('连接正常 · 42 ms · 2 个模型')
    await page.locator('#providerLoadModels').click()
    await expect(page.locator('#providerPoolStatus')).toHaveText('已更新 2 个模型')
    await expect(page.locator('#providerModelOptions option')).toHaveCount(2)
    await expect(page.locator('#providerModelCatalogMeta')).toContainText('目录 2 个')
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({
      path: 'test-results/provider-pool-desktop.png',
      fullPage: true,
      animations: 'disabled'
    })

    await page.locator('[data-settings-view="workers"]').click()
    await expect(page.locator('#workerRoleList .worker-role-row')).toHaveCount(4)
    await expect(page.locator('#workerMaxConcurrent')).toHaveValue('4')
    await expect(page.locator('#workerSchedulerStatus')).toHaveText('运行 1 / 4 · 排队 2')
    await expect(page.locator('[data-worker-role="research"] [data-worker-dispatch-state]')).toHaveText(
      '运行 1 · 排队 1 · 上限 1'
    )
    await expect(page.locator('[data-worker-concurrency]')).toHaveCount(4)
    await expect(page.locator('[data-worker-role="research"] [data-worker-test-state]')).toHaveText(
      '已验证 · 1.2 s · history-model'
    )
    await expect(page.locator('[data-worker-model]').first()).toHaveJSProperty('tagName', 'SELECT')
    await page.locator('#workerRecommendedApply').click()
    await expect(page.locator('[data-worker-role="general"] .worker-toolsets summary')).toHaveText('推荐 · 4 个')
    await expect(page.locator('[data-worker-role="research"] .worker-toolsets summary')).toHaveText('推荐 · 1 个')
    await expect(page.locator('[data-worker-role="coding"] .worker-toolsets summary')).toHaveText('推荐 · 5 个')
    await expect(page.locator('[data-worker-role="media"] .worker-toolsets summary')).toHaveText('推荐 · 2 个')
    await expect(page.locator('#workerAssignmentStatus')).toHaveText('已应用推荐，请保存')
    const firstProviderSelect = page.locator('[data-worker-provider]').first()
    await expect(firstProviderSelect.locator('option')).toHaveCount(providerCount + 1)
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
    await page.unrouteAll({ behavior: 'ignoreErrors' })
    await browser.close()
  }
})
