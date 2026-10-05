import type { ServiceInfo } from '../../shared/contracts'

export function serviceLabel(service: ServiceInfo): string {
  if (service.restartBlocked === 'port_release_timeout') return '端口未释放，重启已阻止'
  if (service.restartBlocked) return `重启已阻止：${service.restartBlocked}`
  if (service.state === 'healthy') {
    const local = service.pid ? `PID ${service.pid}` : '正常'
    if (service.registered === false) return `${local} · 未注册`
    if (service.controlPlaneHealthy === false) return `${local} · 控制面不可用`
    return local
  }
  if (service.state === 'unhealthy') return '无响应'
  return '已停止'
}
