# 运行与恢复

## 首次启动

使用 Python 3.14 虚拟环境安装开发版本：

```powershell
.venv\Scripts\python.exe -m pip install -e ".[all,dev]"
.venv\Scripts\python.exe -m voidcube doctor
.venv\Scripts\python.exe -m voidcube
```

用户配置在 `VOIDCUBE_HOME/config.yaml`，默认 `~/.VoidCube/config.yaml`。凭据放在 `VOIDCUBE_HOME/.env` 或凭据存储，不要写入仓库配置。

## 服务生命周期

```powershell
.venv\Scripts\python.exe -m voidcube serve start
.venv\Scripts\python.exe -m voidcube serve status
.venv\Scripts\python.exe -m voidcube serve stop
```

状态异常时先看 `serve status` 和 `VOIDCUBE_HOME/logs/`，再运行 `doctor`。桌面端关闭窗口不会自动停止后台服务；使用服务菜单或 `serve stop` 停止。

## 数据和备份

长期记忆位于 `VOIDCUBE_HOME/runtime/memory/memory.db`，备份和导出由 MemAI owner 执行。不要手工复制正在写入的 SQLite 文件，也不要删除 outbox 作为“清理”手段。停止服务后再进行文件级备份，并保留 `backups/` 的校验结果。

## 日常模式和 Auto

日常模式使用 `daily_companion`。`/auto` 开启 `auto_evolution`，后台任务须经过 API-B 复核和治理后才会派给员工代理；`/auto-q` 收口并回到日常模式。出现员工任务积压时先查看 Supervisor 状态和任务日志，不要直接修改数据库状态。
