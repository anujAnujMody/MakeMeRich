---
name: git-pr
description: Structured pull request creation with generated body and approval gate. Use when asked to create a PR, pull request, open PR, or submit PR. Generates title + body from commit history, shows diff summary, labels, and requires user approval before creating via gh CLI. Never creates PR without showing full content for review.
---

# Interactive PR Creation

Structured pull request with generated body and approval gate.

## Triggers

User says: "pr", "pull request", "create PR", "open PR", "submit PR", "make a PR".

## Dependencies

Load at start:
1. `github-ops` — `gh` CLI commands, PR best practices, labels
2. `code-review-and-quality` — change sizing rules, review criteria for PR body

## Prerequisites

- `gh` CLI installed and authenticated (`gh auth status`)
- Branch pushed to remote (if not, offer to push first → load `push/SKILL.md`)

## Workflow

### Step 1: Determine branch context

Run `git branch --show-current`. Determine base branch:

| Current branch | Default base | Notes |
|----------------|-------------|-------|
| `feat/*` | `main` | Feature branch |
| `fix/*` | `main` | Bug fix |
| `refactor/*` | `main` | Refactoring |
| `main` | — | **Cannot create PR from main.** Suggest checking out feature branch |

Print: "Creating PR: `feat/scaffolding` → `main`"

### Step 2: Show diff summary

Run `git diff <base>...HEAD --stat`. Print file summary.

### Step 3: Gather commits

Run `git log --oneline <base>..HEAD`. Use commit messages to build body.

### Step 4: Generate PR

**Title** — conventional commit style matching the primary change:
```
feat: add .opencode structure and scaffolding
```

**Body** — structured markdown:
```
## Summary

[1-2 sentence overview]

### Changes
- [bullet from commit 1]
- [bullet from commit 2]

### Files
| File | Change |
|------|--------|
| `.opencode/skills/.../SKILL.md` | +93 lines |

## Testing

- [ ] Build passes (`yarn build`)
- [ ] Lint passes (`yarn lint`)
- [ ] TypeScript checks (`yarn typecheck`)
```

**Labels** — suggest based on type: `feat:` → `enhancement`, `fix:` → `bug`, etc.

### Step 5: Approval board

```
╔═══ PR Approval ═══════════════════════════════════════╗
║                                                        ║
║  From:  feat/scaffolding                               ║
║  To:    main                                           ║
║                                                        ║
║  Title: feat: add .opencode structure + scaffolding     ║
║                                                        ║
║  [Y] Create PR  [E] Edit body  [T] Edit title          ║
║  [B] Change base  [L] Add labels  [N] Cancel           ║
╚════════════════════════════════════════════════════════╝
```

### Step 6: Execute

| Input | Action |
|-------|--------|
| `Y` | `gh pr create --title "<title>" --body "<body>" --base <base> --label <labels>` |
| `E` | Open body in editor, regenerate board → wait for Y |
| `T` | Ask for new title → regenerate board → wait for Y |
| `B` | Ask for base branch → regenerate board → wait for Y |
| `L` | Suggest labels, allow user to add/remove → regenerate |
| `N` | Abort |

On success: print PR URL from `gh` output.

## Key Rules

1. **Never create PR without approval board.** Always show title + body before asking.
2. **Never create PR from main branch.** Block with clear message.
3. **Always include "Testing" section** in body with checkboxes.
4. **If branch not pushed:** offer to push first.
5. **PR body is markdown** — use proper headings, lists, code blocks.
