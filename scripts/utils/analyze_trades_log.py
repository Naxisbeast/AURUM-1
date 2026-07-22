import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
conn = sqlite3.connect(str(ROOT / 'aurum1' / 'data' / 'aurum1.sqlite3'))
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Schema of trades_log
print("=== Schema: trades_log ===")
cur.execute("SELECT sql FROM sqlite_master WHERE name='trades_log'")
print(cur.fetchone()[0])

print("\n=== Schema: performance_log ===")
cur.execute("SELECT sql FROM sqlite_master WHERE name='performance_log'")
print(cur.fetchone()[0])

# trades_log summary
print("\n=== TRADES_LOG SUMMARY ===")
cur.execute("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM trades_log")
r = cur.fetchone()
print(f"Total: {r[0]}, Range: {r[1]} -> {r[2]}")

# trades_log last 20
print("\n=== TRADES_LOG LAST 20 (DESC) ===")
cur.execute("SELECT * FROM trades_log ORDER BY timestamp DESC LIMIT 20")
keys = [desc[0] for desc in cur.description]
print(' | '.join(keys))
for row in cur.fetchall():
    print(' | '.join(str(v) if v is not None else 'NULL' for v in row))

# performance_log
print("\n=== PERFORMANCE_LOG ===")
cur.execute("SELECT * FROM performance_log")
keys = [desc[0] for desc in cur.description]
print(' | '.join(keys))
for row in cur.fetchall():
    print(' | '.join(str(v) if v is not None else 'NULL' for v in row))

conn.close()
