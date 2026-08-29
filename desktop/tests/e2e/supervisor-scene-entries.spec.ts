import { expect, test } from '@playwright/test'
import {
  launchSupervisorPage,
  supervisorUiUrl
} from './helpers/supervisor-page'

test('scene objects route to their owning desktop panels', async () => {
  const { browser, page, pageErrors } = await launchSupervisorPage({ width: 1440, height: 900 })
  const requestedUrls: string[] = []
  page.on('request', (request) => requestedUrls.push(request.url()))

  try {
    await page.goto(supervisorUiUrl, { waitUntil: 'domcontentloaded' })
    await expect(page.locator('.room')).toBeVisible()
    await expect(page.locator('#sceneMiniSummary')).not.toHaveText('')
    await expect(page.locator('#sceneSyncStatus')).toContainText(/已连接|数据可能过期/)

    await page.locator('.plant-corner').click()
    await expect(page.locator('#detailDrawer')).toHaveClass(/open/)
    await expect(page.locator('#detailDrawerTitle')).toContainText('Mem 结构与统计')
    await expect(page.locator('#detailDrawerClose')).toBeFocused()
    await page.locator('#detailDrawerClose').press('Escape')
    await expect(page.locator('#detailDrawer')).not.toHaveClass(/open/)
    await expect(page.locator('.plant-corner')).toBeFocused()

    await page.locator('.desk-monitor').press('Enter')
    await expect(page.locator('#detailDrawer')).toHaveClass(/open/)
    await expect(page.locator('#detailDrawerTitle')).toContainText('员工代理执行详情')
    await page.locator('#detailDrawerClose').click()

    const requestsBeforePaper = requestedUrls.filter((url) => url.includes('/scheduled-tasks')).length
    await page.locator('.desk-write').press('Enter')
    await expect(page.locator('#detailDrawer')).toHaveClass(/open/)
    await expect(page.locator('#detailDrawerTitle')).toContainText('星子自主任务安排')
    await expect.poll(() => requestedUrls.filter((url) => url.includes('/scheduled-tasks')).length)
      .toBe(requestsBeforePaper)
    await page.locator('#detailDrawerClose').click()

    await page.locator('#scheduleClock').click()
    await expect(page.locator('#panelSchedules')).toHaveClass(/open/)
    await expect(page.locator('#panelSchedules')).toContainText('API-A 定时任务')
    await expect.poll(() => requestedUrls.filter((url) => url.includes('/scheduled-tasks')).length)
      .toBeGreaterThan(requestsBeforePaper)

    await expect(page.locator('[data-drill="outboxes"]')).toHaveCount(0)
    await page.screenshot({
      path: 'test-results/supervisor-scene-entries.png',
      fullPage: true,
      animations: 'disabled'
    })
    expect(pageErrors).toEqual([])
  } finally {
    await page.unrouteAll({ behavior: 'ignoreErrors' })
    await browser.close()
  }
})
