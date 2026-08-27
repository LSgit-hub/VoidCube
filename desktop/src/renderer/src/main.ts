import {
  Box,
  Check,
  ChevronDown,
  createIcons,
  ExternalLink,
  FolderGit2,
  Monitor,
  Minus,
  PanelTop,
  Play,
  Puzzle,
  RefreshCw,
  RotateCcw,
  Rows3,
  ShieldCheck,
  Square,
  SquareTerminal,
  X
} from 'lucide'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import './style.css'
import { MonitorHealthGate } from './monitor-health'
import type {
  ServiceControlResult,
  ExecutionContext,
  PluginControlAction,
  PluginInfo,
  ServiceInfo,
  ServiceLifecycleAction,
  TerminalBackend,
  TerminalState
} from '../../shared/contracts'

type LayoutMode = 'split' | 'monitor' | 'terminal'

const SPLIT_STORAGE_KEY = 'voidcube.desktop.monitor-size'

const api = window.voidcubeDesktop
const terminalHost = requiredElement<HTMLDivElement>('terminal')
const monitorFrame = requiredElement<HTMLIFrameElement>('monitor-frame')
const monitorOverlay = requiredElement<HTMLDivElement>('monitor-overlay')
const monitorOverlayTitle = requiredElement<HTMLElement>('monitor-overlay-title')
const monitorOverlayDetail = requiredElement<HTMLElement>('monitor-overlay-detail')
const retryMonitor = requiredElement<HTMLButtonElement>('retry-monitor')
const pluginView = requiredElement<HTMLElement>('plugin-view')
const pluginViewTitle = requiredElement<HTMLElement>('plugin-view-title')
const pluginFrame = requiredElement<HTMLIFrameElement>('plugin-frame')
const pluginOverlay = requiredElement<HTMLElement>('plugin-overlay')
const closePluginViewButton = requiredElement<HTMLButtonElement>('close-plugin-view')
const terminalError = requiredElement<HTMLDivElement>('terminal-error')
const terminalErrorMessage = requiredElement<HTMLElement>('terminal-error-message')
const terminalMeta = requiredElement<HTMLElement>('terminal-meta')
const executionContext = requiredElement<HTMLElement>('execution-context')
const executionSelector = requiredElement<HTMLDetailsElement>('execution-selector')
const executionSelectorSummary = requiredElement<HTMLElement>('execution-selector-summary')
const executionMode = requiredElement<HTMLElement>('execution-mode')
const executionWorkspace = requiredElement<HTMLElement>('execution-workspace')
const bodyImprovementBackend = requiredElement<HTMLElement>('body-improvement-backend')
const localBackendLabel = requiredElement<HTMLElement>('local-backend-label')
const executionSelectorStatus = requiredElement<HTMLParagraphElement>('execution-selector-status')
const backendButtons = Array.from(
  document.querySelectorAll<HTMLButtonElement>('[data-terminal-backend]')
)
const pluginsSummary = requiredElement<HTMLElement>('plugins-summary')
const pluginList = requiredElement<HTMLElement>('plugin-list')
const pluginsEmpty = requiredElement<HTMLElement>('plugins-empty')
const pluginsError = requiredElement<HTMLParagraphElement>('plugins-error')
const servicesSummary = requiredElement<HTMLElement>('services-summary')
const serviceList = requiredElement<HTMLElement>('service-list')
const servicesError = requiredElement<HTMLParagraphElement>('services-error')
const pluginMenu = requiredElement<HTMLDetailsElement>('plugin-menu')
const serviceProcessMenu = requiredElement<HTMLDetailsElement>('service-process-menu')
const workspace = requiredElement<HTMLElement>('workspace')
const splitter = requiredElement<HTMLElement>('splitter')
const layoutButtons = Array.from(document.querySelectorAll<HTMLButtonElement>('[data-layout-mode]'))
const serviceButtons = [
  requiredElement<HTMLButtonElement>('start-services'),
  requiredElement<HTMLButtonElement>('restart-services'),
  requiredElement<HTMLButtonElement>('stop-services')
]

createIcons({
  icons: {
    Box,
    Check,
    ChevronDown,
    PanelTop,
    FolderGit2,
    Monitor,
    Play,
    Puzzle,
    RefreshCw,
    RotateCcw,
    Rows3,
    ShieldCheck,
    Square,
    SquareTerminal,
    Minus,
    X
  }
})

