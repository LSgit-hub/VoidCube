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
  let projectDeleted = false
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
          projects: projectDeleted ? [] : [{
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
    if (url.pathname === '/api/goals/projects/archived' && request.method() === 'GET') {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          projects: projectDeleted ? [{
            id: projectId,
            name: fixture.root.title,
            description: '',
            root_node_id: rootId,
            deleted_at: '2026-10-07T12:00:00Z',
            deleted_batch_id: 'batch-delete-project-m4'
          }] : []
        })
      })
      return
    }
    if (url.pathname === `/api/goals/projects/${projectId}` && request.method() === 'DELETE') {
      if (url.searchParams.get('confirm_token') !== 'delete-project-token-m4') {
        await route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: '确认后才能删除项目',
            requires_confirm: true,
            confirm_token: 'delete-project-token-m4'
          })
        })
        return
      }
      projectDeleted = true
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ project_id: projectId, deleted: true, batch_id: 'batch-delete-project-m4' })
      })
      return
    }
    if (url.pathname === `/api/goals/projects/${projectId}/restore` && request.method() === 'POST') {
      projectDeleted = false
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ project_id: projectId, restored: true, batch_id: 'batch-delete-project-m4' })
      })
      return
    }
    if (url.pathname === `/api/goals/projects/${projectId}/purge` && request.method() === 'DELETE') {
      if (url.searchParams.get('confirm_name') !== fixture.root.title) {
        await route.fulfill({ status: 409, contentType: 'application/json', body: '{"detail":"project name confirmation does not match"}' })
        return
      }
      if (url.searchParams.get('confirm_token') !== 'purge-project-token-m4') {
        await route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({ detail: '不可逆操作需要确认', requires_confirm: true, confirm_token: 'purge-project-token-m4' })
        })
        return
      }
      projectDeleted = false
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ project_id: projectId, purged: true }) })
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

test('deletes the current project through the server confirmation flow', async () => {
  const { browser, page, pageErrors } = await launchSupervisorPage({ width: 1280, height: 900 })
  await installGoalServiceRoute(page)
  const deleteRequests: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'DELETE') deleteRequests.push(request.url())
  })
  try {
    await page.goto('http://127.0.0.1:6002/ui/goal-manager/', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('#delete-project-button')).toBeEnabled()
    await page.locator('#delete-project-button').click()
    await expect(page.locator('#dialog-title')).toHaveText('删除目标项目')
    await page.locator('#dialog-confirm-button').click()
    await expect(page.locator('#dialog-title')).toHaveText('服务端确认删除项目')
    await page.locator('#dialog-confirm-button').click()
    await expect(page.locator('#project-select')).toHaveValue('')
    await expect(page.locator('#project-select option')).toHaveText('暂无项目')
    await expect(page.locator('#delete-project-button')).toBeDisabled()
    await expect(page.locator('#status-text')).toHaveText('项目已删除，目标和关系已归档')
    expect(deleteRequests).toHaveLength(2)
    expect(deleteRequests[1]).toContain('confirm_token=delete-project-token-m4')
    expect(pageErrors).toEqual([])
  } finally {
    await browser.close()
  }
})

