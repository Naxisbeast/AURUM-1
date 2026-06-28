# Contributing to AURUM-1

## Safety First

AURUM-1 is a trading system that could lose real money if misconfigured. Every contribution must respect the safety interlocks:

1. **Never commit `.env`** — it contains API keys and secrets
2. **Never change safety checks** — `ALLOW_OANDA_ORDERS`, `ALLOW_LIVE_TRADING`, and `OANDA_ENV` interlocks must remain
3. **Never remove paper_trade defaults** — the system must default to safe mode
4. **Always add tests** — the test suite protects against regression

## Development Workflow

### 1. Branch Naming

```text
feature/<description>     — New features
fix/<description>         — Bug fixes
research/<phase>          — Research phase work
docs/<description>        — Documentation changes
```

### 2. Code Standards

- **Python**: 3.12 strict (required by forward shadow runtime)
- **Type hints**: Required for all function signatures
- **Tests**: Add or update tests for any logic change
- **Logging**: Use the project's `logger` (loguru or stdlib fallback)
- **No hardcoded secrets**: Use environment variables or `.env`

### 3. Running Tests

```bash
# Run full test suite
python -m pytest -q --basetemp .pytest_tmp -p no:cacheprovider

# Run specific test file
python -m pytest tests/test_forward_shadow_donchian.py -v

# Compile check (Python 3.12)
python -m py_compile scripts/forward_shadow_donchian.py
```

### 4. Before Submitting

- [ ] Tests pass: `python -m pytest -q --basetemp .pytest_tmp -p no:cacheprovider`
- [ ] No `.env` or secrets in the diff
- [ ] No changes to safety interlocks unless explicitly reviewed
- [ ] Documentation updated (README, docs/, inline comments)
- [ ] Commits are atomic and descriptive

### 5. Research Contributions

Research phases follow a specific pattern:

1. **Read-only**: Never modify live/paper execution behavior
2. **Independent**: Each phase reads directly from SQLite, not from other phases' outputs
3. **Auditable**: Produce timestamped CSV + JSON artifacts
4. **Reported**: Update `docs/RESEARCH.md` with findings

New research phases should:
- Live in `aurum1/reports/phase_sN_*.py`
- Have a run script in `scripts/run_phase_sN_*.py`
- Produce artifacts in `reports/forward_shadow/`
- Assert safety interlocks before processing

### 6. Deployment Contributions

Changes that affect the cloud deployment must:
- Update systemd service templates in `deploy/`
- Update `docs/DEPLOYMENT.md`
- Update `docs/DEPLOYMENT_CHECKLIST.md`
- Be tested on `aurum1-paper-server` before merging

## Git Workflow

```bash
# Create feature branch
git checkout -b feature/my-change

# Make changes and commit
git add <files>
git commit -m "Description of change"

# Push and create PR
git push origin feature/my-change
```

Commit messages should be descriptive and reference related issues.

## Safety Review Checklist

For any pull request, verify:

- [ ] No `.env` committed
- [ ] No API keys or secrets exposed
- [ ] Safety interlocks intact (`ALLOW_OANDA_ORDERS`, `ALLOW_LIVE_TRADING`)
- [ ] Paper broker remains the default
- [ ] Forward shadow still asserts safety checks
- [ ] SQLite paths use project root resolution
- [ ] Logging covers error paths
