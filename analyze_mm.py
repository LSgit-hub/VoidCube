"""分析 memory_maintenance 任务的 decision_history 完整结构"""
import json
from collections import Counter

path = r'f:\My_code\Traecode\VoidCube\.soul-runtime\self_evolution_queue.json'
with open(path, 'r', encoding='utf-8') as f:
    data = json.load(f)

tasks = data['tasks']

# 找 5 个 memory_maintenance 任务，看完整 decision_history
mm_tasks = [t for t in tasks if (t.get('metadata') or {}).get('endogenous_drive_key') == 'continuity:memory_maintenance_sweep']
print(f'memory_maintenance 任务总数: {len(mm_tasks)}')

# 收集所有 reason 模式
reasons = Counter()
for t in mm_tasks:
    for d in t.get('decision_history', []):
        reasons[d.get('reason', '')] += 1

print()
print('=== decision_history reason 频次 ===')
for r, c in reasons.most_common(15):
    print(f'  [{c}] {r[:140]}')

# 看一个完整任务
print()
print('=== 第一个 memory_maintenance 任务的 decision_history 完整 ===')
sample = mm_tasks[0]
print(f'task_id: {sample["task_id"]}')
print(f'status: {sample["status"]}')
print(f'decision_reason: {sample.get("decision_reason")}')
for i, d in enumerate(sample.get('decision_history', [])):
    print(f'  [{i}] status={d.get("status")} actor={d.get("actor")} decided_at={d.get("decided_at")}')
    print(f'       reason: {d.get("reason")[:200]}')
    print(f'       context.idle_window.evaluated_at: {(d.get("context") or {}).get("idle_window", {}).get("evaluated_at")}')
