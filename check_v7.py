import urllib.request, re

r = urllib.request.urlopen('http://127.0.0.1:6002/ui', timeout=5)
data = r.read().decode('utf-8', errors='ignore')

checks = [
    ('organize bottom 2%',         r'bottom:\s*2%' in re.search(r'data-action="organize"\][^{]*\{[^}]*\}', data).group(0)),
    ('organize left 10%',          r'left:\s*10%' in re.search(r'data-action="organize"\][^{]*\{[^}]*\}', data).group(0)),
    ('desk-main height 120px',     'height: 120px' in data),
    ('desk-main top 54%',          'top: 54%' in data),
    ('desk-top height 16px',       'height: 16px' in data),
    ('desk-body top 16px',         'top: 16px' in data),
    ('desk-monitor top -156px',    'top: -156px' in data),
    ('desk-keyboard top -22px',    'top: -22px' in data),
    ('desk-mouse top -20px',       'top: -20px' in data),
    ('desk-mug top -30px',         'top: -30px' in data),
    ('desk-notebook top -14px',    'top: -14px' in data),
    ('shelf width 360px',          'width: 360px' in re.search(r'\.shelf\s*\{[^}]*\}', data).group(0)),
    ('shelf height 460px',         'height: 460px' in re.search(r'\.shelf\s*\{[^}]*\}', data).group(0)),
    ('sofa width 480px',           'width: 480px' in re.search(r'\.sofa\s*\{[^}]*\}', data).group(0)),
    ('sofa height 200px',          'height: 200px' in re.search(r'\.sofa\s*\{[^}]*\}', data).group(0)),
    ('OLD desk height 160 gone',   'height: 160px' not in re.search(r'\.desk-main\s*\{[^}]*\}', data).group(0)),
    ('OLD keyboard top 50 gone',   'top: 50px' not in re.search(r'\.desk-keyboard\s*\{[^}]*\}', data).group(0)),
    ('OLD mug top 32 gone',        'top: 32px' not in re.search(r'\.desk-mug\s*\{[^}]*\}', data).group(0)),
]
for label, ok in checks:
    mark = 'OK ' if ok else 'XX '
    print(f'  [{mark}] {label}')
