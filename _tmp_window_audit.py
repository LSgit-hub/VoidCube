"""窗口构成细查 + 全量基线统计（只读）"""
import json, sys, io
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

p = r'C:/Users/lishuo/.VoidCube/runtime/supervisor/endogenous_drive_history.json'
d = json.load(open(p, encoding='utf-8'))
outs = d['outcomes']
print('total outcomes:', len(outs))

def get(o, *keys):
    cur = o
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur

# ---- 全量基线 ----
print('\n===== 全量基线 =====')
with_ra = [o for o in outs if get(o, 'reference_alignment') or get(o, 'metadata', 'reference_alignment') or get(o, 'evidence', 'reference_alignment')]
print('带 reference_alignment 条数:', len(with_ra), '/', len(outs))

# quality_score 分布
qs = [(o.get('recorded_at'), o.get('event_type'), o.get('quality_score')) for o in outs if o.get('quality_score') is not None]
print('\nquality_score 有分条数:', len(qs))
print('有分 event_type 分布:', dict(Counter(x[1] for x in qs)))
print('有分 value 分布:', dict(Counter(x[2] for x in qs)))

# missing_evidence_nodes 全量分布 + 时间戳归属
me = []
for o in outs:
    ra = get(o, 'reference_alignment') or get(o, 'metadata', 'reference_alignment') or get(o, 'evidence', 'reference_alignment')
    if ra and isinstance(ra, dict):
        for n in (ra.get('missing_evidence_nodes') or []):
            me.append((o.get('recorded_at', '')[:10], n))
me_counter = Counter(n for _, n in me)
print('\nmissing_evidence_nodes 全量分布:', dict(me_counter))
print('按日期归属:')
by_date = {}
for dt, n in me:
    by_date.setdefault(dt, Counter())[n] += 1
for dt in sorted(by_date):
    print(' ', dt, dict(by_date[dt]))

# weak_agenda_nodes 全量分布
wa = []
for o in outs:
    ra = get(o, 'reference_alignment') or get(o, 'metadata', 'reference_alignment') or get(o, 'evidence', 'reference_alignment')
    if ra and isinstance(ra, dict):
        for n in (ra.get('weak_agenda_nodes') or []):
            wa.append(n)
print('\nweak_agenda_nodes 全量分布:', dict(Counter(wa)))
print('stabilize_memory_continuity weak 计数:', Counter(wa).get('stabilize_memory_continuity', 0))

# missing_agenda_nodes
ma = Counter()
for o in outs:
    ra = get(o, 'reference_alignment') or get(o, 'metadata', 'reference_alignment') or get(o, 'evidence', 'reference_alignment')
    if ra and isinstance(ra, dict):
        for n in (ra.get('missing_agenda_nodes') or []):
            ma[n] += 1
print('\nmissing_agenda_nodes 全量分布:', dict(ma))

# ---- 窗口细查 ----
print('\n===== post_task_effect 窗口 (outcomes[:16] 跳过 planned/空, 前6条) =====')
cnt = 0
for o in outs[:16]:
    et = o.get('event_type')
    if not et or et == 'planned':
        continue
    cnt += 1
    if cnt > 6:
        break
    print('  [%d] %s | %s | %s | q=%s | ca=%s | ra=%s' % (
        cnt, o.get('recorded_at', '')[:19], o.get('event_type'),
        str(o.get('task_id'))[:12], o.get('quality_score'),
        get(o, 'cognitive_alignment', 'score'), get(o, 'reference_alignment', 'alignment_score')))

print('\n===== proposal_drift 窗口 (outcomes[:12] 带 cognitive_alignment, 前4条) =====')
cnt = 0
for o in outs[:12]:
    ca = get(o, 'cognitive_alignment') or get(o, 'metadata', 'cognitive_alignment') or get(o, 'evidence', 'cognitive_alignment')
    if not ca:
        continue
    cnt += 1
    if cnt > 4:
        break
    print('  [%d] %s | %s | %s | ca.score=%s | ca.quality=%s' % (
        cnt, o.get('recorded_at', '')[:19], o.get('event_type'),
        str(o.get('task_id'))[:12], ca.get('score'), ca.get('quality')))

print('\n===== reference_alignment 窗口 (最近12条带 ra, 前?条) =====')
cnt = 0
for o in outs[:12]:
    ra = get(o, 'reference_alignment') or get(o, 'metadata', 'reference_alignment') or get(o, 'evidence', 'reference_alignment')
    if not ra:
        continue
    cnt += 1
    if cnt > 8:
        break
    print('  [%d] %s | %s | %s | score=%s | q=%s | miss_ev=%s | miss_ag=%s' % (
        cnt, o.get('recorded_at', '')[:19], o.get('event_type'),
        str(o.get('task_id'))[:12], ra.get('alignment_score'), ra.get('alignment_quality'),
        ra.get('missing_evidence_nodes'), ra.get('missing_agenda_nodes')))

print('\n===== 最近 16 条 event_type / task_id / 时间 =====')
for i, o in enumerate(outs[:16]):
    print('  [%02d] %s | %s | %s | %s' % (i, o.get('recorded_at', '')[:19], o.get('event_type'),
          str(o.get('task_id'))[:12], str(o.get('decision_id'))[:12]))

# 本任务是否已入 outcomes
tid = '1c67c351'
mine = [o for o in outs if str(o.get('task_id', '')).startswith(tid)]
print('\n本任务 1c67c351 在 outcomes 中的记录数:', len(mine))
for o in mine:
    print('  ', o.get('recorded_at'), o.get('event_type'), 'q=', o.get('quality_score'))
