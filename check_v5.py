import urllib.request

r = urllib.request.urlopen('http://127.0.0.1:6002/ui', timeout=5)
data = r.read().decode('utf-8', errors='ignore')

checks = [
    ('organize bottom 22%',  'bottom: 22%' in data),
    ('rest bottom 10%',      'bottom: 10%' in data),
    ('organize left 13%',    'left: 13%' in data),
    ('rest left 17%',        'left: 17%' in data),
    ('OLD organize b6 gone', 'bottom: 6%;' not in data.split('organize')[1].split('}')[0] if 'organize' in data else True),
    ('OLD rest b26 gone',    'bottom: 26%;' not in data.split('rest')[1].split('}')[0] if 'rest' in data else True),
    ('charToggle id',        'id="charToggle"' in data),
    ('char-card.collapsed',  '.char-card.collapsed' in data),
    ('等级',                 '等级' in data),
    ('次替身切换',           '次替身切换' in data),
    ('OLD body switch',      'body switch' not in data),
    ('OLD Lv. text',         'Lv.' not in data),
]
for label, ok in checks:
    mark = 'OK ' if ok else 'XX '
    print(f'  [{mark}] {label}')

# 抽查位置片段
import re
m = re.search(r'data-action="organize"\]\s*\.xizi\s*\{[^}]*\}', data)
if m: print('\n[organize block]:', m.group(0)[:200])
m = re.search(r'data-action="rest"\]\s*\.xizi\s*\{[^}]*\}', data)
if m: print('[rest block]:', m.group(0)[:200])
