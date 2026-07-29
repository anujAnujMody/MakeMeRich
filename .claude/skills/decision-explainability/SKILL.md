---
name: decision-explainability
description: Component and data patterns for showing WHY an automated trading decision happened or didn't — decision cards, condition lists, pipeline stages, and status timelines. Use when building any UI that shows the bot's reasoning, skipped signals, or cycle-by-cycle activity.
source: authored for this project, synthesized from GitHub Actions / Composer.trade / QuantConnect patterns
---

# Decision Explainability

Every competing trading product shows *what happened* (fills, P&L). None shows *what was checked
and why it didn't fire*. That gap is this product's core differentiator for a beginner user — they
need to trust the bot, and trust comes from seeing its reasoning, not just its outcomes.

## The pattern, synthesized from three references

1. **GitHub Actions' `Evaluating → Expanded → Result` triple** — when a workflow step is skipped,
   GitHub shows the condition source, the condition with real runtime values substituted in, and
   the boolean result. Steal this triple exactly: `condition → condition with real numbers → pass/fail`.
2. **Composer.trade's rule tree** — the same tree used to author a strategy is reused to show
   *today's evaluation*, with each node colored by whether it passed. Author view and evaluation
   view are the same structure, not two different UIs.
3. **QuantConnect's Insights model** — every signal, traded or not, is a persisted, queryable
   record (not a log line). This is a data-model decision as much as a UI one: the backend must
   emit a structured "evaluation" object per cycle, not free-text logs.

## Data shape (backend must emit this — do not synthesize it client-side)

```typescript
interface CycleEvaluation {
  id: string
  timestamp: string          // ISO, cycle start
  strategy: string
  instrument: string
  verdict: 'traded' | 'skipped' | 'error'
  reason: string              // one plain-English sentence, always present
  conditions: ConditionResult[]
}

interface ConditionResult {
  label: string                // "Volume above average"
  required: string             // "≥ 1.5x avg" — human-readable, not raw operator
  actual: string                // "0.82x" — the REAL value at evaluation time
  passed: boolean
  evaluated: boolean            // false if short-circuited (later conditions greyed, not hidden)
}
```

If the backend can't produce `actual` for a condition, that condition should not exist — an
explainability UI that shows fake or approximate "actual" values is worse than not showing the
condition at all (it teaches the user to distrust the whole feed).

## Components

### `DecisionCard` (compound component — see `vercel-composition-patterns`)

One per cycle evaluation. Collapsed by default:

```
● 10:05:32  SENSEX · ORB · Skipped — volume filter (needed 1.5x, got 0.82x)     [▾]
```

Expanded, shows the full `ConditionList`. Use a compound component, not boolean props
(`showExpanded`, `showError`, `showTraded` is exactly the anti-pattern
`vercel-composition-patterns` warns about) — instead:

```tsx
<DecisionCard.Root verdict={evaluation.verdict}>
  <DecisionCard.Summary>{evaluation.reason}</DecisionCard.Summary>
  <DecisionCard.Conditions items={evaluation.conditions} />
</DecisionCard.Root>
```

Verdict badge colors are **status colors** (good/warning/critical from the design system), never
raw gain/loss red-green — a skipped trade is not a loss, don't paint it red. Pair every status
badge with an icon and a text label — never color alone (see the colorblind requirements below).

### `ConditionList`

Rows of `label | required | actual | state`. Tabular numerals on `required`/`actual`. Short-
circuited conditions (evaluated: false) render at reduced opacity with a "not reached" state — they
are neither pass nor fail, and must look visibly different from both.

```
Volume above average       ≥ 1.5x avg        0.82x         ✗
Range width in bounds      20–200 pts        140 pts       ✓
Daily loss limit           not breached      (not reached)   ⋯
```

### `PipelineStrip`

The *current* cycle's live progress, 5 stages: `fetch → analyze → risk → decide → act`. Each stage
is a segment colored by outcome (pending/active/done/error), with duration once complete. This is
the "is it alive right now" companion to the DecisionCard timeline of *past* cycles.

### `CycleTimeline`

Reverse-chronological feed of `DecisionCard`s, day-grouped, with filter chips: All / Traded /
Skipped / Errors. Never reorder existing rows on a live tick — only prepend new ones.

### `RunSummaryBanner`

A daily rollup at the top of the timeline: "3 signals evaluated, 1 traded, 2 skipped (volume ×1,
daily loss cap ×1)." Mirrors GitHub Actions' step-summary pattern — the reader should be able to
understand the whole day from this one line before scrolling into individual cards.

## Freshness and live-feel (see also the design/UX notes in the project plan)

- `BotStatusStrip`: exactly the vocabulary `Live` / `Stale` / `Paused` — these are pre-tested,
  non-technical terms, don't invent synonyms.
- Show `as of HH:MM:SS`, ticking client-side between polls so the UI never looks frozen.
- Diff-and-flash only the cells that changed (~600ms decay) — never re-flash the whole row.
- Skeleton loaders only on cold load; keep stale data visible-but-dimmed on a refetch, don't blank
  to a skeleton every poll.

## Accessibility — non-negotiable for this component set

- Every verdict badge: icon + text label + color, never color alone.
- `ConditionList` pass/fail: `✓`/`✗` glyphs plus color, and the row background must clear 3:1
  contrast in both themes even for the "not reached" state.
- Provide a colorblind-mode toggle that swaps status hues to the blue/orange pair — tokenize
  `--status-good`/`--status-warning`/`--status-critical` so this is a variable swap, not a rewrite.

## Testing (TDD — write these first)

- Renders the one-line summary for each verdict type.
- Expands to show the full condition list on click/keyboard.
- Short-circuited conditions render distinctly from pass and from fail.
- No condition renders without both `required` and `actual` present.
- Timeline never reorders on a live update; only prepends.
- Status badges pass an automated contrast check in both themes.
