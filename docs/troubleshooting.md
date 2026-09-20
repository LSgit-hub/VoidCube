# 故障排查

## CLI 无法启动

先确认 Python 版本和安装状态：

```powershell
.venv\Scripts\python.exe --version
.venv\Scripts\python.exe -m pip check
.venv\Scripts\python.exe -m voidcube doctor
```

再确认 `VOIDCUBE_HOME` 指向预期目录，且 `config.yaml` 和 `.env` 可读。

## 服务不健康

运行 `voidcube serve status`，查看 `VOIDCUBE_HOME/logs/` 中对应服务的日志。Gateway 注册成功不等于 Memory 数据面就绪；分别确认 Gateway、Memory、Supervisor 的 ready/health。修复配置后使用 `serve restart` 或先 stop 再 start。

## 无法召回或保存记忆

检查 `runtime/memory/memory.db`、Mem 服务状态和 outbox 状态。当前对话可以在 Mem 暂时不可用时继续，但不能把 `accepted` 或 `durable outbox` 当作已提交；只有服务返回 committed 才代表写入 `memory.db`。不要绕过 MemoryClient 直接打开数据库。

## Provider 或 API-B 失败

通过 `/api` 检查 Provider、模型、base URL 和凭据来源。API-B 使用独立模型引用；API-A 可用不代表 API-B 已配置。确认 endpoint 是有效的 `http(s)` 地址，并检查服务日志中的超时、限流和认证错误。

## Auto 或员工任务卡住

确认当前模式、Supervisor 状态和任务 lease。获准任务由员工代理执行，API-B 不直接 claim 或执行任务。先尝试 `/auto-q` 收口，再重启相关服务；不要编辑 `scheduled_tasks.db` 或 `actions.db`。

## 回归定位

先运行文档、架构、集成和打包快速检查，再运行完整门禁。若只在桌面出现，单独运行 `desktop` 的 typecheck、unit 和 e2e；若只在 Mem 出现，运行 `Mem/tests` 的相关测试后仍要回到全量门禁。
