with open('jogging_panel.py', encoding='utf-8') as f:
    lines = f.read().splitlines(keepends=True)
def dump(a, b):
    print(f'=== lines {a}-{b} ===')
    for i in range(a-1, b):
        print(f'{i+1:4}| {lines[i]!r}')
dump(13, 17)
dump(34, 44)
dump(47, 53)
dump(59, 77)
dump(86, 121)
dump(122, 146)
