# MemAI

MemAI 是 VoidCube 的长期记忆实现，源码位于 `Mem/src/memai/`，安装包中的规范包名为 `memai`。它负责记忆领域对象、SQLite schema、迁移、召回、压缩、备份、时间摘要和可选 HTTP transport。

## 所有权

MemAI 独占 `VOIDCUBE_HOME/runtime/memory/memory.db`。VoidCube 通过 `plugins/memory/mem/` 适配器注册和调用 MemAI，不在 Agent、Supervisor 或 Gateway 中复制 repository、schema 或召回逻辑。当前上下文和长期记忆的边界见 [docs/mem-temporary-memory-contract.md](../docs/mem-temporary-memory-contract.md)，资源分类见 [docs/memory-resource-contract.md](../docs/memory-resource-contract.md)。

记忆按 `owner_id`、`workspace_id`、`memory_domain` 和 actor 能力隔离。API-A、Companion 和 Gateway 使用独立 outbox；outbox 是可靠传输队列，不是第二份长期记忆。只有 MemAI 返回 committed 才代表写入 `memory.db`。

## 目录

```text
Mem/src/memai/domain/       记忆对象、作用域和生命周期
Mem/src/memai/application/  会话封口、召回、维护和聚合用例
Mem/src/memai/repository/   repository、SQLite、迁移和备份
Mem/src/memai/indexes/      词法、语义、实体和时间索引
Mem/src/memai/transport/    可选 HTTP 适配器
Mem/tests/                  MemAI 行为测试
Mem/docs/                   领域规则和资源说明
```

## 开发与验证

```powershell
.venv\Scripts\python.exe -m pip install -e ".[all,dev]"
.venv\Scripts\python.exe -m pytest Mem/tests -q
.venv\Scripts\python.exe scripts/run_ci_tests.py
```

修改 `Mem/src/` 后必须运行根项目全量门禁。MemAI 的数据文件不要放入仓库；需要备份时先停止写入服务，由 owner 执行 SQLite backup/integrity check。

## 当前实现范围

已实现的主路径包括结构化记忆对象、时间规范化、事件/场景构建、持久化、增量更新、受预算约束的 recall、压缩质量审计、Profile 和 Session/Day 摘要。周/月摘要和反向展开等能力以代码与测试为准，不把设计稿当作实现承诺。
