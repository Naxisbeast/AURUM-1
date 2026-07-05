#!/bin/bash
# Deploy the updated walk-forward script to the server
scp -i ~/.ssh/aurum1_key scripts/run_d4_walk_forward.py root@178.105.245.66:/opt/aurum1/scripts/run_d4_walk_forward.py
echo "--- verify ---"
ssh -i ~/.ssh/aurum1_key root@178.105.245.66 "wc -l /opt/aurum1/scripts/run_d4_walk_forward.py; md5sum /opt/aurum1/scripts/run_d4_walk_forward.py"
