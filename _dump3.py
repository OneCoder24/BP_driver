with open('jogging_panel.py', encoding='utf-8') as f:
    lines = f.read().splitlines(keepends=True)
def dump(a, b):
    print(f'=== lines {a}-{b} ===')
    for i in range(a-1, b):
        print(f'{i+1:4}| {lines[i]!r}')
dump(146, 158)
dump(159, 217)
dump(266, 310)
