import sqlite3

conn = sqlite3.connect(r'C:\Users\thape\Desktop\Trading algorithim\aurum1\data\aurum1.sqlite3')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")
rows = cur.fetchall()
print("Tables/Views in aurum1.sqlite3:")
for r in rows:
    print(f"  {r['name']} ({r['type']})")
print()

for r in rows:
    try:
        cur.execute(f'SELECT COUNT(*) FROM "{r["name"]}"')
        cnt = cur.fetchone()[0]
        print(f"  {r['name']}: {cnt} rows")
    except Exception as e:
        print(f"  {r['name']}: {e}")

# Also check for any paper-trade-like tables
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%paper%' OR name LIKE '%trade%' OR name LIKE '%position%')")
pt = cur.fetchall()
if pt:
    print("\nPaper/Trade-related tables:")
    for r in pt:
        print(f"  {r[0]}")

conn.close()
