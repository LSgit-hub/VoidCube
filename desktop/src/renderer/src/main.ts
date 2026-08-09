import { createIcons, RefreshCw, RotateCcw } from 'lucide'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import './style.css'
import type { TerminalState } from '../../shared/contracts'

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
const monitorState = requiredElement<HTMLElement>('monitor-state')
const terminalState = requiredElement<HTMLElement>('terminal-state')
const workspace = requiredElement<HTMLElement>('workspace')
const splitter = requiredElement<HTMLElement>('splitter')

createIcons({ icons: { RefreshCw, RotateCcw } })

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
let splitPercent = readSplitPercent()
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
      terminal.focus()
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

async function connectMonitor(forceReload = false): Promise<void> {
  if (monitorTimer !== undefined) window.clearTimeout(monitorTimer)
  setRuntimeState(monitorState, '监控连接中', 'pending')
  retryMonitor.hidden = true
  monitorOverlay.hidden = false
  monitorOverlay.classList.remove('error')
  monitorOverlayTitle.textContent = '正在启动 Supervisor'
  monitorOverlayDetail.textContent = '本地监控服务就绪后将在这里显示'

  const result = await api.monitor.probe()
  if (result.ready) {
    setRuntimeState(monitorState, '正在载入监控', 'pending')
    if (forceReload || monitorFrame.src !== result.url) monitorFrame.src = result.url
    return
  }

  monitorOverlayDetail.textContent = 'VoidCube 服务仍在启动，请稍候'
  monitorTimer = window.setTimeout(() => void connectMonitor(), 1500)
}

function showMonitorFailure(message: string): void {
  setRuntimeState(monitorState, '监控不可用', 'bad')
  monitorOverlay.hidden = false
  monitorOverlay.classList.add('error')
  monitorOverlayTitle.textContent = 'Supervisor 页面无法加载'
  monitorOverlayDetail.textContent = message
  retryMonitor.hidden = false
}

function fitTerminal(): void {
  try {
    fitAddon.fit()
    api.terminal.resize(terminal.cols, terminal.rows)
  } catch {
    // Layout may be between resize frames; the observer will retry.
  }
}

function readSplitPercent(): number {
  const stored = Number.parseFloat(localStorage.getItem('voidcube.desktop.split') ?? '')
  return Number.isFinite(stored) ? Math.max(25, Math.min(75, stored)) : 58
}

function setSplitPercent(value: number, persist = false): void {
  splitPercent = Math.max(25, Math.min(75, value))
  workspace.style.setProperty('--monitor-size', `${splitPercent}%`)
  splitter.setAttribute('aria-valuenow', String(Math.round(splitPercent)))
  if (persist) localStorage.setItem('voidcube.desktop.split', splitPercent.toFixed(2))
  requestAnimationFrame(fitTerminal)
}

function beginSplitDrag(event: PointerEvent): void {
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
  monitorOverlay.hidden = true
  setRuntimeState(monitorState, '监控已连接', 'good')
})
monitorFrame.addEventListener('error', () => showMonitorFailure('请检查 Supervisor 服务日志后重试'))
requiredElement<HTMLButtonElement>('reload-monitor').addEventListener('click', () => void connectMonitor(true))
retryMonitor.addEventListener('click', () => void connectMonitor(true))
requiredElement<HTMLButtonElement>('restart-terminal').addEventListener('click', async () => applyTerminalState(await api.terminal.restart()))
requiredElement<HTMLButtonElement>('retry-terminal').addEventListener('click', async () => applyTerminalState(await api.terminal.start()))

splitter.addEventListener('pointerdown', beginSplitDrag)
splitter.addEventListener('pointermove', moveSplitDrag)
splitter.addEventListener('pointerup', endSplitDrag)
splitter.addEventListener('pointercancel', endSplitDrag)
splitter.addEventListener('keydown', (event) => {
  if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
  event.preventDefault()
  setSplitPercent(splitPercent + (event.key === 'ArrowDown' ? 2 : -2), true)
})

window.addEventListener('beforeunload', () => {
  if (monitorTimer !== undefined) window.clearTimeout(monitorTimer)
  resizeObserver.disconnect()
  disposeTerminalData()
  disposeTerminalState()
})

setSplitPercent(splitPercent)
void connectMonitor()
void api.terminal.start().then(applyTerminalState)
