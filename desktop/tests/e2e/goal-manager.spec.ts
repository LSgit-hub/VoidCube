import { expect, test, type Page } from '@playwright/test'
import { launchSupervisorPage } from './helpers/supervisor-page'

const goalServiceUrl = 'http://127.0.0.1:6003'
const projectId = 'project-m3-smoke'
const rootId = 'root-m3-smoke'

function buildFixture() {
  const root = {
    id: rootId,
    project_id: projectId,
    node_type: 'project',
    title: 'M3 总览验证',
    description: '',
    status: 'in_progress',
    progress: 0.42,
    version: 1,
    acceptance_criteria: []
  }
  const nodes = [root, ...Array.from({ length: 500 }, (_, index) => ({
    id: `goal-m3-${index}`,
    project_id: projectId,
    node_type: index % 3 === 0 ? 'feature' : 'task',
    title: `目标节点 ${index}`,
    description: '',
    status: index % 5 === 0 ? 'blocked' : 'in_progress',
    progress: (index % 11) / 10,
    version: 1,
    acceptance_criteria: []
  }))]
  const edges = nodes.slice(1).map((node) => ({
    id: `edge-m3-${node.id}`,
    project_id: projectId,
    source_id: rootId,
    target_id: node.id,
    edge_type: 'decomposes_to',
    progress_weight: 1,
    required: true
  }))
  return { root, nodes, edges }
}

async function installGoalServiceRoute(page: Page): Promise<void> {
  const fixture = buildFixture()
  Object.assign(fixture.root, {
    acceptance_criteria: [{ text: '回归测试通过', met: false }],
    evidence: [],
    events: [{ id: 'event-m4-seed', batch_id: 'batch-m4-seed', event_type: 'create_node', reason: 'seed' }]
  })
  await page.route(`${goalServiceUrl}/**`, async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname === '/api/goals/projects' && request.method() === 'GET') {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          projects: [{
            id: projectId,
            name: fixture.root.title,
            description: '',
            root_node_id: rootId,
            progress: fixture.root.progress
          }]
        })
      })
      return
    }
    if (url.pathname === `/api/goals/projects/${projectId}/focus`) {
      const nodeId = url.searchParams.get('node') || rootId
      const focus = fixture.nodes.find((node) => node.id === nodeId) || fixture.root
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          focus,
          children: nodeId === rootId ? fixture.nodes.slice(1, 3) : [],
          parent_hint_count: nodeId === rootId ? 0 : 1,
          can_back: nodeId !== rootId,
          can_forward: false
        })
      })
      return
    }
    if (url.pathname === `/api/goals/projects/${projectId}/overview`) {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ nodes: fixture.nodes, edges: fixture.edges })
      })
      return
    }
    if (url.pathname === '/api/goals/events/latest') {
      await route.fulfill({ contentType: 'application/json', body: '{"event_id":null}' })
      return
    }
    if (url.pathname === '/api/goals/events/stream' || url.pathname === `/api/goals/projects/${projectId}/events`) {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: ': keep-alive\n\n'
      })
      return
    }
    if (url.pathname.startsWith('/api/goals/nodes/')) {
      const nodeId = url.pathname.split('/').pop()
      const node = fixture.nodes.find((item) => item.id === nodeId) || fixture.root
      if (request.method() === 'PATCH') {
        const body = request.postDataJSON() as { patch?: Record<string, unknown> }
        Object.assign(node, body.patch ?? {})
        node.version += 1
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ node }) })
        return
      }
      if (request.method() === 'POST' && url.pathname.endsWith('/evidence')) {
        const body = request.postDataJSON() as { evidence_type?: string; title?: string; uri?: string; content?: string }
        const evidence = {
          id: 'evidence-m4',
          evidence_type: body.evidence_type ?? 'manual',
          title: body.title ?? '',
          uri: body.uri ?? '',
          content: body.content ?? ''
        }
        fixture.root.evidence = [...(fixture.root.evidence ?? []), evidence]
        await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ evidence, batch_id: 'batch-m4-evidence' }) })
        return
      }
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(node) })
      return
    }
    if (url.pathname === '/api/goals/rollback' && request.method() === 'POST') {
      await route.fulfill({ contentType: 'application/json', body: '{"batch_id":"batch-m4-seed","rolled_back":true}' })
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"not mocked"}' })
  })
}

