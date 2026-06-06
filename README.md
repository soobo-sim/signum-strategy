# signum-strategy

Concrete trading strategy implementations for [signum-engine](https://github.com/soobo-sim/signum-engine).

## Overview

```
signum-engine (platform)  ←  BaseStrategyManager, IStrategy Protocol
       ↑
signum-strategy (this repo)  ←  GmoCoinTrendManager, GmoCoinBoxManager, Policies
       ↑
signum-adapters  ←  GmoCoinAdapter (exchange I/O)
```

## Strategies

| Strategy | Class | Style |
|----------|-------|-------|
| GMO Coin Trend Following | `GmoCoinTrendManager` | `trend_following` |
| GMO Coin Box Mean Reversion | `GmoCoinBoxManager` | `box_mean_reversion` |

## Installation

```bash
pip install git+https://github.com/soobo-sim/signum-strategy.git@main
```

## Usage

```python
from signum_strategy.gmo_coin.trend.manager import GmoCoinTrendManager
from signum_strategy.gmo_coin.box.manager import GmoCoinBoxManager
```

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -m "not requires_api_key"
ruff check src/ tests/
```

## License

MIT