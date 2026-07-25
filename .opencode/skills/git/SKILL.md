---
name: git
description: Git operations — commit, push, PR, branch, merge, rebase, stash, checkout, log, diff, status. Trigger on ANY git-related request. Interactive commit workflow shows file table + code review + approval gate. Push adds safety checks + confirmation. PR creates structured body from commits. Mandatory user approval before ANY git mutation. Use before every commit, push, or PR.
---

# Git Operations Suite

Parent dispatcher for git sub-skills. Routes git-related requests to the appropriate sub-skill.

## Intent Routing

| User says | Load sub-skill |
|-----------|---------------|
| "commit", "stage", "add files", "save changes" | `commit/SKILL.md` |
| "push", "upload" | `push/SKILL.md` |
| "pr", "pull request", "create PR" | `pr/SKILL.md` |

## Quick Reference

- **`commit/`** — Interactive commit with file table + code review + approval gate
- **`push/`** — Pre-push safety checks + confirmation
- **`pr/`** — Structured PR creation with body generation

## Rules

- All sub-skills require explicit user approval before executing any git mutation
- No silent staging, committing, pushing, or PR creation
- Always show what will change before asking for permission
