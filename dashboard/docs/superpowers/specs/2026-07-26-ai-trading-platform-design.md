# AI-First Trading Platform Design

**Date:** Jul 26, 2026  
**Market:** Indian F&O (NFO/BFO) — Nifty, BankNifty, FinNifty, Sensex options  
**Risk:** Conservative — paper first, then ₹10-20K, capital preservation first  
**Autonomy:** Toggle between semi-auto (approval) and full-auto

## 1. Architecture — 7 AI Agents

```
  0. Research Agent   → OpenRouter LLM @ 9AM   → Market context brief
  1. Scanner Agent    → numpy/pandas each bar  → Live market state
  2. Discovery Agent  → XGBoost overnight      → Pattern detection
  3. Validator Agent  → Statistics post-disco  → Overfit check
  4. Risk Manager     → Rule engine pre-trade  → Sizing + veto
  5. Execution Agent  → OpenAlgo API           → Order placement
  6. Learning Agent   → OpenRouter LLM post-cl → Explanations + updates
```

### Model Config (Settings)

```yaml
reasoning_model: "anthropic/claude-sonnet-4.6"
cheap_model: "openai/gpt-5-mini"
fallback_model: "deepseek/deepseek-v4-flash"
```

## 2. Pages

### Dashboard — Newbie Command Center
- Today's Outlook card (red/yellow/green + plain English + confidence %)
- Big P&L (Today + Total, green/red)
- Active strategy cards with pause toggle
- Mini stats row (trades, wins, losses, win%)

### Strategies — AI Discovery Hub
- Discovered strategies with deploy toggle
- Win rate + P&L per strategy (7d)
- Confidence score with trend arrow
- Plain English rule explanation
- Discovery queue (what's being tested)
- Auto-pause with reason

### Orders — Trade Log with ML Learning
- Calendar picker to view past days
- Daily recap (net P&L per strategy)
- AI explanation per trade (why entered, confidence at entry)
- "ML learned today" section

### Learn — Learning Dashboard
- AI skill progression (win rate trend, strategies discovered/retired)
- Pattern library (what works, what doesn't, confidence per pattern)
- Research archive (daily research briefs by date)

### Settings (NEW)
- AI Model selection (reasoning, cheap, fallback)
- Research agent toggle + schedule
- Trading mode (paper/live)
- Risk limits (max loss/day, position size, drawdown, max trades)
- Autonomy mode (full auto / semi-auto)

## 3. Data Flow

```
8:00AM → Research Agent → today's brief
8:30AM → Discovery Agent → new patterns?
9:00AM → Dashboard shows Today's Outlook
9:15AM → Scanner + Execution start
3:30PM → Market close → Learning Agent analyzes
5:00PM → Confidence updated → auto-pause if needed
```

## 4. Tech Stack
- Frontend: React + TypeScript + Tailwind + Recharts
- Backend agents: TypeScript services in same project (MVP)
- LLM: OpenRouter API (swap models anytime)
- Traditional ML: XGBoost (separate Python service, notebook-first)
- Data: MSW mocks → real broker API
