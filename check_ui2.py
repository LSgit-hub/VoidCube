import urllib.request
r = urllib.request.urlopen('http://127.0.0.1:6002/ui', timeout=3)
data = r.read().decode('utf-8', errors='ignore')
# Print first 200 chars and look for key markers
print('FIRST 200:')
print(data[:200])
print('---')
print('CONTAINS v4:', 'v4' in data)
print('CONTAINS 书架:', '书架' in data)
print('CONTAINS 暖橘:', '暖橘' in data)
print('CONTAINS wall-clock:', 'wall-clock' in data)
print('CONTAINS cozy-sofa:', 'cozy-sofa' in data)
print('CONTAINS <section class="shelf":', '<section class="shelf"' in data)
print('CONTAINS class="shelf":', 'class="shelf"' in data)
print('CONTAINS class="sofa":', 'class="sofa"' in data)
print('CONTAINS class="sofa" anywhere:')
# search broadly
import re
for cls in ['shelf', 'sofa', 'desk-main', 'desk-write', 'chair', 'wall-clock', 'floor-lamp', 'plant', 'rug', 'picture']:
    n = len(re.findall(r'class="[^"]*\b' + re.escape(cls) + r'\b', data))
    print(f'  {cls}: {n} occurrences')
