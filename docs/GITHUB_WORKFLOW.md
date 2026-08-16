# AURUM-1 GitHub Workflow

How this repository is organised on GitHub — issues, milestones, branches, and pull requests.

## Issues

Every piece of planned work is tracked as a GitHub Issue. Issues describe *what* needs to change and *why* — not just the task, but the reasoning behind it.

Current open issues cover the audit-readiness work:
- Capacity & Decay Modeling
- Stationarity Audit for D4 Signal Components
- Track Realized vs Modeled Execution Costs
- Containerization Decision
- Signal Decay Monitoring
- Determinism Audit
- Model Documentation (SR 26-2 Lite)

Each issue links back to the relevant document (e.g. `docs/system/AUDIT_ROADMAP.md`) and records the decision context.

## Milestones

Issues are grouped into milestones that represent phases of work:

| Milestone | Goal | Due |
|-----------|------|-----|
| **Evidence Collection** | Accumulate 100+ trades at 0.35% risk to reach the strategy review gate | ✅ 104 trades reached 2026-08-16; gate run, 2/3 automated criteria passed |
| **Audit Readiness** | Address professor feedback: dependency scanning, SBOM, containerization decisions, workflow improvements | Sep 15 |

A milestone is *done* when every issue in it is closed. Closing an issue means the work is merged, verified, and the reasoning documented.

## Branches

For any non-trivial change, work happens on a **feature branch**, not directly on `main`:

```
main ── feat/add-issues-workflow-docs ── main
```

Branch naming convention: `feat/<short-description>` or `fix/<short-description>`.

- `feat/` — new functionality or documentation
- `fix/` — bug fixes

## Pull Requests

Feature branches are merged back to `main` via a **Pull Request**. A PR must have:

1. A title describing what changed
2. A description explaining *what* and *why* (mirrors the issue it resolves)
3. A reference to the issue it closes (`Closes #N`)

This gives every merge a reviewable record — even for solo work, it forces a second look at the change before it lands on `main`.

## Why This Matters

The repository should demonstrate how software is actually developed, not just the final state of the code:

- Issues show **what's being worked on and why**
- Milestones show **the plan and progress**
- Branches and PRs show **that changes are reviewed before landing**

This is the difference between "a repo with code" and "a repo that shows how a developer works on a team."
