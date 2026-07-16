"""Comprehensive analysis of aurum1.sqlite3 for paper trader context."""
import sqlite3
import json

conn = sqlite3.connect(r'C:\Users\thape\Desktop\Trading algorithim\aurum1\data\aurum1.sqlite3')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# trades_log breakdown
print("=== TRADES_LOG BREAKDOWN ===")
cur.execute("SELECT status, COUNT(*) as cnt FROM trades_log GROUP BY status")
for r in cur.fetchall():
    print(f"  status='{r['status']}': {r['cnt']}")

print()
cur.execute("SELECT direction, COUNT(*) as cnt FROM trades_log WHERE direction IS NOT NULL GROUP BY direction")
for r in cur.fetchall():
    print(f"  direction={r['direction']}: {r['cnt']}")

print()
cur.execute("SELECT status, direction, COUNT(*) FROM trades_log GROUP BY status, direction ORDER BY status, direction")
for r in cur.fetchall():
    print(f"  {r[0]}/{r[1]}: {r[2]}")

# Trades with entry/exit prices (non-NULL)
cur.execute("SELECT COUNT(*) FROM trades_log WHERE status='filled' AND price IS NOT NULL")
print(f"\nFilled trades with prices: {cur.fetchone()[0]}")

# Rejected count
cur.execute("SELECT COUNT(*) FROM trades_log WHERE status='rejected'")
print(f"Rejected trades: {cur.fetchone()[0]}")

# Rejection reasons
cur.execute("SELECT payload_json FROM trades_log WHERE status='rejected' AND payload_json IS NOT NULL LIMIT 5")
reasons = {}
for r in cur.fetchall():
    try:
        pj = json.loads(r[0])
        rr = pj.get('rejection_reason', 'unknown')
        reasons[rr] = reasons.get(rr, 0) + 1
    except:
        pass

# Better: get from the JSON
cur.execute("SELECT payload_json FROM trades_log WHERE status='rejected'")
from collections import Counter
rej = Counter()
for r in cur.fetchall():
    try:
        pj = json.loads(r[0])
        rr = pj.get('rejection_reason') or pj.get('risk_order', {}).get('rejection_reason', 'unknown')
        rej[rr] += 1
    except:
        rej['parse_error'] += 1
print(f"Rejection reasons: {dict(rej)}")

# Time span analysis
cur.execute("SELECT MIN(timestamp), MAX(timestamp) FROM trades_log")
r = cur.fetchone()
print(f"\nTime span: {r[0]} -> {r[1]}")

# Distinct dates active
cur.execute("SELECT DISTINCT substr(timestamp,1,10) as d FROM trades_log ORDER BY d")
dates = [r[0] for r in cur.fetchall()]
print(f"Dates active: {len(dates)} days")
print(f"  {dates[:5]}...{dates[-5:] if len(dates) > 10 else ''}")

# Trades per status per direction detail
cur.execute("""
    SELECT status, direction,
           COUNT(*) as cnt,
           MIN(price) as min_price,
           MAX(price) as max_price,
           AVG(price) as avg_price
    FROM trades_log
    WHERE price IS NOT NULL
    GROUP BY status, direction
    ORDER BY status, direction
""")
print("\nPrice stats by status/direction:")
for r in cur.fetchall():
    print(f"  {r['status']}/{r['direction']}: n={r['cnt']}, avg_price={r['avg_price']:.2f}")

# Regime info from payloads
cur.execute("SELECT payload_json FROM trades_log WHERE payload_json IS NOT NULL LIMIT 2000")
regimes = Counter()
for r in cur.fetchall():
    try:
        pj = json.loads(r[0])
        inst = pj.get('risk_order', {}).get('instruction', {})
        reg = inst.get('regime', 'unknown')
        regimes[reg] += 1
    except:
        pass
print(f"\nRegime distribution (from sample): {dict(regimes.most_common())}")

conn.close()