const terminal = new Terminal({
  allowTransparency: false,
  convertEol: false,
  cursorBlink: true,
  cursorStyle: 'block',
  drawBoldTextInBrightColors: true,
  fontFamily: '"Cascadia Code", "JetBrains Mono", "SFMono-Regular", Consolas, monospace',
  fontSize: 14,
  fontWeight: '400',
  fontWeightBold: '600',
  letterSpacing: 0,
  lineHeight: 1.25,
  scrollback: 20_000,
  theme: {
    background: '#0d1117',
    foreground: '#d7dde5',
    cursor: '#68d5b3',
    cursorAccent: '#0d1117',
    selectionBackground: '#316d785c',
    black: '#11151b',
    red: '#ef7f79',
    green: '#68d5b3',
    yellow: '#e6bd69',
    blue: '#70a8e8',
    magenta: '#bd93d8',
    cyan: '#64c7d7',
    white: '#d7dde5',
    brightBlack: '#697386',
    brightRed: '#ff9a94',
    brightGreen: '#83e4c4',
    brightYellow: '#f2cf81',
    brightBlue: '#8cbbf0',
    brightMagenta: '#d0a8e6',
    brightCyan: '#83d9e5',
    brightWhite: '#f4f7fa'
  }
})
const fitAddon = new FitAddon()
terminal.loadAddon(fitAddon)
terminal.open(terminalHost)
terminal.attachCustomKeyEventHandler((event) => {
  const isPasteShortcut = event.type === 'keydown'
    && event.ctrlKey
    && event.key.toLowerCase() === 'v'
  if (!isPasteShortcut) return true

  event.preventDefault()
  void pasteClipboardIntoTerminal()
  return false
})

let monitorTimer: number | undefined
let monitorProbeGeneration = 0
let monitorProbePending = false
const monitorHealth = new MonitorHealthGate(3)
let servicePollTimer: number | undefined
let serviceActionPending = false
let pluginActionPending: string | undefined
let activePluginName: string | undefined
let backendChangePending = false
let splitPercent = readSplitPercent()
let layoutMode = readLayoutMode()
let dragStartY = 0
let dragStartPercent = splitPercent

function requiredElement<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id)
  if (!element) throw new Error(`Missing required element: ${id}`)
  return element as T
}

function applyTerminalState(state: TerminalState): void {
  terminalError.hidden = true
  switch (state.phase) {
    case 'starting':
      terminalMeta.textContent = '正在创建 PTY'
      break
    case 'running':
      terminalMeta.textContent = state.pid ? `PID ${state.pid}` : '运行中'
      requestAnimationFrame(fitTerminal)
      if (layoutMode !== 'monitor') terminal.focus()
      break
    case 'exited':
      terminalMeta.textContent = `退出码 ${state.exitCode ?? '-'}`
      showTerminalError(state.message ?? 'CLI 进程已结束')
      break
    case 'error':
      terminalMeta.textContent = '进程不可用'
      showTerminalError(state.message ?? '未知启动错误')
      break
    case 'stopped':
      terminalMeta.textContent = '等待进程'
      break
  }
}

function showTerminalError(message: string): void {
  terminalErrorMessage.textContent = message
  terminalError.hidden = false
}

async function pasteClipboardIntoTerminal(): Promise<void> {
  const result: { ok: boolean; text?: string; error?: string } = await api.clipboardReadText().catch((error: unknown) => ({
    ok: false,
    error: error instanceof Error ? error.message : String(error)
  }))
  if (!result.ok) {
    showTerminalError(result.error || '无法读取系统剪贴板')
    return
  }
  if (result.text) terminal.paste(result.text)
}

function showMonitorWaiting(title: string, detail: string, error = false): void {
  monitorOverlay.hidden = false
  monitorOverlay.classList.remove('stale')
  monitorOverlay.classList.toggle('error', error)
  monitorOverlayTitle.textContent = title
  monitorOverlayDetail.textContent = detail
  retryMonitor.hidden = !error
}

function showMonitorStale(): void {
  monitorOverlay.hidden = false
  monitorOverlay.classList.remove('error')
  monitorOverlay.classList.add('stale')
  monitorOverlayTitle.textContent = '状态更新暂停'
  monitorOverlayDetail.textContent = 'Supervisor 正在处理后台任务，连接恢复后继续更新'
  retryMonitor.hidden = true
}

