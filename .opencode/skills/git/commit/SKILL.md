---
name: git-commit
description: Interactive git commit with 7 mandatory steps including code-simplifier pass and 5-axis code review. Use when asked to commit, stage, add files, save work, or check in changes. Shows file table (staged/unstaged/untracked), simplifies code, runs 5-axis code review, generates conventional commit message, and waits for explicit user approval. Never commits silently. Never skips simplify or review. Use before every commit.
---

# Interactive Commit

Commit with file review, code quality check, and mandatory approval gate.

## Triggers

User says: "commit", "stage", "add files", "save changes", "check in", or any request to record changes to git.

## ⚠️ HARD GATES

Every step below is mandatory. No skipping. No reordering. No "checkpoint shortcuts."

| Step | What | Consequence if skipped |
|------|------|------------------------|
| 1 | Show status | User can't see what will change |
| 2 | Stage files | Wrong files committed or secrets leaked |
| 3 | Simplify (code-simplifier) | Code goes in messy, technical debt accumulates |
| 4 | Code review (5-axis) | Bugs/security issues reach production |
| 5 | Generate commit message | Bad history, hard to revert later |
| 6 | Approval board | Silent commit — user loses control |
| 7 | Execute | (only after Y) |

**If user says "just commit" or "checkpoint" or "commit it all":** pause, explain steps 3+4 are not skippable, ask again. If user insists, note the override explicitly in the commit message body with `SkipOverride: user-requested`.

**Rationalizations (you will have these thoughts. They are traps.):**

| Thought | Truth |
|---------|-------|
| "Many files, simplify would take too long" | Break into chunks. Simplify group by group. Do not skip. |
| "User said checkpoint, they want speed" | Checkpoint still needs review. Speed comes from approval board skip, not from skipping quality steps. |
| "These are just skaffolding/test files" | Scaffolding sets patterns for all future code. Review it. |
| "I already read these files, no need to re-review" | Reading ≠ reviewing against axes. Run the axes. |
| "This change is trivial, skip to commit" | Trivial changes have the shortest diff. Run the steps — they take 30 seconds. |

## Dependencies

Load these skills at start:
1. `git-workflow-and-versioning` — commit message format, branching rules, pre-commit checklist
2. `code-review-and-quality` — 5-axis review criteria, severity labels
3. `code-simplifier` — mandatory simplification pass on changed files

## Workflow

### Step 1: Show status

Why: See exactly what will change before touching anything.

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

### Step 2: Stage files

Why: Stage the right files. Skip secrets. Let user override to commit only what they specified.

Auto-stage all changed files via `git add .`, then unstage patterns that should never be committed: `.env`, `credentials.json`, `*.local.*`, `node_modules/`, `dist/`, `.next/`, `*.log`.

If user specified paths ("commit only these files"), stage only those instead. Acceptable path formats:
- Space-separated: `src/foo.ts src/bar.ts`
- Glob: `src/hooks/* src/services/*.ts`
- Keywords: `all`, `modified`, `new`
- Negation: `all !src/data/*`

### Step 3: Simplify — HARD GATE (code-simplifier)

Why: Code that enters the repo clean stays clean. Skipping this step means every future reader pays the complexity tax.

**You MUST:**
1. Load `code-simplifier` skill via `skill` tool
2. Run simplification pass on all staged files
3. Check for: deep nesting, long functions, nested ternaries, generic names, duplicated logic, dead code
4. Apply each simplification incrementally
5. Run `yarn typecheck` or `tsc --noEmit` after each change
6. Re-stage simplified files if auto-fixed
7. Record simplifications made

**Do NOT proceed** until this step is complete.

### Step 4: Code review — HARD GATE (code-review-and-quality)

Why: Five-axis review catches what the author can't see. Skipping this means bugs land in production.

**You MUST:**
1. Load `code-review-and-quality` skill via `skill` tool
2. Review staged diff against all 5 axes
3. Print findings with severity labels: **Blocking** | **Optional** | **Nit**
4. If any blocking issues: **Stop. Do not proceed.** Report to user. Wait for fix or explicit override.
5. If user overrides a blocking finding, note it in commit body: `ReviewOverride: user-accepted <finding>`

**Do NOT proceed** until this step is complete.

### Step 5: Generate commit message

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

### Step 6: Render approval board

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

### Step 7: Execute on approval

| User input | Action |
|------------|--------|
| `Y` or Enter | `git commit -m "<type>(<scope>): <desc>" -m "<body>"` |
| `E` | Ask user for their message → use that → show updated board → wait for Y again |
| `S` | Re-stage with custom paths: go back to Step 2, user specifies which files |
| `N` | `git restore --staged .` → abort |

After commit: offer to run `yarn build` or `yarn typecheck`.

## Key Rules

1. **HARD GATES: Steps 3 (simplify) and 4 (code review) are never skippable.** Not for checkpoints. Not for small changes. Not when user says "just commit." If user insists, override goes in commit body as `SkipOverride:`.
2. **Never commit without approval board being shown and Y received.** No silent commits.
3. **Always show file table** with +/- counts. Never say "some files changed" without specifics.
4. **Split suggestion** if diff > 300 lines: show breakdown and ask if user wants to split into multiple commits.
5. **Blocking review findings halt commit** until user addresses or overrides with "commit anyway".
6. **Staging is separate from committing.** Stage in Step 2, commit in Step 7. Approval board shows exactly what will be committed.
7. **If nothing to commit** (no changes), say so and stop. Don't create empty commits.


