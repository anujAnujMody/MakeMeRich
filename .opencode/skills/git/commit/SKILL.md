---
name: git-commit
description: Interactive git commit with file review, code quality check, and mandatory approval gate. Use when asked to commit, stage, add files, save work, or check in changes. Shows file table (staged/unstaged/untracked), runs 5-axis code review, generates conventional commit message, and waits for explicit user approval. Never commits silently. Use before every commit.
---

# Interactive Commit

Commit with file review, code quality check, and mandatory approval gate.

## Triggers

User says: "commit", "stage", "add files", "save changes", "check in", or any request to record changes to git.

## Dependencies

Load these skills at start:
1. `git-workflow-and-versioning` — commit message format, branching rules, pre-commit checklist
2. `code-review-and-quality` — 5-axis review criteria, severity labels

## Workflow

### Step 1: Show status

Run `git status` and `git diff --stat`. Build a table of every change:

```
All changes:
  Staged:
    M src/foo.ts  (+12/-3)
  Unstaged:
    M src/bar.ts  (+5/-1)
  Untracked:
    A src/baz.ts  (+34/-0)
```

### Step 2: Ask scope

"Which files should I stage?"

Options in order of recommendation:
1. **All** — stage everything (default choice, hit Enter)
2. **Modified only** — skip untracked (new files)
3. **Partial** — user specifies paths or glob patterns (`src/services/*`, `src/hooks/use*.ts`)
4. **Cancel** — no-op

Stage the selected files with `git add <paths...>`.

### Step 3: Code review

Run review on the staged diff against the 5 axes from `code-review-and-quality`:

| Axis | Check |
|------|-------|
| Correctness | TypeScript errors? Edge cases? Runtime crashes? |
| Readability | Clear names? Consistent style? Dead code? |
| Architecture | Separation of concerns? Proper layer (service/hook/page)? |
| Security | Secrets in diff? XSS? Missing validation? |
| Performance | Bundle impact? Unnecessary re-renders? Large file changes? |

Print findings with severity labels: **Blocking** | **Optional** | **Nit**.

If any blocking issues found: **Stop and report.** Do not proceed to commit until user addresses or explicitly overrides.

### Step 4: Generate commit message

Analyze the diff to determine:
- **Type**: `feat:` (new feature), `fix:` (bug fix), `refactor:` (restructure), `chore:` (tooling/deps), `style:` (formatting), `docs:` (documentation)
- **Scope**: Which subsystem changed (e.g., `opencode`, `dashboard`, `strategies`, `engine`, `deps`)
- **Short description**: Imperative mood, ≤50 chars, what the change does
- **Body**: Bullet list of key changes (if >1 file or complex)

Format:
```
<type>(<scope>): <short description>

- <change detail>
- <change detail>
```

### Step 5: Render approval board

```
╔═══ Commit Approval ═════════════════════════════════╗
║                                                     ║
║  Files to commit (4):                               ║
║  ┌─────────────────────────────────────────────┐   ║
║  │ M src/strategies/orbs.py     (+24/-3)      │   ║
║  │ A src/hooks/useMarketData.ts (+86/-0)      │   ║
║  │ A .opencode/skills/git/...   (+55/-0)      │   ║
║  │ M .opencode/AGENTS.md        (+12/-3)      │   ║
║  └─────────────────────────────────────────────┘   ║
║                                                     ║
║  Code review: 0 blocking, 1 nit                     ║
║  Size: 177 lines (OK, < 300)                        ║
║                                                     ║
║  Proposed message:                                  ║
║  feat(strategies): add ORB engine with              ║
║  multi-instrument config                            ║
║                                                     ║
║  - Opening Range Breakout for NFO/BFO               ║
║  - Config per instrument with range/window params   ║
║  - OpenAlgo REST integration for order placement    ║
║                                                     ║
║  [Y] Commit  [E] Edit message                       ║
║  [S] Change files  [N] Cancel                       ║
╚═════════════════════════════════════════════════════╝
```

### Step 6: Execute on approval

| User input | Action |
|------------|--------|
| `Y` or Enter | `git commit -m "<type>(<scope>): <desc>" -m "<body>"` |
| `E` | Ask user for their message → use that → show updated board → wait for Y again |
| `S` | Go back to Step 2 (re-select files) |
| `N` | `git restore --staged .` → abort |

After commit: offer to run `yarn build` or `yarn typecheck`.

## Key Rules

1. **Never commit without approval board being shown and Y received.** No silent commits.
2. **Always show file table** with +/- counts. Never say "some files changed" without specifics.
3. **Split suggestion** if diff > 300 lines: show breakdown and ask if user wants to split into multiple commits.
4. **Blocking review findings halt commit** until user addresses or overrides with "commit anyway".
5. **Staging is separate from committing.** Stage in Step 2, commit in Step 6. Give user chance to review staged content.
6. **If nothing to commit** (no changes), say so and stop. Don't create empty commits.

## Partial Selection Format

User can specify:
- Space-separated paths: `src/foo.ts src/bar.ts`
- Glob patterns: `src/hooks/* src/services/*.ts`
- Keywords: `all`, `modified`, `new`
- Negation: `all !src/data/*`