async function connectMonitor(forceReload = false): Promise<void> {
  if (monitorTimer !== undefined) {
    window.clearTimeout(monitorTimer)
    monitorTimer = undefined
  }
  showMonitorWaiting('正在连接 Supervisor', '本地监控服务就绪后将在这里显示')

  const result = await api.monitor.probe().catch((error: unknown) => ({
    ready: false,
    url: '',
    message: error instanceof Error ? error.message : String(error)
  }))
  if (result.ready) {
    monitorHealth.observe(true)
    if (forceReload || monitorFrame.getAttribute('src') !== result.url) {
      monitorFrame.src = result.url
    } else {
      monitorOverlay.hidden = true
    }
    return
  }

  monitorOverlayDetail.textContent = 'VoidCube 服务仍在启动，请稍候'
  monitorTimer = window.setTimeout(() => void connectMonitor(), 1500)
}

function showMonitorFailure(message: string): void {
  showMonitorWaiting('Supervisor 页面无法加载', message, true)
}

function closePluginView(): void {
  activePluginName = undefined
  pluginView.hidden = true
  pluginFrame.removeAttribute('src')
  pluginViewTitle.textContent = '插件'
  pluginOverlay.hidden = false
}

function showPluginView(name: string, title: string, url: string): void {
  activePluginName = name
  pluginViewTitle.textContent = title
  pluginOverlay.hidden = false
  pluginView.hidden = false
  pluginFrame.src = url
}

function invalidateMonitorProbe(): void {
  monitorProbeGeneration += 1
  monitorProbePending = false
}

function stopMonitorView(): void {
  invalidateMonitorProbe()
  monitorHealth.reset()
  if (monitorTimer !== undefined) {
    window.clearTimeout(monitorTimer)
    monitorTimer = undefined
  }
  monitorFrame.removeAttribute('src')
  showMonitorWaiting('Supervisor 已停止', 'Gateway → Memory → Supervisor')
}

async function verifyMonitorAvailability(): Promise<void> {
  if (monitorProbePending) return

  monitorProbePending = true
  const generation = ++monitorProbeGeneration
  const result = await api.monitor.probe().catch((error: unknown) => ({
    ready: false,
    url: '',
    message: error instanceof Error ? error.message : String(error)
  }))
  if (generation !== monitorProbeGeneration) return
  monitorProbePending = false

  if (monitorHealth.observe(result.ready) === 'keep') {
    if (result.ready) {
      if (!monitorFrame.getAttribute('src')) monitorFrame.src = result.url
      monitorOverlay.hidden = true
      monitorOverlay.classList.remove('stale')
    }
    return
  }

  if (monitorFrame.getAttribute('src')) {
    showMonitorStale()
    monitorTimer = window.setTimeout(() => void verifyMonitorAvailability(), 1500)
  } else {
    showMonitorWaiting('正在重新连接 Supervisor', '连续探测失败，正在等待服务恢复')
    monitorTimer = window.setTimeout(() => void connectMonitor(), 1500)
  }
}

function executionModeLabel(context: ExecutionContext): string {
  const backendNames: Record<string, string> = {
    local: localEnvironmentLabel(),
    docker: 'Docker',
    podman: 'Podman',
    singularity: 'Singularity',
    modal: 'Modal',
    daytona: 'Daytona',
    ssh: 'SSH'
  }
  const backend = backendNames[context.backend] ?? context.backend
  if (context.mode === 'sandbox') return `${backend} 沙箱`
  if (context.mode === 'remote') return `${backend} 远程`
  return '系统终端'
}

function localEnvironmentLabel(): string {
  if (api.runtime.platform === 'win32') return 'Windows 本地环境'
  if (api.runtime.platform === 'linux') return 'Linux 本地环境'
  return '宿主本地环境'
}

function bodyImprovementBackendLabel(backend?: string): string {
  if (backend === 'podman') return 'Podman / Linux'
  if (backend === 'docker') return 'Docker / Linux'
  return backend || '检测中'
}

function applyBackendSelection(backend?: string): void {
  localBackendLabel.textContent = localEnvironmentLabel()
  executionSelector.dataset.backend = backend ?? 'pending'
  for (const button of backendButtons) {
    const selected = button.dataset.terminalBackend === backend
    button.classList.toggle('selected', selected)
    button.setAttribute('aria-checked', String(selected))
  }
}

function setBackendSelectionBusy(busy: boolean): void {
  backendChangePending = busy
  executionSelector.classList.toggle('busy', busy)
  for (const button of backendButtons) button.disabled = busy
}

