# -*- coding: utf-8 -*-
"""Rebuild snapshot-time assessment window at candidate timestamps + full assessment dump."""
import json, sys, collections
sys.path.insert(0, 'src')
from voidcube.systems.supervisor.endogenous_cognitive_memory import build_cognitive_assessment_memory

p = r'C:/Users/lishuo/.VoidCube/runtime/supervisor/endogenous_drive_history.json'
d = json.load(open(p, encoding='utf-8'))

# 1. full assessment dump of recent entries (judgement text)
print('===== FULL ASSESSMENT (recent 6 with assessment) =====')
n = 0
for o in d['outcomes']:
    a = o.get('llm_cognitive_assessment')
    if not isinstance(a, dict) or not a.get('current_judgement'):
        continue
    n += 1
    print('--- %s %s %s' % (o.get('recorded_at'), o.get('event_type'), str(o.get('task_id'))[:8]))
    print('  judgement: %s' % str(a.get('current_judgement'))[:180])
    print('  why: %s' % json.dumps(a.get('why_not_improvement_now'), ensure_ascii=False)[:200])
    print('  gaps: %s' % json.dumps(a.get('primary_grounding_gaps'), ensure_ascii=False)[:160])
    if n >= 6:
        break

# 2. rebuild assessment memory at candidate timestamps
print('\n===== SNAPSHOT WINDOW REBUILD (recorded_at <= t) =====')
cands = ['2026-08-24T11:25:00', '2026-08-24T11:27:00', '2026-08-24T11:32:20',
         '2026-08-24T11:34:00', '2026-08-24T11:36:00', '2026-08-24T11:40:15',
         '2026-08-24T11:40:40']
for t in cands:
    sub = {'outcomes': [o for o in d['outcomes'] if o.get('recorded_at','') <= t]}
    r = build_cognitive_assessment_memory({'drive_history': sub})
    print('%s | why_cnt=%s gap=%s gap_cnt=%s jcount=%s entry=%s' % (
        t, r.get('why_not_improvement_now_count'), str(r.get('primary_grounding_gap'))[:42],
        r.get('grounding_gap_count'), r.get('current_judgement_count'), r.get('entry_count')))

# 3. where are weak_agenda_nodes / missing_evidence_nodes?
print('\n===== FIELD SEARCH: weak_agenda_nodes / missing_evidence_nodes =====')
ca_keys = collections.Counter()
found = collections.Counter()
for o in d['outcomes']:
    ca = o.get('cognitive_alignment')
    if isinstance(ca, dict):
        ca_keys.update(ca.keys())
        for k in ca:
            v = ca[k]
            if isinstance(v, list) and any(isinstance(x, str) and 'node' in x for x in v):
                found[k] += 1
    lpa = o.get('llm_posture_alignment')
    if isinstance(lpa, dict):
        ca_keys.update(('posture:'+k) for k in lpa)
    lpb = o.get('llm_priority_basis')
    if isinstance(lpb, dict):
        ca_keys.update(('basis:'+k) for k in lpb)
print('cognitive_alignment keys:', dict(ca_keys))
print('list-like fields w/ node strings:', dict(found))
