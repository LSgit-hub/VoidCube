"""复现 grounding 矛盾信号的关键记忆构建器输出（只读，不修改任何数据）"""
import json, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, 'src')

from voidcube.systems.supervisor.endogenous_cognitive_memory import (
    build_cognitive_assessment_memory,
    build_self_iteration_trend_memory,
    build_switch_self_regulation_memory,
    build_post_task_effect_memory,
)
from voidcube.systems.supervisor.endogenous_meta_cognition import build_proposal_drift_memory
from voidcube.systems.supervisor.endogenous_self_model import build_recent_reference_alignment

p = r'C:/Users/lishuo/.VoidCube/runtime/supervisor/endogenous_drive_history.json'
d = json.load(open(p, encoding='utf-8'))
ctx = {'drive_history': d}

print('=== drive_history keys ===')
print(list(d.keys()))
print('updated_at:', d.get('updated_at'))
print('judgements:', len(d.get('judgements', [])))
print('outcomes:', len(d.get('outcomes', [])))
print('strategy_memory keys:', list(d.get('strategy_memory', {}).keys()))

for fn in (build_post_task_effect_memory, build_proposal_drift_memory,
           build_recent_reference_alignment, build_self_iteration_trend_memory,
           build_switch_self_regulation_memory, build_cognitive_assessment_memory):
    print('\n=== %s ===' % fn.__name__)
    try:
        out = fn(ctx)
        print(json.dumps(out, ensure_ascii=False, indent=1))
    except Exception as e:
        print('ERROR:', type(e).__name__, e)