function showBackendSelectionStatus(message: string, error = false): void {
  executionSelectorStatus.hidden = !message
  executionSelectorStatus.textContent = message
  executionSelectorStatus.dataset.state = error ? 'error' : 'ok'
}

function applyExecutionContext(context?: ExecutionContext): void {
  if (!context) {
    executionContext.dataset.mode = 'pending'
    executionMode.textContent = '检测中'
    applyBackendSelection()
    executionWorkspace.textContent = '等待 CLI'
    bodyImprovementBackend.textContent = '检测中'
    executionContext.title = ''
    return
  }
  executionContext.dataset.mode = context.mode
  executionMode.textContent = executionModeLabel(context)
  applyBackendSelection(context.backend)
  executionWorkspace.textContent = context.branch
    ? `${context.workspaceName} · ${context.branch}`
    : context.workspaceName
  bodyImprovementBackend.textContent = bodyImprovementBackendLabel(
    context.bodyImprovementBackend
  )
  executionContext.title = [
    `执行方式：${executionModeLabel(context)}`,
    `替身验证：${bodyImprovementBackendLabel(context.bodyImprovementBackend)}`,
    `Agent 目录：${context.backendWorkingDirectory}`,
    `宿主工作区：${context.hostWorkingDirectory}`,
    context.worktree ? 'Git 隔离：Worktree' : 'Git 隔离：主工作区',
    `回退到系统终端：${context.fallbackToLocal ? '允许' : '禁止'}`
  ].join('\n')
  executionSelectorSummary.title = '点击切换执行环境'
}

function serviceLabel(service: ServiceInfo): string {
  if (service.state === 'healthy') return service.pid ? `PID ${service.pid}` : '正常'
  if (service.state === 'unhealthy') return '无响应'
  return '已停止'
}

function serviceDisplayName(service: ServiceInfo, plugins: PluginInfo[]): string {
  const plugin = plugins.find((item) => item.name === service.name)
  if (plugin) return plugin.displayName
  const labels: Record<string, string> = {
    gateway: 'Gateway',
    memory: 'Memory',
    supervisor: 'Supervisor'
  }
  return labels[service.name] ?? service.name
}

function renderServiceRows(services: ServiceInfo[], plugins: PluginInfo[]): void {
  serviceList.replaceChildren()
  if (services.length === 0) {
    const empty = document.createElement('p')
    empty.className = 'services-empty'
    empty.textContent = '暂无后台服务进程'
    serviceList.append(empty)
    return
  }

  for (const service of services) {
    const row = document.createElement('div')
    row.className = `service-row ${service.state}`
    row.dataset.service = service.name

    const stateDot = document.createElement('span')
    stateDot.className = 'state-dot'
    stateDot.setAttribute('aria-hidden', 'true')

    const name = document.createElement('span')
    name.textContent = serviceDisplayName(service, plugins)
    name.title = service.name

    const detail = document.createElement('small')
    detail.textContent = `${serviceLabel(service)} · ${service.port}`

    row.append(stateDot, name, detail)
    serviceList.append(row)
  }
}

function applyServiceResult(result: ServiceControlResult): void {
  servicesError.hidden = !result.error
  servicesError.textContent = result.error ?? ''
  applyPluginResult(result)

  renderServiceRows(result.services, result.plugins ?? [])
  const serviceByName = new Map(result.services.map((service) => [service.name, service]))

  if (result.error) {
    servicesSummary.textContent = '控制不可用'
    return
  }

  applyExecutionContext(result.executionContext)

  const healthyCount = result.services.filter((service) => service.state === 'healthy').length
  const stoppedCount = result.services.filter((service) => service.state === 'stopped').length
  const total = result.services.length
  servicesSummary.textContent = `${healthyCount}/${total} 正常`
  if (total > 0 && stoppedCount === total) servicesSummary.textContent = '已全部停止'

  const supervisor = serviceByName.get('supervisor')
  if (result.action === 'stop') {
    stopMonitorView()
  } else if (supervisor?.state === 'healthy') {
    invalidateMonitorProbe()
    monitorHealth.reset()
    if (!monitorFrame.getAttribute('src')) void connectMonitor()
  } else if (supervisor) {
    void verifyMonitorAvailability()
  }
}

