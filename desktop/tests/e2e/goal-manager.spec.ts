import { expect, test, type Page } from '@playwright/test'
import { launchSupervisorPage } from './helpers/supervisor-page'

const goalServiceUrl = 'http://127.0.0.1:6003'
const projectId = 'project-m3-smoke'
const rootId = 'root-m3-smoke'

type GoalFixtureNode = {
  id: string
  project_id: string
  node_type: string
  title: string
  description: string
  status: string
  progress: number
  version: number
  acceptance_criteria: Array<{ text: string; met: boolean }>
  evidence?: Array<Record<string, string>>
  events?: Array<Record<string, string>>
}

function buildFixture(): {
  root: GoalFixtureNode
  nodes: GoalFixtureNode[]
  edges: Array<Record<string, unknown>>
} {
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

async function installGoalServiceRoute(page: Page): Promise<() => void> {
  const fixture = buildFixture()
  let rolledBack = false
  let liveEventPending = false
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
    if (url.pathname === `/api/goals/projects/${projectId}` && request.method() === 'GET') {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          id: projectId,
          name: fixture.root.title,
          description: '',
          root_node_id: rootId,
          progress: fixture.root.progress
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
    if (url.pathname === `/api/goals/projects/${projectId}/history`) {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          can_undo: !rolledBack,
          can_redo: rolledBack,
          undo_batch_id: rolledBack ? null : 'batch-m4-seed',
          redo_batch_id: rolledBack ? 'batch-m4-seed' : null
        })
      })
      return
    }
    if (url.pathname === '/api/goals/batch' && request.method() === 'POST') {
      const body = request.postDataJSON() as {
        operations?: Array<Record<string, unknown>>
      }
      const createOperation = body.operations?.find((operation) => operation.op === 'create_node')
      const edgeOperation = body.operations?.find((operation) => operation.op === 'create_edge')
      if (!createOperation || !edgeOperation) {
        await route.fulfill({ status: 422, contentType: 'application/json', body: '{"detail":"invalid batch"}' })
        return
      }
      const nodeType = typeof createOperation.node_type === 'string' ? createOperation.node_type : 'task'
      const title = typeof createOperation.title === 'string' ? createOperation.title : '新建子目标'
      const description = typeof createOperation.description === 'string'
        ? createOperation.description
        : ''
      const child = {
        id: 'goal-m4-created-child',
        project_id: projectId,
        node_type: nodeType,
        title,
        description,
        status: 'planned',
        progress: 0,
        version: 1,
        acceptance_criteria: []
      }
      fixture.nodes.splice(1, 0, child)
      fixture.edges.push({
        id: 'edge-m4-created-child',
        project_id: projectId,
        source_id: edgeOperation.source_id ?? rootId,
        target_id: child.id,
        edge_type: 'decomposes_to',
        progress_weight: 1,
        required: true
      })
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          batch_id: 'batch-m4-create-child',
          temp_ids: { new_child: child.id },
          results: [{ op: 'create_node', node: child }]
        })
      })
      return
    }
    if (url.pathname === '/api/goals/events/stream' || url.pathname === `/api/goals/projects/${projectId}/events`) {
      if (liveEventPending) {
        liveEventPending = false
        await route.fulfill({
          status: 200,
          contentType: 'text/event-stream',
          body: [
            'id: event-m4-live',
            'data: {"id":"event-m4-live","event_type":"update_node","batch_id":"batch-m4-live","reason":"external batch"}',
            '',
            ''
          ].join('\n')
        })
        return
      }
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
      if (request.method() === 'DELETE') {
        const confirmToken = url.searchParams.get('confirm_token')
        if (confirmToken !== 'delete-token-m4') {
          await route.fulfill({
            status: 409,
            contentType: 'application/json',
            body: JSON.stringify({
              detail: '确认后才能删除',
              requires_confirm: true,
              confirm_token: 'delete-token-m4'
            })
          })
          return
        }
        const index = fixture.nodes.findIndex((item) => item.id === nodeId)
        if (index >= 0) fixture.nodes.splice(index, 1)
        fixture.edges = fixture.edges.filter((edge) => (
          edge.source_id !== nodeId && edge.target_id !== nodeId
        ))
        await route.fulfill({ contentType: 'application/json', body: '{"deleted":true}' })
        return
      }
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(node) })
      return
    }
    if (url.pathname === '/api/goals/rollback' && request.method() === 'POST') {
      rolledBack = true
      await route.fulfill({ contentType: 'application/json', body: '{"batch_id":"batch-m4-seed","rolled_back":true}' })
      return
    }
    if (url.pathname === '/api/goals/redo' && request.method() === 'POST') {
      rolledBack = false
      await route.fulfill({ contentType: 'application/json', body: '{"batch_id":"batch-m4-seed","redone":true}' })
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"not mocked"}' })
  })
  return () => {
    fixture.root.title = 'M4 SSE 已刷新'
    fixture.root.progress = 0.73
    liveEventPending = true
  }
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
    await expect(root).toBeVisible()
    await root.dispatchEvent('click')
    await expect(page.locator('#detail-title')).toHaveText('M3 总览验证')
    await expect(page.locator('#add-evidence-button')).toBeVisible()

    await page.locator('#add-child-button').click()
    await expect(page.locator('#goal-dialog')).toBeVisible()
    await page.locator('#create-child-title').fill('新建训练任务')
    await page.locator('#dialog-confirm-button').click()
    await expect(page.locator('#focus-heading')).toHaveText('M3 总览验证')
    await expect(page.locator('#radial-content [data-node-id="goal-m4-created-child"]')).toBeVisible()

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

    await root.dispatchEvent('contextmenu', {
      button: 2,
      clientX: 220,
      clientY: 180
    })
    await expect(page.locator('#node-menu')).toBeVisible()
    await page.locator('#node-menu [data-menu-action="rollback"]').dispatchEvent('click')
    await expect(page.locator('#goal-dialog')).toBeVisible()
    await page.locator('#dialog-confirm-button').click()
    await expect(page.locator('#goal-dialog')).toBeHidden()
    expect(pageErrors).toEqual([])
  } finally {
    await browser.close()
  }
})

