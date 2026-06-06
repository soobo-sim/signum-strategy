# GitHub Copilot Instructions — signum-strategy

## Repository Purpose

`signum-strategy` provides concrete trading strategy implementations for
[signum-engine](https://github.com/soobo-sim/signum-engine).

```
signum-engine  ←  IStrategy / BaseStrategyManager  ←  signum-strategy
                                                          └── GmoCoinTrendManager
                                                          └── GmoCoinBoxManager
signum-adapters  ←  GmoCoinAdapter  ←  (used by signum-strategy managers)
```

## Stack

| Layer | Technology |
|-------|-----------|
| Language | Python ≥ 3.12 |
| Strategy base | `core.strategy.managers.gmo_coin_base.GmoCoinBaseManager` (from signum-engine) |
| Exchange adapter | `signum_adapters.gmo_coin.GmoCoinAdapter` (from signum-adapters) |
| Test framework | `pytest` + `pytest-asyncio` |
| Linter | `ruff` |
| Build backend | `hatchling` |

## Directory Structure

```
signum-strategy/
├── src/
│   └── signum_strategy/
│       ├── __init__.py
│       └── gmo_coin/
│           ├── trend/
│           │   ├── manager.py           ← GmoCoinTrendManager
│           │   └── policies/
│           │       ├── stop_long.py     ← TrendLongStop
│           │       ├── stop_short.py    ← TrendShortStop
│           │       ├── exit_long.py     ← TrendLongExit
│           │       ├── exit_short.py    ← TrendShortExit
│           │       ├── profit_long.py   ← TrendLongProfit
│           │       └── profit_short.py  ← TrendShortProfit
│           ├── box/
│           │   ├── manager.py           ← GmoCoinBoxManager
│           │   └── policies/
│           │       ├── stop_long.py     ← BoxLongStop
│           │       ├── stop_short.py    ← BoxShortStop
│           │       ├── exit_long.py     ← BoxLongExit
│           │       ├── exit_short.py    ← BoxShortExit
│           │       ├── profit_long.py   ← BoxLongProfit
│           │       └── profit_short.py  ← BoxShortProfit
│           └── shared/
│               ├── guard/               ← GuardPolicy implementations
│               ├── regime/              ← GateRegimeClassifier, RegimeContext
│               └── sizing/              ← MarginSizer
├── tests/
├── .github/
│   ├── agents/
│   │   ├── implementor.agent.md
│   │   └── validator.agent.md
│   ├── copilot-instructions.md
│   ├── workflows/
│   └── PULL_REQUEST_TEMPLATE.md
├── pyproject.toml
└── README.md
```

## Key Import Patterns

```python
# Platform imports (from signum-engine dependency)
from core.strategy.managers.gmo_coin_base import GmoCoinBaseManager
from core.strategy.contracts.stop import StopStrategy
from core.strategy.contracts.exit import ExitPolicy
from core.strategy.contracts.regime import RegimeContext
from core.shared.signals import compute_trend_signal

# Internal imports (within this repo)
from signum_strategy.gmo_coin.trend.policies.stop_long import TrendLongStop
from signum_strategy.gmo_coin.shared.regime.classifier import GateRegimeClassifier
```

## OOP Rules

- One policy class per file
- `TrendLongStop` / `TrendShortStop` — never `if side == "long"` in a single class
- `BoxLongStop` / `BoxShortStop` — same principle
- All policy classes must implement the corresponding Protocol from `core.strategy.contracts.*`