function pluginStateLabel(plugin: PluginInfo): string {
  if (!plugin.enabled) return '已禁用'
  if (!plugin.service) return '工具插件'
  if (plugin.service.state === 'healthy') {
    return plugin.service.pid ? `PID ${plugin.service.pid}` : '运行中'
  }
  if (plugin.service.state === 'unhealthy') return '无响应'
  return '已停止'
}

function pluginStateClass(plugin: PluginInfo): string {
  if (!plugin.enabled) return 'disabled'
  return plugin.service?.state ?? 'available'
}

function pluginCapabilitiesLabel(plugin: PluginInfo): string {
  const labels: Record<string, string> = {
    tools: '工具',
    service: '服务',
    web: '界面',
    memory: '记忆'
  }
  return plugin.capabilities.map((capability) => labels[capability] ?? capability).join(' · ')
}

function pluginActionButton(
  label: string,
  icon: string,
  action: PluginControlAction,
  name: string
): HTMLButtonElement {
  const button = document.createElement('button')
  button.type = 'button'
  button.className = 'plugin-action'
  button.dataset.pluginAction = action
  button.dataset.pluginName = name
  button.title = `${label} ${name}`
  button.setAttribute('aria-label', `${label} ${name}`)
  const iconElement = document.createElement('i')
  iconElement.dataset.lucide = icon
  iconElement.setAttribute('aria-hidden', 'true')
  button.append(iconElement)
  return button
}

function renderPlugin(plugin: PluginInfo): HTMLElement {
  const row = document.createElement('article')
  row.className = `plugin-row ${pluginStateClass(plugin)}`
  row.dataset.plugin = plugin.name

  const stateDot = document.createElement('span')
  stateDot.className = 'state-dot'
  stateDot.setAttribute('aria-hidden', 'true')

  const copy = document.createElement('div')
  copy.className = 'plugin-copy'
  const heading = document.createElement('div')
  heading.className = 'plugin-heading'
  const name = document.createElement('strong')
  name.textContent = plugin.displayName
  const version = document.createElement('small')
  version.textContent = `v${plugin.version}`
  heading.append(name, version)
  const detail = document.createElement('span')
  detail.textContent = plugin.description || pluginCapabilitiesLabel(plugin)
  detail.title = plugin.description || pluginCapabilitiesLabel(plugin)
  copy.append(heading, detail)

  const meta = document.createElement('small')
  meta.className = 'plugin-meta'
  meta.textContent = plugin.service
    ? `${pluginStateLabel(plugin)} · ${plugin.service.port}`
    : pluginStateLabel(plugin)

  const actions = document.createElement('div')
  actions.className = 'plugin-actions'
  if (plugin.service && plugin.enabled) {
    if (plugin.service.state === 'healthy') {
      actions.append(
        pluginActionButton('重启', 'refresh-cw', 'restart', plugin.name),
        pluginActionButton('停止', 'square', 'stop', plugin.name)
      )
    } else {
      actions.append(pluginActionButton('启动', 'play', 'start', plugin.name))
    }
  }
  if (plugin.uiPath && plugin.enabled) {
    const openButton = document.createElement('button')
    openButton.type = 'button'
    openButton.className = 'plugin-action plugin-open'
    openButton.dataset.pluginOpen = plugin.name
    openButton.title = `打开 ${plugin.displayName}`
    openButton.setAttribute('aria-label', `打开 ${plugin.displayName}`)
    const openIcon = document.createElement('i')
    openIcon.dataset.lucide = 'external-link'
    openIcon.setAttribute('aria-hidden', 'true')
    openButton.append(openIcon)
    actions.append(openButton)
  }

  row.append(stateDot, copy, meta, actions)
  return row
}

function applyPluginResult(result: ServiceControlResult): void {
  const plugins = result.plugins ?? []
  pluginsError.hidden = !result.error
  pluginsError.textContent = result.error ?? ''
  pluginList.replaceChildren()
  pluginsEmpty.hidden = plugins.length > 0
  if (plugins.length === 0) {
    pluginList.append(pluginsEmpty)
  } else {
    for (const plugin of plugins) pluginList.append(renderPlugin(plugin))
  }
  const enabled = plugins.filter((plugin) => plugin.enabled)
  const available = enabled.filter(
    (plugin) => !plugin.service || plugin.service.state === 'healthy'
  )
  pluginsSummary.textContent = result.error
    ? '控制不可用'
    : `${available.length}/${enabled.length} 可用`
  createIcons({
    icons: {
      ExternalLink,
      Play,
      RefreshCw,
      Square
    }
  })
  if (activePluginName) {
    const activePlugin = plugins.find((plugin) => plugin.name === activePluginName)
    if (!activePlugin || (activePlugin.service && activePlugin.service.state !== 'healthy')) {
      closePluginView()
    }
  }
}

