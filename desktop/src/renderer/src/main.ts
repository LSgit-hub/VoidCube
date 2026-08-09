import {
  createIcons,
  Minus,
  PanelTop,
  Play,
  RefreshCw,
  RotateCcw,
  Rows3,
  ServerCog,
  Square,
  SquareTerminal,
  X
} from 'lucide'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import './style.css'
import type {
  ServiceControlResult,
  ServiceInfo,
  ServiceLifecycleAction,
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
const terminalError = requiredElement<HTMLDivElement>('terminal-error')
const terminalErrorMessage = requiredElement<HTMLElement>('terminal-error-message')
const terminalMeta = requiredElement<HTMLElement>('terminal-meta')
const servicesState = requiredElement<HTMLElement>('services-state')
const monitorState = requiredElement<HTMLElement>('monitor-state')
const terminalState = requiredElement<HTMLElement>('terminal-state')
const servicesSummary = requiredElement<HTMLElement>('services-summary')
const servicesError = requiredElement<HTMLParagraphElement>('services-error')
const serviceMenu = requiredElement<HTMLDetailsElement>('service-menu')
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
    PanelTop,
    Play,
    RefreshCw,
    RotateCcw,
    Rows3,
    ServerCog,
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
  lineHeight: 1.12,
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

let monitorTimer: number | undefined
let servicePollTimer: number | undefined
let serviceActionPending = false
let splitPercent = readSplitPercent()
let layoutMode = readLayoutMode()
let dragStartY = 0
let dragStartPercent = splitPercent

function requiredElement<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id)
  if (!element) throw new Error(`Missing required element: ${id}`)
  return element as T
}

function setRuntimeState(element: HTMLElement, label: string, phase: 'pending' | 'good' | 'bad'): void {
  element.className = `runtime-state ${phase}`
  const labelNode = element.querySelector('span:last-child')
  if (labelNode) labelNode.textContent = label
}

function applyTerminalState(state: TerminalState): void {
  terminalError.hidden = true
  switch (state.phase) {
    case 'starting':
      setRuntimeState(terminalState, 'CLI 启动中', 'pending')
      terminalMeta.textContent = '正在创建 PTY'
      break
    case 'running':
      setRuntimeState(terminalState, 'CLI 已连接', 'good')
      terminalMeta.textContent = state.pid ? `PID ${state.pid}` : '运行中'
      requestAnimationFrame(fitTerminal)
      if (layoutMode !== 'monitor') terminal.focus()
      break
    case 'exited':
      setRuntimeState(terminalState, 'CLI 已退出', 'bad')
      terminalMeta.textContent = `退出码 ${state.exitCode ?? '-'}`
      showTerminalError(state.message ?? 'CLI 进程已结束')
      break
    case 'error':
      setRuntimeState(terminalState, 'CLI 启动失败', 'bad')
      terminalMeta.textContent = '进程不可用'
      showTerminalError(state.message ?? '未知启动错误')
      break
    case 'stopped':
      setRuntimeState(terminalState, 'CLI 已停止', 'pending')
      terminalMeta.textContent = '等待进程'
      break
  }
}

function showTerminalError(message: string): void {
  terminalErrorMessage.textContent = message
  terminalError.hidden = false
}

function showMonitorWaiting(title: string, detail: string, error = false): void {
  monitorOverlay.hidden = false
  monitorOverlay.classList.toggle('error', error)
  monitorOverlayTitle.textContent = title
  monitorOverlayDetail.textContent = detail
  retryMonitor.hidden = !error
}

async function connectMonitor(forceReload = false): Promise<void> {
  if (monitorTimer !== undefined) window.clearTimeout(monitorTimer)
  setRuntimeState(monitorState, '监控连接中', 'pending')
  showMonitorWaiting('正在连接 Supervisor', '本地监控服务就绪后将在这里显示')

  const result = await api.monitor.probe()
  if (result.ready) {
    setRuntimeState(monitorState, '正在载入监控', 'pending')
    if (forceReload || monitorFrame.getAttribute('src') !== result.url) {
      monitorFrame.src = result.url
    }
    return
  }

  monitorOverlayDetail.textContent = 'VoidCube 服务仍在启动，请稍候'
  monitorTimer = window.setTimeout(() => void connectMonitor(), 1500)
}