test('fits both views and restores the previous viewport', async () => {
  const { browser, page, pageErrors } = await launchSupervisorPage({ width: 1280, height: 900 })
  await installGoalServiceRoute(page)
  try {
    await page.goto('http://127.0.0.1:6002/ui/goal-manager/', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('#overview-content .overview-node')).toHaveCount(501)
    await expect(page.locator('#overview-restore-button')).toBeDisabled()
    await expect(page.locator('#focus-restore-button')).toBeDisabled()

    await page.locator('#overview-zoom-in').click()
    await expect(page.locator('#overview-zoom-label')).toHaveText('110%')
    await expect(page.locator('#overview-restore-button')).toBeEnabled()
    await page.locator('#overview-restore-button').click()
    await expect(page.locator('#overview-zoom-label')).toHaveText('100%')
    await page.locator('#overview-restore-button').click()
    await expect(page.locator('#overview-zoom-label')).toHaveText('110%')
    const overviewBox = await page.locator('#overview-wrap').boundingBox()
    if (!overviewBox) throw new Error('overview is not laid out')
    const overviewBeforePan = await page.locator('#overview-content').getAttribute('transform')
    await page.mouse.move(overviewBox.x + overviewBox.width - 30, overviewBox.y + overviewBox.height - 30)
    await page.mouse.down()
    await page.mouse.move(overviewBox.x + overviewBox.width - 95, overviewBox.y + overviewBox.height - 65, { steps: 3 })
    await page.mouse.up()
    const overviewAfterPan = await page.locator('#overview-content').getAttribute('transform')
    expect(overviewAfterPan).not.toBe(overviewBeforePan)
    await page.locator('#overview-restore-button').click()
    await expect(page.locator('#overview-content')).toHaveAttribute('transform', overviewBeforePan ?? '')
    await page.locator('#overview-restore-button').click()
    await expect(page.locator('#overview-content')).not.toHaveAttribute('transform', overviewBeforePan ?? '')

    await page.locator('#overview-fit-button').click()
    const fitPercent = Number((await page.locator('#overview-zoom-label').textContent())?.replace('%', ''))
    expect(fitPercent).toBeGreaterThan(0)
    expect(fitPercent).toBeLessThan(100)

    const radialBox = await page.locator('#radial-svg').boundingBox()
    if (!radialBox) throw new Error('focus view is not laid out')
    await page.mouse.move(radialBox.x + radialBox.width / 2, radialBox.y + radialBox.height / 2)
    await page.mouse.wheel(0, -100)
    await expect(page.locator('#zoom-label')).toHaveText('105%')
    await expect(page.locator('#focus-restore-button')).toBeEnabled()
    await page.locator('#focus-restore-button').click()
    await expect(page.locator('#zoom-label')).toHaveText('100%')
    await page.locator('#focus-restore-button').click()
    await expect(page.locator('#zoom-label')).toHaveText('105%')
    const focusBeforePan = await page.locator('#radial-content').getAttribute('transform')
    await page.mouse.move(radialBox.x + 24, radialBox.y + 24)
    await page.mouse.down()
    await page.mouse.move(radialBox.x + 74, radialBox.y + 54, { steps: 3 })
    await page.mouse.up()
    const focusAfterPan = await page.locator('#radial-content').getAttribute('transform')
    expect(focusAfterPan).not.toBe(focusBeforePan)
    await page.locator('#focus-restore-button').click()
    await expect(page.locator('#radial-content')).toHaveAttribute('transform', focusBeforePan ?? '')
    await page.locator('#focus-restore-button').click()
    await expect(page.locator('#radial-content')).not.toHaveAttribute('transform', focusBeforePan ?? '')
    expect(pageErrors).toEqual([])
  } finally {
    await browser.close()
  }
})

test('restores a deleted project from the archived projects dialog', async () => {
  const { browser, page, pageErrors } = await launchSupervisorPage({ width: 1280, height: 900 })
  await installGoalServiceRoute(page)
  try {
    await page.goto('http://127.0.0.1:6002/ui/goal-manager/', { waitUntil: 'domcontentloaded' })
    await page.locator('#delete-project-button').click()
    await page.locator('#dialog-confirm-button').click()
    await page.locator('#dialog-confirm-button').click()
    await expect(page.locator('#project-select option')).toHaveText('暂无项目')

    await page.locator('#archived-projects-button').click()
    await expect(page.locator('#dialog-title')).toHaveText('已归档项目')
    await expect(page.locator('[data-restore-project-id]')).toHaveCount(1)
    await page.locator('[data-restore-project-id]').click()
    await expect(page.locator('#dialog-title')).toHaveText('恢复归档项目')
    await page.locator('#dialog-confirm-button').click()
    await expect(page.locator('#project-select')).toHaveValue(projectId)
    await expect(page.locator('#focus-heading')).toHaveText('M3 总览验证')
    expect(pageErrors).toEqual([])
  } finally {
    await browser.close()
  }
})

test('filters archived projects and permanently deletes with name confirmation', async () => {
  const { browser, page, pageErrors } = await launchSupervisorPage({ width: 1280, height: 900 })
  await installGoalServiceRoute(page)
  try {
    await page.goto('http://127.0.0.1:6002/ui/goal-manager/', { waitUntil: 'domcontentloaded' })
    await page.locator('#delete-project-button').click()
    await page.locator('#dialog-confirm-button').click()
    await page.locator('#dialog-confirm-button').click()
    await page.locator('#archived-projects-button').click()
    await page.locator('#archived-project-search').fill('M3')
    await page.locator('#archived-project-filter button[type="submit"]').click()
    await expect(page.locator('[data-purge-project-id]')).toHaveCount(1)
    await page.locator('[data-purge-project-id]').click()
    await expect(page.locator('#dialog-title')).toHaveText('永久删除归档项目')
    await page.locator('#purge-project-name').fill('错误名称')
    await page.locator('#dialog-confirm-button').click()
    await expect(page.locator('#status-text')).toHaveText('项目名称不匹配，未执行永久删除')
    await page.locator('#purge-project-name').fill('M3 总览验证')
    await page.locator('#dialog-confirm-button').click()
    await expect(page.locator('#dialog-title')).toHaveText('服务端确认永久删除')
    await page.locator('#dialog-confirm-button').click()
    await expect(page.locator('#dialog-title')).toHaveText('已归档项目')
    await expect(page.locator('.archived-project-item')).toHaveCount(0)
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