function setServiceBusy(action?: ServiceLifecycleAction): void {
  serviceActionPending = action !== undefined
  for (const button of serviceButtons) button.disabled = serviceActionPending
  serviceProcessMenu.classList.toggle('busy', serviceActionPending)
  if (!action) return
  const labels: Record<ServiceLifecycleAction, string> = {
    start: '服务启动中',
    restart: '服务重启中',
    stop: '服务停止中'
  }
  servicesSummary.textContent = labels[action]
}

function setPluginBusy(name?: string): void {
  pluginActionPending = name
  pluginMenu.classList.toggle('busy', pluginActionPending !== undefined)
  for (const button of pluginList.querySelectorAll<HTMLButtonElement>('.plugin-action')) {
    button.disabled = pluginActionPending !== undefined
  }
}

async function runPluginAction(name: string, action: PluginControlAction): Promise<void> {
  if (pluginActionPending) return
  setPluginBusy(name)
  try {
    applyServiceResult(await api.plugins.control(name, action))
  } catch (error) {
    pluginsError.hidden = false
    pluginsError.textContent = error instanceof Error ? error.message : String(error)
  } finally {
    setPluginBusy()
  }
}

async function supervisorOrigin(): Promise<string | undefined> {
  const current = monitorFrame.getAttribute('src')
  if (current) {
    try {
      return new URL(current).origin
    } catch {
      // Fall through to the authoritative monitor probe.
    }
  }
  const probe = await api.monitor.probe()
  if (!probe.ready || !probe.url) return undefined
  return new URL(probe.url).origin
}

async function openPlugin(name: string): Promise<void> {
  if (pluginActionPending) return
  let plugin: PluginInfo | undefined
  try {
    plugin = (await api.services.status()).plugins?.find((item) => item.name === name)
  } catch (error) {
    pluginsError.hidden = false
    pluginsError.textContent = error instanceof Error ? error.message : String(error)
    return
  }
  if (!plugin || !plugin.enabled || !plugin.uiPath) return
  if (plugin.service && plugin.service.state !== 'healthy') {
    setPluginBusy(name)
    try {
      const result = await api.plugins.control(name, 'start')
      applyServiceResult(result)
      const refreshed = result.plugins?.find((item) => item.name === name)
      if (!refreshed || (refreshed.service && refreshed.service.state !== 'healthy')) return
      plugin = refreshed
    } catch (error) {
      pluginsError.hidden = false
      pluginsError.textContent = error instanceof Error ? error.message : String(error)
      return
    } finally {
      setPluginBusy()
    }
  }
  const origin = await supervisorOrigin()
  if (!origin) {
    pluginsError.hidden = false
    pluginsError.textContent = 'Supervisor 页面尚未就绪'
    return
  }
  if (!plugin.uiPath) return
  if (layoutMode === 'terminal') setLayoutMode('split', true)
  showPluginView(name, plugin.displayName, new URL(plugin.uiPath, origin).toString())
  pluginMenu.open = false
}

async function runServiceAction(action: ServiceLifecycleAction): Promise<ServiceControlResult> {
  setServiceBusy(action)
  try {
    const result = await api.services.control(action)
    applyServiceResult(result)
    if (action !== 'stop' && result.services.some(
      (service) => service.name === 'supervisor' && service.state === 'healthy'
    )) {
      await connectMonitor(true)
    }
    return result
  } finally {
    setServiceBusy()
  }
}

async function changeTerminalBackend(backend: TerminalBackend): Promise<void> {
  if (backendChangePending || executionSelector.dataset.backend === backend) {
    executionSelector.open = false
    return
  }

  setBackendSelectionBusy(true)
  setServiceBusy('restart')
  showBackendSelectionStatus('正在切换执行环境…')
  try {
    const result = await api.services.setBackend(backend)
    if (!result.ok) {
      showBackendSelectionStatus(result.error ?? '执行环境切换失败', true)
      return
    }
    if (result.services) applyServiceResult(result.services)
    if (result.terminal) applyTerminalState(result.terminal)
    applyBackendSelection(result.backend)
    executionSelector.open = false
    showBackendSelectionStatus('执行环境已切换')
    window.setTimeout(() => {
      if (!executionSelector.open) showBackendSelectionStatus('')
    }, 2400)
  } catch (error) {
    showBackendSelectionStatus(error instanceof Error ? error.message : String(error), true)
  } finally {
    setBackendSelectionBusy(false)
    setServiceBusy()
  }
}

