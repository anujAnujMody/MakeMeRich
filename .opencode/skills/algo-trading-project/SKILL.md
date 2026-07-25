---
name: algo-trading-project
description: Project-specific knowledge for the Algo Trading Tool. Contains broker config, instrument list, strategy registry, risk rules, and architecture conventions. Use when implementing strategies, configuring instruments, or making architecture decisions.
---

# Algo Trading Project

Project-specific knowledge for the Bank Nifty options trading tool.

## Broker Setup

- **Primary:** Angel One SmartAPI via OpenAlgo
- **Switch:** Change `BROKER` env var in OpenAlgo config. Zero code changes to our dashboard.
- **OpenAlgo REST API:** All communication goes through OpenAlgo's 57 REST endpoints. No direct broker API calls.
- **Rate limits:** 9 req/sec (Angel), 10 req/sec (Zerodha)

## Supported Brokers (via OpenAlgo)

AngelOne, Zerodha, Dhan, Fyers, Upstox, Shoonya, 5paisa, AliceBlue, HDFC Sky, Kotak Neo, Motilal Oswal, +22 more

## Strategy Registry

```python
STRATEGIES = {
    "orbs": {
        "instruments": [
            {"symbol": "BANKNIFTY", "exchange": "NFO", "range_min": 15, "max_trades": 2},
            {"symbol": "NIFTY",    "exchange": "NFO", "range_min": 15, "max_trades": 2},
            {"symbol": "FINNIFTY", "exchange": "NFO", "range_min": 20, "max_trades": 1},
            {"symbol": "SENSEX",   "exchange": "BFO", "range_min": 15, "max_trades": 2},
        ],
        "params": {"target_rr": 2.0, "sl_rr": 1.0, "max_hold_min": 105},
    }
}
```

## Risk Rules

- Max loss/day: ₹300 (3% of ₹10K capital)
- Max loss/trade: ₹150 (1.5%)
- Max trades/day: 2 per instrument
- No positions past 11:00 AM IST
- No Fridays near monthly expiry
- No news-event days (budget, RBI policy)

## Architecture

```
UI → Store (Zustand) → Hooks (TanStack Query) → API (OpenAlgo REST)
```

- `dashboard/` — React 19 + shadcn/ui + Zustand + TanStack Query
- `strategies/` — Python strategy engine (runs inside OpenAlgo strategy host)
- `engine/` — Python executor + scheduler + self-learning ML
