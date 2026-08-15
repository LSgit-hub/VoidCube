import { chromium, type Page } from '@playwright/test'

const supervisorPort = Number(process.env.VOIDCUBE_PLAYWRIGHT_SUPERVISOR_PORT || 6002)
export const supervisorUiUrl = `http://127.0.0.1:${supervisorPort}/ui`

export interface SupervisorAccountsFixture {
  accounts: Array<Record<string, unknown>>
  supported_platforms: string[]
  accounts_revision: number
}

export interface SupervisorPageContext {
  browser: Awaited<ReturnType<typeof chromium.launch>>
  page: Page
  pageErrors: string[]
}

export async function launchSupervisorPage(
  viewport: { width: number; height: number }
): Promise<SupervisorPageContext> {
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport })
  const pageErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.route('**/ui/media-events', (route) => route.fulfill({
    status: 200,
    contentType: 'text/event-stream',
    body: ''
  }))
  return { browser, page, pageErrors }
}

export async function installAccountsRoute(
  page: Page,
  fixture: SupervisorAccountsFixture
): Promise<void> {
  await page.route('**/ui/accounts', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify(fixture)
  }))
}
