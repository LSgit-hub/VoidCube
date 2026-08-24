# -*- coding: utf-8 -*-
"""Round-11 probe: extract assessment timeline, window members, key counters."""
import json, sys, collections
sys.path.insert(0, 'src')

p = r'C:/Users/lishuo/.VoidCube/runtime/supervisor/endogenous_drive_history.json'
d = json.load(open(p, encoding='utf-8'))
outs = d.get('outcomes', [])
print('updated_at:', d.get('updated_at'))
print('total outcomes:', len(outs))
print('judgements:', len(d.get('judgements', [])))

def get_assessment(o):
    md = o.get('metadata') or {}
    ev = o.get('evidence') or {}
    a = o.get('llm_cognitive_assessment') or md.get('llm_cognitive_assessment') or ev.get('llm_cognitive_assessment')
    return a if isinstance(a, dict) else None

# 1. timeline of assessment entries (most recent 12)
print('\n===== ASSESSMENT TIMELINE (recent 12) =====')
n = 0
for o in reversed(outs):
    a = get_assessment(o)
    if a and a.get('current_judgement'):
        n += 1
        print('---', o.get('recorded_at'), o.get('event_type'), o.get('task_id','')[:8],
              '| target=%s | gap=%s(count=%s) | why=%s | jcount=%s' % (
                  (a.get('self_iteration_target') or '')[:30],
                  (a.get('primary_grounding_gap') or '')[:40], a.get('grounding_gap_count'),
                  a.get('why_not_improvement_now_count'), a.get('current_judgement_count')))
        if n >= 12:
            break

# 2. PTE window members (replicate builder logic: outcomes[:16] skip planned/empty, skip all-zero)
from voidcube.systems.supervisor.endogenous_cognitive_memory import build_post_task_effect_memory
print('\n===== PTE window scan =====')
cnt = 0
for o in outs[:16]:
    et = o.get('event_type') or ''
    if et in ('planned', '') or et is None:
        continue
    q = o.get('quality_score'); ca = o.get('cognitive_alignment') or {}
    ref = o.get('reference_alignment') or {}
    ca_s = (ca.get('score') if isinstance(ca, dict) else None)
    ref_s = (ref.get('score') if isinstance(ref, dict) else None)
    if (q in (None, 0) and ca_s in (None, 0) and ref_s in (None, 0)):
        continue
    cnt += 1
    print('%d. %s %s %s q=%s ca=%s ref=%s' % (cnt, o.get('recorded_at'), et,
          str(o.get('task_id'))[:8], q, ca_s, ref_s))
print('PTE window size:', cnt)

# 3. drift window members (outcomes[:12] with ca score, first 4)
print('\n===== DRIFT window scan =====')
cnt = 0
for o in outs[:12]:
    ca = o.get('cognitive_alignment')
    if not isinstance(ca, dict) or ca.get('score') is None:
        continue
    cnt += 1
    qq = ca.get('quality')
    print('%d. %s %s %s ca=%s quality=%s' % (cnt, o.get('recorded_at'), o.get('event_type'),
          str(o.get('task_id'))[:8], ca.get('score'), qq))
print('DRIFT window size:', cnt)

# 4. key counters
print('\n===== KEY COUNTERS =====')
c_missing = collections.Counter()
c_weak_agenda = collections.Counter()
c_weak_evidence = collections.Counter()
c_missing_agenda = collections.Counter()
scored = 0; total = 0; qvals = collections.Counter()
ra_total = 0
for o in outs:
    total += 1
    md = o.get('metadata') or {}; ev = o.get('evidence') or {}
    for node in (o.get('missing_evidence_nodes') or md.get('missing_evidence_nodes') or ev.get('missing_evidence_nodes') or []):
        c_missing[node] += 1
    for node in (o.get('weak_agenda_nodes') or md.get('weak_agenda_nodes') or ev.get('weak_agenda_nodes') or []):
        c_weak_agenda[node] += 1
    for node in (o.get('weak_evidence_nodes') or md.get('weak_evidence_nodes') or ev.get('weak_evidence_nodes') or []):
        c_weak_evidence[node] += 1
    for node in (o.get('missing_agenda_nodes') or md.get('missing_agenda_nodes') or ev.get('missing_agenda_nodes') or []):
        c_missing_agenda[node] += 1
    q = o.get('quality_score')
    if q is not None:
        scored += 1; qvals[q] += 1
    ra = o.get('reference_alignment')
    if isinstance(ra, dict) and ra.get('score') is not None:
        ra_total += 1
print('missing_evidence total:', sum(c_missing.values()), dict(c_missing))
print('missing_agenda:', sum(c_missing_agenda.values()), dict(c_missing_agenda))
print('weak_agenda:', sum(c_weak_agenda.values()), dict(c_weak_agenda))
print('weak_evidence:', sum(c_weak_evidence.values()), dict(c_weak_evidence))
print('quality scored/total: %d/%d  dist=%s' % (scored, total, dict(qvals)))
print('with reference_alignment: %d/%d' % (ra_total, total))

# selfref node timestamps (post_task_effect_memory / proposal_drift_memory in missing_evidence)
print('\n===== SELFREF MISSING SOURCE (post_task_effect_memory / proposal_drift_memory / recent_learning / self_iteration_trend_memory) =====')
for o in outs:
    md = o.get('metadata') or {}; ev = o.get('evidence') or {}
    nodes = (o.get('missing_evidence_nodes') or md.get('missing_evidence_nodes') or ev.get('missing_evidence_nodes') or [])
    selfref = [x for x in nodes if x in ('post_task_effect_memory','proposal_drift_memory','recent_learning','self_iteration_trend_memory')]
    if selfref:
        print(o.get('recorded_at'), str(o.get('task_id'))[:8], o.get('event_type'), selfref)

# external_research missing entries (snapshot claimed primary gap)
print('\n===== external_research missing entries =====')
for o in outs:
    md = o.get('metadata') or {}; ev = o.get('evidence') or {}
    nodes = (o.get('missing_evidence_nodes') or md.get('missing_evidence_nodes') or ev.get('missing_evidence_nodes') or [])
    if 'external_research' in nodes:
        print(o.get('recorded_at'), str(o.get('task_id'))[:8], o.get('event_type'))
