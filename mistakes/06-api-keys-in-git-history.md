# API Keys in Git History

**What happened**: The `.env` file containing OANDA and FRED API keys was committed in the initial repository push and propagated through 9+ commits before being noticed.

**Impact**: API keys were exposed in git history. Required a full `git-filter-repo` scrub, key rotation, and pre-commit hook installation.

**Fix**: 
- `git-filter-repo` to purge keys from history
- Rotated all affected API keys
- Added `.env` to `.gitignore`
- Installed `.githooks/pre-commit` to block future credential commits

**Lesson**: One mistake in your first commit can haunt you forever. Set up guards before you need them.
