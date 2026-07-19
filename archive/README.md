# AURUM-1 Archive

This directory contains code, experiments, and research notes that have been **removed from the active codebase** but preserved for reference.

## Contents

| Path | Original Location | Preserved Because |
|------|------------------|-------------------|
| `experiments/` | `experiments/` | ML experiment scripts (metalabeler, multi-asset, sweep, etc.) |
| `aurum1/orchestrator.py` | `aurum1/orchestrator.py` | Original full ML orchestrator — shows how all modules wired together |
| `aurum1/phase_s*.py` | `aurum1/reports/phase_s*.py` | Phase S1-S5 shadow audit/report modules |
| `aurum1/ai_co_pilot/` | `aurum1/ai_co_pilot/` | AI co-pilot experiments |
| `scripts/shadow/run_phase_s*.py` | `scripts/shadow/run_phase_s*.py` | Phase S shadow audit run scripts |
| `scripts/ml/` | `scripts/ml/` | ML model training + validation scripts |
| `tests/test_phase3_*.py` | `tests/test_phase3_*.py` | ML model tests |
| `tests/test_phase_s*.py` | `tests/test_phase_s*.py` | Phase S shadow audit tests |
| `research/` | `research/` | Strategy research plans and notes (markdown) |
| `exports/` | `exports/` | Obsidian export template |
| `journey/` | `journey/` | Legacy journey documentation |

## Policy

- **Do not restore** archived code to active directories without an explicit decision record
- **Do not delete** archived files — the archive is permanent
- To add new items to the archive: `cp -r <source> archive/<path>` then `rm -rf <source>`

*Archived 2026-07-18 during AURUM Hardening v1.0 Phase 1.*
