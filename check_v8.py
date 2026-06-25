import urllib.request, re

r = urllib.request.urlopen('http://127.0.0.1:6002/ui', timeout=5)
data = r.read().decode('utf-8', errors='ignore')

# Debug
print('=== organize block ===')
m = re.search(r'data-action="organize"\][^{]*\{[^}]*\}', data)
if m: print(m.group(0))

print('\n=== desk-main block ===')
m = re.search(r'\.desk-main\s*\{[^}]*\}', data)
if m: print(m.group(0))

print('\n=== shelf block ===')
m = re.search(r'\.shelf\s*\{[^}]*\}', data)
if m: print(m.group(0))

print('\n=== sofa block ===')
m = re.search(r'\.sofa\s*\{[^}]*\}', data)
if m: print(m.group(0))

print('\n=== OLD 160 occurrences ===')
for m in re.finditer(r'height:\s*160px', data):
    start = max(0, m.start()-40)
    end = min(len(data), m.end()+40)
    print(f'  pos {m.start()}: ...{data[start:end]}...')
