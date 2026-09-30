import sqlite3

db = sqlite3.connect(r'C:\Users\12820\.openclaw\agents\main\agent\openclaw-agent.sqlite')
db.row_factory = sqlite3.Row

# 所有表
cur = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r['name'] for r in cur.fetchall()]
print('=== TABLES ===')
for t in tables:
    n = db.execute(f'SELECT COUNT(*) AS c FROM "{t}"').fetchone()['c']
    print(f'  {t}: {n} rows')

# 候选
candidates = [t for t in tables if any(k in t.lower() for k in ['msg', 'conversation', 'transcript', 'session', 'chat', 'history', 'turn', 'event'])]
print()
print('=== CANDIDATE TABLES ===')
for t in candidates:
    print(f'-- {t} --')
    for r in db.execute("SELECT sql FROM sqlite_master WHERE name=?", (t,)).fetchall():
        sql = r['sql'] or ''
        print('   ' + sql[:500].replace(chr(10), ' '))
