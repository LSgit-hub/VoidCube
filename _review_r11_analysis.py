# -*- coding: utf-8 -*-
"""第11轮复核: pte degrading x drift correcting/drifting 统一基线分析 (只读)"""
import json, sys, collections
sys.path.insert(0, 'src')

p = r'C:/Users/lishuo/.VoidCube/runtime/supervisor/endogenous_drive_history.json'
d = json.load(open(p, encoding='utf-8'))
outcomes = d.get('outcomes', [])
print('total outcomes:', len(outcomes))
print('outcomes order check (first 8 recorded_at / event_type / task_id):')
for o in outcomes[:8]:
    print('  ', o.get('recorded_at'), '|', o.get('event_type'), '|', o.get('task_id'))

def nested(o, key):
    md = o.get('metadata') or {}
    ev = o.get('evidence') or {}
    v = o.get(key)
    if not isinstance(v, dict):
        v = md.get(key)
    if not isinstance(v, dict):
        v = ev.get(key)
    return v if isinstance(v, dict) else {}

def norm_assessment(o):
    md = o.get('metadata') or {}
    ev = o.get('evidence') or {}
    a = o.get('llm_cognitive_assessment')
    if not isinstance(a, dict):
        a = md.get('llm_cognitive_assessment')
    if not isinstance(a, dict):
        a = ev.get('llm_cognitive_assessment')
    return a if isinstance(a, dict) else {}

def al_score(o):
    ca = nested(o, 'cognitive_alignment')
    return float(ca.get('score') or 0.0)

def ref_score(o):
    ra = nested(o, 'reference_alignment')
    return float(ra.get('alignment_score') or 0.0)

def ra_dict(o):
    return nested(o, 'reference_alignment')

# ---------- pte window ----------
print('\n=== PTE 窗口 (outcomes[:16] skip planned/empty/allzero, max 6) ===')
pte = []
for o in outcomes[:16]:
    et = str(o.get('event_type') or '').strip().lower()
    if et in ('', 'planned'):
        continue
    q = float(o.get('quality_score') or 0.0)
    ca = al_score(o)
    ra = ref_score(o)
    if not q and not ca and not ra:
        continue
    pte.append(o)
    if len(pte) >= 6:
        break
for i, o in enumerate(pte, 1):
    q = o.get('quality_score')
    print(f"  [{i}] {o.get('recorded_at')} | {o.get('event_type')} | task={o.get('task_id')} | "
          f"q={q!r} (scored={q is not None}) | ca={al_score(o):.3f} | ra={ref_score(o):.3f} | "
          f"target={norm_assessment(o).get('self_iteration_target')}")
scored = sum(1 for o in pte if o.get('quality_score') is not None)
print(f'  scored={scored}/{len(pte)}')

# ---------- drift window ----------
print('\n=== DRIFT 窗口 (outcomes[:12] with cognitive_alignment, max 4) ===')
drift = []
for o in outcomes[:12]:
    ca = o.get('cognitive_alignment')
    if not isinstance(ca, dict):
        md = o.get('metadata') or {}; ev = o.get('evidence') or {}
        ca = md.get('cognitive_alignment') if isinstance(md.get('cognitive_alignment'), dict) else None
        if not isinstance(ca, dict):
            ca = ev.get('cognitive_alignment') if isinstance(ev.get('cognitive_alignment'), dict) else None
    if not isinstance(ca, dict):
        continue
    drift.append(o)
    if len(drift) >= 4:
        break
for i, o in enumerate(drift, 1):
    md = o.get('metadata') or {}; ev = o.get('evidence') or {}
    ca = o.get('cognitive_alignment')
    if not isinstance(ca, dict):
        ca = md.get('cognitive_alignment') or ev.get('cognitive_alignment') or {}
    pa = o.get('llm_posture_alignment') or (md.get('llm_posture_alignment') if isinstance(md.get('llm_posture_alignment'), list) else None) or (ev.get('llm_posture_alignment') if isinstance(ev.get('llm_posture_alignment'), list) else None)
    print(f"  [{i}] {o.get('recorded_at')} | {o.get('event_type')} | task={o.get('task_id')} | "
          f"ca_score={ca.get('score')} | ca_quality={ca.get('quality')} | posture={bool(pa)}")
print('  window task_ids:', [o.get('task_id') for o in drift])

# ---------- 当前任务是否已入 outcomes ----------
print('\ncurrent task a61cd164 in outcomes?',
      sum(1 for o in outcomes if str(o.get('task_id')) == 'a61cd164-1b3e-4378-a937-fbb927dea3f1'))

# ---------- 测量层指标 ----------
print('\n=== 测量层指标 ===')
# 1. missing_evidence_nodes 分布
mec = collections.Counter()
for o in outcomes:
    ra = ra_dict(o)
    if ra:
        for n in (ra.get('missing_evidence_nodes') or []):
            mec[n] += 1
print('missing_evidence_nodes 全量分布:')
for k, v in mec.most_common():
    print(f'   {k}: {v}')
print('deliberation_state missing:', mec.get('deliberation_state', 0))
selfref = [k for k in mec if k in ('post_task_effect_memory', 'proposal_drift_memory', 'self_iteration_trend_memory', 'recent_learning', 'cognitive_assessment_memory')]
print('自指节点合计:', sum(mec.get(k, 0) for k in selfref), {k: mec.get(k, 0) for k in selfref})

# 2. weak_agenda_nodes 分布
wac = collections.Counter()
for o in outcomes:
    ra = ra_dict(o)
    if ra:
        for n in (ra.get('weak_agenda_nodes') or []):
            wac[n] += 1
print('\nweak_agenda_nodes top10:')
for k, v in wac.most_common(10):
    print(f'   {k}: {v}')
print('stabilize_memory_continuity weak:', wac.get('stabilize_memory_continuity', 0))

# 3. quality_score 分布 (scored 记录)
qs = collections.Counter()
for o in outcomes:
    v = o.get('quality_score')
    if v is not None:
        qs[round(float(v), 3)] += 1
print('\nquality_score 分布 (scored):', dict(qs), '| scored count:', sum(qs.values()))
evt_q = collections.Counter()
for o in outcomes:
    if o.get('quality_score') is not None:
        evt_q[o.get('event_type')] += 1
print('带分事件类型分布:', dict(evt_q))

# 4. reference_alignment 覆盖
with_ra = sum(1 for o in outcomes if ra_dict(o))
print(f'\nreference_alignment 覆盖: {with_ra}/{len(outcomes)}')

# 5. pte 三值全零跳过统计 (窗口内被跳过的条目)
skipped = []
for o in outcomes[:16]:
    et = str(o.get('event_type') or '').strip().lower()
    if et in ('', 'planned'):
        continue
    q = float(o.get('quality_score') or 0.0)
    ca = al_score(o); ra = ref_score(o)
    if not q and not ca and not ra:
        skipped.append((o.get('recorded_at'), o.get('event_type'), o.get('task_id')))
print('\npte 窗口内被三值全零跳过条目:', skipped)