function showMonitorFailure(message: string): void {
  setRuntimeState(monitorState, '监控不可用', 'bad')
  showMonitorWaiting('Supervisor 页面无法加载', message, true)
}

function serviceLabel(service: ServiceInfo): string {
  if (service.state === 'healthy') return service.pid ? `PID ${service.pid}` : '正常'
  if (service.state === 'unhealthy') return '无响应'
  return '已停止'
}

function applyServiceResult(result: ServiceControlResult): void {
  servicesError.hidden = !result.error
  servicesError.textContent = result.error ?? ''

  const serviceByName = new Map(result.services.map((service) => [service.name, service]))
  for (const row of document.querySelectorAll<HTMLElement>('.service-row')) {
    const name = row.dataset.service
    const service = name ? serviceByName.get(name) : undefined
    row.className = `service-row ${service?.state ?? 'unknown'}`
    const detail = row.querySelector('small')
    if (detail && service) detail.textContent = serviceLabel(service)
  }

  if (result.error) {
    setRuntimeState(servicesState, '服务控制失败', 'bad')
    servicesSummary.textContent = '控制不可用'
    return
  }

  const healthyCount = result.services.filter((service) => service.state === 'healthy').length
  const stoppedCount = result.services.filter((service) => service.state === 'stopped').length
  const total = result.services.length
  servicesSummary.textContent = `${healthyCount}/${total} 正常`
  if (total > 0 && healthyCount === total) {
    setRuntimeState(servicesState, '服务正常', 'good')
  } else if (total > 0 && stoppedCount === total) {
    setRuntimeState(servicesState, '服务已停止', 'pending')
  } else {
    setRuntimeState(servicesState, `服务 ${healthyCount}/${total}`, 'bad')
  }

  const supervisor = serviceByName.get('supervisor')
  if (supervisor?.state === 'healthy') {
    if (!monitorFrame.getAttribute('src')) void connectMonitor()
  } else if (supervisor) {
    if (monitorTimer !== undefined) window.clearTimeout(monitorTimer)
    monitorFrame.removeAttribute('src')
    setRuntimeState(monitorState, '等待 Supervisor', 'pending')
    showMonitorWaiting(
      result.action === 'stop' ? 'Supervisor 已停止' : '正在启动 Supervisor',
      'Gateway → Memory → Supervisor'
    )
  }
}

function setServiceBusy(action?: ServiceLifecycleAction): void {
  serviceActionPending = action !== undefined
  for (const button of serviceButtons) button.disabled = serviceActionPending
  serviceMenu.classList.toggle('busy', serviceActionPending)
  if (!action) return
  const labels: Record<ServiceLifecycleAction, string> = {
    start: '服务启动中',
    restart: '服务重启中',
    stop: '服务停止中'
  }
  setRuntimeState(servicesState, labels[action], 'pending')
  servicesSummary.textContent = labels[action]
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
  monitorOverlay.hidden = true
  setRuntimeState(monitorState, '监控已连接', 'good')
})
monitorFrame.addEventListener('error', () => showMonitorFailure('请检查 Supervisor 服务日志后重试'))
requiredElement<HTMLButtonElement>('reload-monitor').addEventListener('click', () => void connectMonitor(true))
retryMonitor.addEventListener('click', () => void connectMonitor(true))
requiredElement<HTMLButtonElement>('restart-terminal').addEventListener('click', async () => applyTerminalState(await api.terminal.restart()))
requiredElement<HTMLButtonElement>('retry-terminal').addEventListener('click', async () => applyTerminalState(await api.terminal.start()))
requiredElement<HTMLButtonElement>('minimize-window').addEventListener('click', () => api.window.minimize())
requiredElement<HTMLButtonElement>('close-window').addEventListener('click', () => api.window.close())
requiredElement<HTMLButtonElement>('start-services').addEventListener('click', () => void runServiceAction('start'))
requiredElement<HTMLButtonElement>('restart-services').addEventListener('click', () => void runServiceAction('restart'))
requiredElement<HTMLButtonElement>('stop-services').addEventListener('click', () => void runServiceAction('stop'))
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

async function startDesktop(): Promise<void> {
  setLayoutMode(layoutMode)
  setSplitPercent(splitPercent)
  await runServiceAction('start')
  applyTerminalState(await api.terminal.start())
  scheduleServicePoll()
}

void startDesktop()
