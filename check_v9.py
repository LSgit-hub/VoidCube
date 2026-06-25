import urllib.request, re

r = urllib.request.urlopen('http://127.0.0.1:6002/ui', timeout=5)
data = r.read().decode('utf-8', errors='ignore')

pats = {
    'organize': r'data-action="organize"\]\s*\.xizi\s*\{[^}]*\}',
    'desk-main': r'\.desk-main\s*\{[^}]*\}',
    'shelf':     r'\.shelf\s*\{[^}]*\}',
    'sofa':      r'\.sofa\s*\{[^}]*\}',
    'desk-monitor': r'\.desk-monitor\s*\{[^}]*\}',
    'desk-keyboard': r'\.desk-keyboard\s*\{[^}]*\}',
    'desk-mug': r'\.desk-mug\s*\{[^}]*\}',
}
for label, pat in pats.items():
    m = re.search(pat, data)
    if m:
        text = m.group(0).replace('\n', ' ')
        print(f'[{label}] {text[:180]}')