test('renders a 500-node overview in a worker and keeps focus synchronized', async () => {
  const { browser, page, pageErrors } = await launchSupervisorPage({ width: 1280, height: 900 })
  await installGoalServiceRoute(page)
  try {
    await page.goto('http://127.0.0.1:6002/ui/goal-manager/', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('#overview-content .overview-node')).toHaveCount(501)
    await expect(page.locator('#overview-svg')).toHaveAttribute('width', /\d+/)
    await expect(page.locator('#overview-content .overview-node.focused')).toHaveCount(1)
    await expect(page.locator('#overview-content .overview-node.direct')).toHaveCount(2)
    await page.locator('#overview-content [data-node-id="goal-m3-0"]').dispatchEvent('click')
    await expect(page.locator('#focus-heading')).toHaveText('目标节点 0')
    expect(pageErrors).toEqual([])
    await page.screenshot({ path: 'test-results/goal-manager-m3-overview.png', fullPage: true })
  } finally {
    await browser.close()
  }
})

test('contains the overview inside the page on mobile', async () => {
  const { browser, page, pageErrors } = await launchSupervisorPage({ width: 390, height: 844 })
  await installGoalServiceRoute(page)
  try {
    await page.goto('http://127.0.0.1:6002/ui/goal-manager/', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('#overview-content .overview-node')).toHaveCount(501)
    const dimensions = await page.evaluate(() => ({
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
      overviewScrollWidth: document.querySelector('.overview-wrap')?.scrollWidth ?? 0,
      overviewClientWidth: document.querySelector('.overview-wrap')?.clientWidth ?? 0
    }))
    expect(dimensions.documentWidth).toBeLessThanOrEqual(dimensions.viewportWidth + 1)
    expect(dimensions.overviewScrollWidth).toBeGreaterThan(dimensions.overviewClientWidth)
    expect(pageErrors).toEqual([])
    await page.screenshot({ path: 'test-results/goal-manager-m3-mobile.png', fullPage: true })
  } finally {
    await browser.close()
  }
})

test('supports detail editing, evidence write-back, rollback, and context actions', async () => {
  const { browser, page, pageErrors } = await launchSupervisorPage({ width: 1280, height: 900 })
  await installGoalServiceRoute(page)
  try {
    await page.goto('http://127.0.0.1:6002/ui/goal-manager/', { waitUntil: 'domcontentloaded' })
    const root = page.locator(`#radial-content [data-node-id="${rootId}"]`)
    await root.click()
    await expect(page.locator('#detail-title')).toHaveText('M3 总览验证')
    await expect(page.locator('#add-evidence-button')).toBeVisible()

    await page.locator('#edit-detail-button').click()
    await page.locator('#detail-edit-title').fill('M4 已编辑目标')
    await page.locator('#detail-edit-reason').fill('浏览器回归编辑')
    await page.locator('#save-detail-edit').click()
    await expect(page.locator('#detail-title')).toHaveText('M4 已编辑目标')

    await page.locator('#add-evidence-button').click()
    await expect(page.locator('#goal-dialog')).toBeVisible()
    await page.locator('#evidence-title').fill('Playwright 回归')
    await page.locator('#evidence-reason').fill('验证证据写回')
    await page.locator('#dialog-confirm-button').click()
    await expect(page.locator('.evidence-item')).toContainText('Playwright 回归')

    await root.click({ button: 'right' })
    await expect(page.locator('#node-menu')).toBeVisible()
    await page.locator('#node-menu [data-menu-action="rollback"]').click()
    await expect(page.locator('#goal-dialog')).toBeVisible()
    await page.locator('#dialog-confirm-button').click()
    await expect(page.locator('#goal-dialog')).toBeHidden()
    expect(pageErrors).toEqual([])
  } finally {
    await browser.close()
  }
})
