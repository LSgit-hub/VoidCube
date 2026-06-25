import urllib.request
import sys

try:
    r = urllib.request.urlopen('http://127.0.0.1:6002/ui', timeout=3)
    data = r.read().decode('utf-8', errors='ignore')
    print('STATUS:', r.status)
    print('LEN:', len(data))
    checks = [
        'char-card', 'action-bar', 'class="xizi"', 'class="shelf"',
        'class="sofa"', 'class="desk-main"', 'class="desk-write"',
        'class="chair"', 'class="window"', 'class="wall-clock"',
        'class="floor-lamp"', 'class="plant', 'class="rug"',
        'class="picture"', 'data-action="organize"', 'data-action="rest"',
        'data-action="work"', 'data-action="write"',
    ]
    for c in checks:
        print(f'HAS_{c:30s} =', c in data)
except Exception as e:
    print('ERR:', type(e).__name__, e)
    sys.exit(1)
