#!/usr/bin/env python3
"""Deploy run_d4_walk_forward.py to server via SSH + base64."""
import subprocess, base64, sys

with open('scripts/run_d4_walk_forward.py', 'rb') as f:
    b64 = base64.b64encode(f.read()).decode()

cmd = [
    'ssh', '-i', f'{sys.argv[1]}/.ssh/aurum1_key',
    'root@178.105.245.66',
    f'base64 -d <<<"{b64}" > /opt/aurum1/scripts/run_d4_walk_forward.py && wc -l /opt/aurum1/scripts/run_d4_walk_forward.py'
]

r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
print(r.stdout)
if r.returncode != 0:
    print("STDERR:", r.stderr, file=sys.stderr)
    sys.exit(r.returncode)
