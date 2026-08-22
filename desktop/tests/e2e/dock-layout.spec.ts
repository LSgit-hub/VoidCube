import { expect, test } from '@playwright/test'
import {
  installAccountsRoute,
  launchSupervisorPage,
  supervisorUiUrl
} from './helpers/supervisor-page'

test('all Dock panels stay contained across responsive viewports', async () => {
  const { browser, page, pageErrors } = await launchSupervisorPage({ width: 1440, height: 800 })
  await installAccountsRoute(page, {
    accounts: [{
      id: 'layout-bilibili',
      platform: 'bilibili',
      platform_name: 'B站',
      label: '用于布局回归的超长账号标签不会推动相邻控件离开面板',
      cookies_count: 24,
      status: 'active'
    }],
    supported_platforms: ['bilibili'],
    accounts_revision: 0
  })
  await page.route('**/scheduled-tasks*', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ tasks: [], due_count: 0 })
  }))
  await page.route(/\/provider-pool(?:\?.*)?$/, async (route) => {
    const response = await route.fetch()
    const body = await response.json()
    if (Array.isArray(body.providers)) body.providers = body.providers.slice(0, 2)
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
  await page.route('**/provider-pool/scheduler', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      status: 'ok',
      max_concurrent: 4,
      active_count: 0,
      queued_count: 0,
      roles: [],
      providers: []
    })
  }))

  const assertPanelContained = async (panelSelector: string): Promise<void> => {
    const result = await page.locator(panelSelector).evaluate((panel) => {
      const panelRect = panel.getBoundingClientRect()
      const structuralOverflow = [
        panel,
        ...panel.querySelectorAll<HTMLElement>('.panel-header, .panel-body, .settings-tabs')
      ]
        .filter((element) => element.scrollWidth > element.clientWidth + 1)
        .map((element) => element.id || element.className || element.tagName)
      const oversizedControls = Array.from(
        panel.querySelectorAll<HTMLElement>('button, input, select, textarea, [role="button"]')
      )
        .filter((element) => {
          const style = getComputedStyle(element)
          if (style.display === 'none' || style.visibility === 'hidden') return false
          return element.getBoundingClientRect().width > panelRect.width + 1
        })
        .map((element) => element.id || element.className || element.tagName)
      return {
        box: { left: panelRect.left, right: panelRect.right },
        structuralOverflow,
        oversizedControls
      }
    })
    expect(result.box.left).toBeGreaterThanOrEqual(-1)
    expect(result.box.right).toBeLessThanOrEqual((await page.evaluate(() => innerWidth)) + 1)
    expect(result.structuralOverflow).toEqual([])
    expect(result.oversizedControls).toEqual([])
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  }

  const panels = [
    ['chain', '#panelChain'],
    ['lminput', '#panelLMInput'],
    ['cognition', '#panelCognition'],
    ['media', '#panelMedia'],
    ['observation', '#panelObservation'],
    ['stats', '#panelStats'],
    ['promotions', '#panelPromotions'],
    ['schedules', '#panelSchedules'],
    ['account', '#panelAccount']
  ] as const

  try {
    await page.goto(supervisorUiUrl, { waitUntil: 'domcontentloaded' })
    for (const width of [480, 600, 1024, 1440]) {
      await page.setViewportSize({ width, height: 800 })
      const settings = page.locator('.dock-btn[data-panel="settings"]')
      await settings.dispatchEvent('click')
      await expect(page.locator('#panelSettings')).toHaveClass(/open/)
      await assertPanelContained('#panelSettings')
      await page.screenshot({
        path: `test-results/dock-settings-${width}.png`,
        fullPage: true,
        animations: 'disabled'
      })

      for (const [name, selector] of panels) {
        const opener = name === 'schedules'
          ? page.locator('#scheduleClock')
          : page.locator(`.dock-btn[data-panel="${name}"]`)
        await opener.dispatchEvent('click')
        await expect(page.locator(selector)).toHaveClass(/open/)
        await assertPanelContained(selector)
      }
    }
    await page.setViewportSize({ width: 1440, height: 800 })
    await page.locator('.dock-btn[data-panel="chain"]').dispatchEvent('click')
    await expect(page.locator('#panelChain')).toHaveClass(/open/)
    await expect(page.locator('.dock-btn[data-panel="lminput"]')).toBeHidden()
    await expect(page.locator('#panelChain .observation-tab')).toHaveCount(4)
    await page.locator('#panelChain .observation-tab[data-observation-tab="cognition"]').click()
    await expect(page.locator('#panelCognition')).toHaveClass(/open/)
    await expect(page.locator('.dock-btn[data-panel="chain"]')).toHaveClass(/active/)
    expect(pageErrors).toEqual([])
  } finally {
    await page.unrouteAll({ behavior: 'ignoreErrors' })
    await browser.close()
  }
})
