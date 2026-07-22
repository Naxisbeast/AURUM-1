# Overengineering the Orchestrator

**What happened**: I built a full ML pipeline — orchestrator, multiple ML models, feature engineering, pullback-breakout state machine, ensemble voting. It was elegant. It was complicated. And it produced ~3 trades a week.

**Impact**: Weeks of development time for a system that was too slow to be useful.

**Fix**: Killed the orchestrator. Replaced it with D4 — a 20-bar channel with no filters.

**Lesson**: The market doesn't care how clever your architecture is. Confusing complexity with sophistication is the most expensive mistake an engineer can make.