function scheduleServicePoll(): void {
  if (servicePollTimer !== undefined) window.clearTimeout(servicePollTimer)
  servicePollTimer = window.setTimeout(() => void refreshServiceStatus(), 5000)
}

async function refreshServiceStatus(): Promise<void> {
  if (serviceActionPending || document.hidden) {
    scheduleServicePoll()
    return
  }
  applyServiceResult(await api.services.status())
  scheduleServicePoll()
}

function fitTerminal(): void {
  if (layoutMode === 'monitor') return
  try {
    fitAddon.fit()
    api.terminal.resize(terminal.cols, terminal.rows)
  } catch {
    // Layout may be between resize frames; the observer will retry.
  }
}

function readSplitPercent(): number {
  localStorage.removeItem('voidcube.desktop.split')
  const stored = Number.parseFloat(localStorage.getItem(SPLIT_STORAGE_KEY) ?? '')
  return Number.isFinite(stored) ? Math.max(25, Math.min(75, stored)) : 54
}

function setSplitPercent(value: number, persist = false): void {
  splitPercent = Math.max(25, Math.min(75, value))
  workspace.style.setProperty('--monitor-size', `${splitPercent}%`)
  splitter.setAttribute('aria-valuenow', String(Math.round(splitPercent)))
  if (persist) localStorage.setItem(SPLIT_STORAGE_KEY, splitPercent.toFixed(2))
  requestAnimationFrame(fitTerminal)
}

function readLayoutMode(): LayoutMode {
  const stored = localStorage.getItem('voidcube.desktop.layout')
  return stored === 'monitor' || stored === 'terminal' ? stored : 'split'
}

function setLayoutMode(mode: LayoutMode, persist = false): void {
  layoutMode = mode
  workspace.dataset.layout = mode
  splitter.tabIndex = mode === 'split' ? 0 : -1
  for (const button of layoutButtons) {
    button.setAttribute('aria-pressed', String(button.dataset.layoutMode === mode))
  }
  if (persist) localStorage.setItem('voidcube.desktop.layout', mode)
  requestAnimationFrame(() => {
    fitTerminal()
    if (mode === 'terminal') terminal.focus()
  })
}

function beginSplitDrag(event: PointerEvent): void {
  if (layoutMode !== 'split') return
  dragStartY = event.clientY
  dragStartPercent = splitPercent
  splitter.setPointerCapture(event.pointerId)
  splitter.classList.add('dragging')
  document.body.classList.add('resizing')
}

function moveSplitDrag(event: PointerEvent): void {
  if (!splitter.hasPointerCapture(event.pointerId)) return
  const usableHeight = workspace.clientHeight
  setSplitPercent(dragStartPercent + ((event.clientY - dragStartY) / usableHeight) * 100)
}

function endSplitDrag(event: PointerEvent): void {
  if (!splitter.hasPointerCapture(event.pointerId)) return
  splitter.releasePointerCapture(event.pointerId)
  splitter.classList.remove('dragging')
  document.body.classList.remove('resizing')
  setSplitPercent(splitPercent, true)
}

terminal.onData((data) => api.terminal.write(data))
const disposeTerminalData = api.terminal.onData((data) => terminal.write(data))
const disposeTerminalState = api.terminal.onState(applyTerminalState)
const resizeObserver = new ResizeObserver(() => requestAnimationFrame(fitTerminal))
resizeObserver.observe(terminalHost)

