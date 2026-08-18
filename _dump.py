with open('jogging_panel.py', encoding='utf-8') as f:
    lines = f.read().splitlines(keepends=True)
print('TOTAL', len(lines))
for i, l in enumerate(lines, 1):
    print(f'{i:4}|' + l.rstrip('\n'))
