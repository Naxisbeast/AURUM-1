import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = str(ROOT / 'aurum1' / 'data' / 'paper_trading.sqlite3')
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=" * 70)
print("PAPER TRADING DATABASE ANALYSIS")
print("=" * 70)

# -- Schemas --
for tbl in ['trades', 'account_snapshots', 'open_positions', 'missed_signals', 'settings']:
    cur.execute(f'SELECT sql FROM sqlite_master WHERE type="table" AND name="{tbl}"')
    row = cur.fetchone()
    print(f"\n--- Schema: {tbl} ---")
    print(row[0] if row else "NOT FOUND")

# -- trades summary --
print("\n--- TRADES SUMMARY ---")
cur.execute('SELECT COUNT(*), MIN(exit_time), MAX(exit_time) FROM trades')
r = cur.fetchone()
print(f"Total trades: {r[0]}, Date range: {r[1]}  ->  {r[2]}")

print("\n--- ACCOUNT SNAPSHOTS COUNT ---")
cur.execute('SELECT COUNT(*) FROM account_snapshots')
print(f"Snapshots: {cur.fetchone()[0]}")

print("\n--- OPEN POSITIONS COUNT ---")
cur.execute('SELECT COUNT(*) FROM open_positions')
print(f"Open positions: {cur.fetchone()[0]}")

print("\n--- MISSED SIGNALS COUNT ---")
cur.execute('SELECT COUNT(*) FROM missed_signals')
print(f"Missed signals: {cur.fetchone()[0]}")

print("\n--- SETTINGS ---")
cur.execute('SELECT * FROM settings')
rows = cur.fetchall()
if rows:
    keys = [desc[0] for desc in cur.description]
    for r in rows:
        for k, v in zip(keys, r):
            print(f"  {k}: {v}")
else:
    print("  (empty)")

print("\n--- LAST 20 TRADES (desc) ---")
cur.execute('SELECT timestamp, direction, entry_price, exit_price, r_multiple, net_pnl FROM trades ORDER BY timestamp DESC LIMIT 20')
rows = cur.fetchall()
keys = [desc[0] for desc in cur.description]
print(f"{' | '.join(keys)}")
for r in rows:
    print(' | '.join(str(v) if v is not None else 'NULL' for v in r))

print("\n--- OPEN POSITIONS ---")
cur.execute('SELECT * FROM open_positions')
rows = cur.fetchall()
if rows:
    keys = [desc[0] for desc in cur.description]
    print(f"{' | '.join(keys)}")
    for r in rows:
        print(' | '.join(str(v) if v is not None else 'NULL' for v in r))
else:
    print("  (empty)")

print("\n--- MISSED SIGNALS (last 20) ---")
cur.execute('SELECT timestamp, direction, price, reason FROM missed_signals ORDER BY timestamp DESC LIMIT 20')
rows = cur.fetchall()
if rows:
    keys = [desc[0] for desc in cur.description]
    print(f"{' | '.join(keys)}")
    for r in rows:
        print(' | '.join(str(v) if v is not None else 'NULL' for v in r))
else:
    print("  (empty)")

print("\n--- ACCOUNT SNAPSHOTS (last 10) ---")
cur.execute('SELECT timestamp, equity, balance, peak_equity, daily_pnl FROM account_snapshots ORDER BY timestamp DESC LIMIT 10')
rows = cur.fetchall()
keys = [desc[0] for desc in cur.description]
print(f"{' | '.join(keys)}")
for r in rows:
    print(' | '.join(str(v) if v is not None else 'NULL' for v in r))

# -- Additional useful stats --
print("\n--- EXTRA STATS ---")
cur.execute('SELECT COUNT(*), SUM(CASE WHEN direction="LONG" THEN 1 ELSE 0 END), SUM(CASE WHEN direction="SHORT" THEN 1 ELSE 0 END) FROM trades')
r = cur.fetchone()
print(f"Long trades: {r[1]}, Short trades: {r[2]}")

cur.execute('SELECT COUNT(*), SUM(net_pnl) FROM trades')
r = cur.fetchone()
print(f"Closed trades: {r[0]}, Total net PnL: {r[1]:.2f}" if r[1] is not None else f"Closed trades: {r[0]}, Total net PnL: NULL")

cur.execute('SELECT COUNT(*), SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END), SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END) FROM trades WHERE net_pnl IS NOT NULL')
r = cur.fetchone()
print(f"Winners: {r[1]}, Losers: {r[2]}")

cur.execute('SELECT AVG(r_multiple), AVG(net_pnl) FROM trades WHERE net_pnl IS NOT NULL')
r = cur.fetchone()
print(f"Avg R-multiple: {r[0]:.2f}" if r[0] is not None else "Avg R-multiple: NULL")
print(f"Avg net PnL: {r[1]:.2f}" if r[1] is not None else "Avg net PnL: NULL")

# Last snapshot
cur.execute('SELECT * FROM account_snapshots ORDER BY timestamp DESC LIMIT 1')
r = cur.fetchone()
if r:
    keys = [desc[0] for desc in cur.description]
    print("\n--- LATEST ACCOUNT STATE ---")
    for k, v in zip(keys, r):
        print(f"  {k}: {v}")

conn.close()
