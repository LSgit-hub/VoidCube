---
name: memory-maintenance-sweep
description: 系统化执行内存维护扫掠，检查 Tier1/Tier2 状态、内生驱动历史、认知对齐度，并生成维护报告。当收到 memory_maintenance 类型任务、或需定期审计记忆健康时执行。
---

# Memory Maintenance Sweep

## 触发条件
- 收到 `task_type: memory_maintenance` 的 API-B 派发任务
- 需要定期检查记忆系统健康状态
- 怀疑记忆写入/召回链路有异常

## 核心文件位置
- 记忆数据库: `C:/Users/lishuo/.VoidCube/runtime/memory/memory.db`
- 内生驱动历史: `C:/Users/lishuo/.VoidCube/runtime/supervisor/endogenous_drive_history.json`
- 维护报告目录: `C:/Users/lishuo/.VoidCube/runtime/supervisor/self-learning/conclusions/`

## 执行步骤

### 1. 检查记忆系统状态
```bash
# 数据库完整性检查
python -c "
import sqlite3, json
conn = sqlite3.connect(r'C:/Users/lishuo/.VoidCube/runtime/memory/memory.db')
# 核心表行数
for tbl in ['turns', 'sessions', 'compressed_memories', 'profile_memories', 'entity_nodes']:
    try:
        cnt = conn.execute(f'SELECT COUNT(*) FROM [{tbl}]').fetchone()[0]
        print(f'{tbl}: {cnt} 行')
    except Exception as e:
        print(f'{tbl}: ERROR ({e})')
# 最新 turn 时间戳
latest = conn.execute('SELECT MAX(timestamp) FROM turns').fetchone()[0]
print(f'Latest turn: {latest}')
# 压缩状态分布
status = conn.execute('SELECT compression_status, COUNT(*) FROM turns GROUP BY 1').fetchall()
print('Compression status:', dict(status))
"
```

### 2. 读取内生驱动历史
```python
import json
with open(r'C:/Users/lishuo/.VoidCube/runtime/supervisor/endogenous_drive_history.json', 'r') as f:
    d = json.load(f)
# 关键统计
print('Total outcomes:', len(d.get('outcomes', [])))
print('Strategy memory keys:', list(d.get('strategy_memory', {}).keys()))
print('Recent focus_stats:', d.get('strategy_memory', {}).get('focus_stats', {}))
```

### 3. 检查影子任务
shadow tasks 是待激活的基础任务：
- `fill_self_cognition` - 填充自我认知缺口
- `fill_research_knowledge` - 填充研究知识库
- `run_evolution_evaluation` - 运行进化评估

当 reference_alignment < 0.5 时，这些任务处于 shadow 状态。

### 4. 生成维护报告
写入报告到 `self-learning/conclusions/memory-maintenance-<date>.md`。

### 5. 回写结论到 Mem

**重要**: 维护任务结果必须回写 Mem，**不能**使用 `media_display`（会返回 `autonomous_employee_delivery_forbidden`）。

正确方法：直接 POST 到记忆服务 HTTP API。

```python
import requests
import uuid

BASE = "http://127.0.0.1:6001"

payload = {
    "session_id": f"maintenance-{__import__('datetime').datetime.now().strftime('%Y%m%d')}-{task_id}",
    "write_id": str(uuid.uuid4()),
    "user_content": f"[memory_maintenance] Task {task_id} completed.\n\nFindings:\n- Database: operational\n- Tier1 turns: active\n- Tier2 compressed: active\n- Cognitive alignment: 0.57\n- Reference alignment: 0.32\n- Primary gap: missing_evidence:deliberation_state\n\nShadow tasks dormant: fill_self_cognition, fill_research_knowledge, run_evolution_evaluation",
    "assistant_content": "Memory maintenance sweep completed successfully.",
    "memory_actor": "agent_b",  # 注意：不是 agent-b，用下划线
    "memory_domain": "agent_interaction",
    "workspace_id": "VoidCube"  # 有效值: default, VoidCube, system
}

resp = requests.post(f"{BASE}/turn-pairs", json=payload, timeout=10)
resp.raise_for_status()
data = resp.json()

# 验证写入成功
assert data['write_status'] == 'committed'
print(f"Committed revision: {data['commit_revision']}")
```

### 6. 验证落库
```python
import sqlite3
conn = sqlite3.connect(r'C:/Users/lishuo/.VoidCube/runtime/memory/memory.db')
rows = conn.execute("""
    SELECT speaker, substr(text,1,100), timestamp 
    FROM turns 
    WHERE session_id LIKE '%maintenance-%'
    ORDER BY timestamp DESC
""").fetchall()
for r in rows:
    print(f'{r[0]}: {r[1]}... @ {r[2]}')
```

## 常见问题与修复

### 错误 1: workspace_id 无效
```
HTTP 422: workspace_id must be one of: default, VoidCube, system
```
修复：使用 `VoidCube` 或 `default`。

### 错误 2: memory_actor 无效
```
HTTP 422: memory_actor must be one of: agent_a, agent_b, api_a, user, ...
```
修复：使用 `agent_b`（注意下划线，不是连字符）。

### 错误 3: media_display 被拒绝
```
autonomous_employee_delivery_forbidden
```
修复：维护任务结果必须通过 `/turn-pairs` API 回写，不能走展板交付。

## 验证标准
- [ ] 数据库核心表有数据（turns/compressed_memories/profile_memories 均 > 0）
- [ ] 内生驱动历史文件可读（endogenous_drive_history.json）
- [ ] 维护报告已写入 conclusions/ 目录
- [ ] Mem 回写成功（write_status=committed）
- [ ] 已验证落库（turns 表有对应记录）

## 注意事项
- 本技能为只读诊断，不修改系统配置或代码
- 维护任务由星子（AI-自主）执行，不是 Auto 员工链
- 实际认知对齐数据来自任务派发 evidence，非实时复现（除非任务要求）