---
name: git-push
description: Pre-push safety checks with confirmation gate. Use when asked to push, upload, or submit changes to remote. Shows outgoing commits list, checks branch protection (warns on main/master), checks ahead/behind status, requires user confirmation. Never force-pushes without explicit user request.
---

# Interactive Push

Pre-push safety checks with confirmation gate.

## Triggers

User says: "push", "upload", "push changes", "git push".

## Dependencies

Load at start: `git-workflow-and-versioning` — for branch naming conventions and workflow rules.

## Workflow

### Step 1: Check current branch

Run `git branch --show-current`. Print branch name prominently.

```
Current branch: feat/auth
```

### Step 2: Check upstream

Run `git rev-parse --abbrev-ref @{upstream}` to verify remote tracking branch exists.

If no upstream: set it automatically (`git push -u origin <branch>`) but warn user.

### Step 3: Show outgoing commits

Run `git log --oneline @{upstream}..HEAD`. Print each commit with type prefix colored:

```
3 commits to push:
  ✓ 2b6de32 feat: add architecture skill with anti-patterns
  ✓ a1b2c3d refactor: extract service layer from pages
  ✓ f8e7d6c fix: handle null in login edge case
```

### Step 4: Branch protection check

| Branch | Action |
|--------|--------|
| `main` / `master` | **WARN** — pushing to main directly. Show red banner. Require explicit "yes, push to main" confirmation |
| `feat/*` / `fix/*` | Normal flow |
| Any other | OK |

### Step 5: Ahead/behind check

Run `git rev-list --count --left-right @{upstream}...HEAD`. Show ahead/behind counts.

```
Ahead: 3, Behind: 0  (fast-forward push)
```

If behind remote: **WARN** — suggest `git pull --rebase` first.

### Step 6: Approval board

```
╔═══ Push Approval ═════════════════════════════════╗
║                                                    ║
║  Branch:  feat/scaffolding                         ║
║  Remote:  origin/feat/scaffolding                  ║
║  Ahead:   3 commits                                ║
║  Behind:  0                                        ║
║                                                    ║
║  Commits:                                          ║
║  • 2b6de32 feat: add .opencode/ structure          ║
║  • a1b2c3d feat: add git commit skill              ║
║  • f8e7d6c chore: scaffold dashboard               ║
║                                                    ║
║  [Y] Push  [N] Cancel  [D] Full diff preview       ║
╚════════════════════════════════════════════════════╝
```

### Step 7: Execute

| Input | Action |
|-------|--------|
| `Y` | `git push` (or `git push -u origin <branch>` if no upstream) |
| `N` | Abort |
| `D` | Show `git diff @{upstream}..HEAD` → return to approval board |

On success: optionally offer "Create a PR for this branch?" (links to `pr/SKILL.md`).

## Key Rules

1. **Never push without showing the outgoing commits list.** No "pushing X commits" without details.
2. **Always warn on main/master push.** Require extra confirmation.
3. **Never force push** unless user explicitly types `--force` or `--force-with-lease`.
4. **If behind remote:** suggest pull/rebase before push. Do not push without resolving.
5. **No upstream** = set automatically with `-u` flag (one-time, warn user).
