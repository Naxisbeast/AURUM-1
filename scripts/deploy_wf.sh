#!/bin/bash
# Deploy the walk-forward script
B64=$(base64 -w0 scripts/run_d4_walk_forward.py)
echo "$B64" | ssh -i ~/.ssh/aurum1_key root@178.105.245.66 "base64 -d > /opt/aurum1/scripts/run_d4_walk_forward.py"
echo "Deployed $(wc -l < scripts/run_d4_walk_forward.py) lines"
