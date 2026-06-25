import urllib.request

r = urllib.request.urlopen('http://127.0.0.1:6002/ui', timeout=5)
data = r.read().decode('utf-8', errors='ignore')

checks = [
    ('organize bottom 6% (on floor)',  'bottom: 6%' in data.split('data-action="organize"')[1].split('}')[0]),
    ('OLD organize bottom 22% gone',   'bottom: 22%' not in data.split('data-action="organize"')[1].split('}')[0]),
    ('desk-main top 50%',              'top: 50%' in data),
    ('desk-main height 160px',         'height: 160px' in data),
    ('OLD desk height 240px gone',     'height: 240px' not in data),
    ('desk-monitor top -156px',        'top: -156px' in data),
    ('OLD desk-monitor top -120 gone', 'top: -120px' not in data),
    ('desk-top height 20px',           'height: 20px' in data),
    ('desk-body top 20px',             'top: 20px' in data),
    ('OLD desk-top 26px gone',         'height: 26px' not in data),
]
for label, ok in checks:
    mark = 'OK ' if ok else 'XX '
    print(f'  [{mark}] {label}')

import re
print()
m = re.search(r'data-action="organize"\]\s*\.xizi\s*\{[^}]*\}', data)
if m: print('[organize block]:', m.group(0)[:200])

m = re.search(r'\.desk-main\s*\{[^}]*\}', data)
if m: print('[desk-main block]:', m.group(0)[:300])

m = re.search(r'\.desk-monitor\s*\{[^}]*\}', data)
if m: print('[desk-monitor block]:', m.group(0)[:200])