test('completes server confirm-token flow for deleting a child node', async () => {
  const { browser, page, pageErrors } = await launchSupervisorPage({ width: 1280, height: 900 })
  await installGoalServiceRoute(page)
  const deleteRequests: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'DELETE') deleteRequests.push(request.url())
  })
  try {
    await page.goto('http://127.0.0.1:6002/ui/goal-manager/', { waitUntil: 'domcontentloaded' })
    const child = page.locator('#radial-content [data-node-id="goal-m3-0"]')
    await expect(child).toBeVisible()
    await child.dispatchEvent('contextmenu', {
      button: 2,
      clientX: 220,
      clientY: 180
    })
    await page.locator('#node-menu [data-menu-action="delete"]').dispatchEvent('click')
    await expect(page.locator('#goal-dialog')).toBeVisible()
    await page.locator('#dialog-confirm-button').click()
    await expect(page.locator('#dialog-title')).toHaveText('服务端确认删除')
    await page.locator('#dialog-confirm-button').click()
    await expect(page.locator('#goal-dialog')).toBeHidden()
    expect(deleteRequests).toHaveLength(2)
    expect(deleteRequests[1]).toContain('confirm_token=delete-token-m4')
    expect(pageErrors).toEqual([])
  } finally {
    await browser.close()
  }
})

test('supports project-level undo and redo controls', async () => {
  const { browser, page, pageErrors } = await launchSupervisorPage({ width: 1280, height: 900 })
  await installGoalServiceRoute(page)
  try {
    await page.goto('http://127.0.0.1:6002/ui/goal-manager/', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('#undo-button')).toBeEnabled()
    await expect(page.locator('#redo-button')).toBeDisabled()

    await page.locator('#undo-button').click()
    await expect(page.locator('#goal-dialog')).toBeVisible()
    await page.locator('#dialog-confirm-button').click()
    await expect(page.locator('#undo-button')).toBeDisabled()
    await expect(page.locator('#redo-button')).toBeEnabled()

    await page.locator('#redo-button').click()
    await expect(page.locator('#goal-dialog')).toBeVisible()
    await page.locator('#dialog-confirm-button').click()
    await expect(page.locator('#redo-button')).toBeDisabled()
    await expect(page.locator('#undo-button')).toBeEnabled()
    expect(pageErrors).toEqual([])
  } finally {
    await browser.close()
  }
})

test('refreshes focus after an external batch SSE event', async () => {
  const { browser, page, pageErrors } = await launchSupervisorPage({ width: 1280, height: 900 })
  const publishLiveEvent = await installGoalServiceRoute(page)
  try {
    await page.goto('http://127.0.0.1:6002/ui/goal-manager/', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('#focus-heading')).toHaveText('M3 总览验证')
    publishLiveEvent()
    await expect(page.locator('#focus-heading')).toHaveText('M4 SSE 已刷新', { timeout: 15_000 })
    await expect(page.locator('#project-progress')).toHaveText('项目进度 73%')
    expect(pageErrors).toEqual([])
  } finally {
    await browser.close()
  }
})
