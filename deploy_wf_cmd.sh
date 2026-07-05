#!/bin/bash
# Deploy walk-forward script to server via base64
ssh -i ~/.ssh/aurum1_key root@178.105.245.66 "base64 -d > /opt/aurum1/scripts/run_d4_walk_forward.py" < scripts/wf_b64.txt && echo "---VERIFY---" && ssh -i ~/.ssh/aurum1_key root@178.105.245.66 "wc -l /opt/aurum1/scripts/run_d4_walk_forward.py && head -5 /opt/aurum1/scripts/run_d4_walk_forward.py"
