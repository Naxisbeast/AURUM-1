#!/usr/bin/env python3
"""Read the walk-forward script, base64 encode it, print an SSH command that deploys it."""
import base64

with open('scripts/run_d4_walk_forward.py', 'rb') as f:
    encoded = base64.b64encode(f.read()).decode()

print(f'echo "{encoded}" | base64 -d > /opt/aurum1/scripts/run_d4_walk_forward.py')