monitorFrame.addEventListener('load', () => {
  if (!monitorFrame.getAttribute('src')) return
  monitorHealth.observe(true)
  monitorOverlay.hidden = true
})
monitorFrame.addEventListener('error', () => showMonitorFailure('请检查 Supervisor 服务日志后重试'))
pluginFrame.addEventListener('load', () => {
  pluginOverlay.hidden = true
})
requiredElement<HTMLButtonElement>('reload-monitor').addEventListener('click', () => void connectMonitor(true))
retryMonitor.addEventListener('click', () => void connectMonitor(true))
closePluginViewButton.addEventListener('click', closePluginView)
requiredElement<HTMLButtonElement>('restart-terminal').addEventListener('click', async () => applyTerminalState(await api.terminal.restart()))
requiredElement<HTMLButtonElement>('retry-terminal').addEventListener('click', async () => applyTerminalState(await api.terminal.start()))
requiredElement<HTMLButtonElement>('minimize-window').addEventListener('click', () => api.window.minimize())
requiredElement<HTMLButtonElement>('close-window').addEventListener('click', () => api.window.close())
requiredElement<HTMLButtonElement>('start-services').addEventListener('click', () => void runServiceAction('start'))
requiredElement<HTMLButtonElement>('restart-services').addEventListener('click', () => void runServiceAction('restart'))
requiredElement<HTMLButtonElement>('stop-services').addEventListener('click', () => void runServiceAction('stop'))
pluginList.addEventListener('click', (event) => {
  const target = event.target as HTMLElement
  const actionButton = target.closest<HTMLButtonElement>('[data-plugin-action]')
  if (actionButton?.dataset.pluginName && actionButton.dataset.pluginAction) {
    const action = actionButton.dataset.pluginAction
    if (action === 'start' || action === 'stop' || action === 'restart') {
      void runPluginAction(actionButton.dataset.pluginName, action)
    }
    return
  }
  const openButton = target.closest<HTMLButtonElement>('[data-plugin-open]')
  if (openButton?.dataset.pluginOpen) void openPlugin(openButton.dataset.pluginOpen)
})
for (const button of backendButtons) {
  button.addEventListener('click', () => {
    const backend = button.dataset.terminalBackend
    if (backend === 'local' || backend === 'podman') void changeTerminalBackend(backend)
  })
}
requiredElement<HTMLButtonElement>('open-workspace').addEventListener('click', async () => {
  const result = await api.workspace.open()
  if (result.ok) return
  executionContext.classList.add('context-error')
  executionContext.title = result.message ?? '无法打开工作区'
  window.setTimeout(() => executionContext.classList.remove('context-error'), 1800)
})
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) void refreshServiceStatus()
})

for (const button of layoutButtons) {
  button.addEventListener('click', () => {
    const mode = button.dataset.layoutMode
    if (mode === 'split' || mode === 'monitor' || mode === 'terminal') {
      setLayoutMode(mode, true)
    }
  })
}

splitter.addEventListener('pointerdown', beginSplitDrag)
splitter.addEventListener('pointermove', moveSplitDrag)
splitter.addEventListener('pointerup', endSplitDrag)
splitter.addEventListener('pointercancel', endSplitDrag)
splitter.addEventListener('keydown', (event) => {
  if (layoutMode !== 'split' || (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')) return
  event.preventDefault()
  setSplitPercent(splitPercent + (event.key === 'ArrowDown' ? 2 : -2), true)
})

window.addEventListener('beforeunload', () => {
  if (monitorTimer !== undefined) window.clearTimeout(monitorTimer)
  if (servicePollTimer !== undefined) window.clearTimeout(servicePollTimer)
  resizeObserver.disconnect()
  disposeTerminalData()
  disposeTerminalState()
})

// 监听 Supervisor iframe 的 postMessage（账号中心 cookie 刷新等）
window.addEventListener('message', (event) => {
  const data = event.data as Record<string, unknown> | undefined
  if (!data || typeof data.type !== 'string') return
  if (data.type === 'cookies:refresh') {
    api.cookiesRefresh?.().catch(() => {})
    return
  }
  if (data.type === 'accounts:platform-login') {
    const source = event.source as WindowProxy | null
    if (!source || source !== monitorFrame.contentWindow) return
    const platform = typeof data.platform === 'string' ? data.platform : ''
    source.postMessage({ type: 'accounts:platform-login-state', state: 'started' }, '*')
    api.platformLogin(platform)
      .then((result) => source.postMessage({ type: 'accounts:platform-login-result', ...result }, '*'))
      .catch((error: unknown) => source.postMessage({
        type: 'accounts:platform-login-result',
        ok: false,
        error: error instanceof Error ? error.message : String(error)
      }, '*'))
  }
})

async function startDesktop(): Promise<void> {
  setLayoutMode(layoutMode)
  setSplitPercent(splitPercent)
  await runServiceAction('start')
  applyTerminalState(await api.terminal.start())
  scheduleServicePoll()
}

void startDesktop()
