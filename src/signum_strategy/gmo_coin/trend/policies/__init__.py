from signum_strategy.gmo_coin.trend.policies.stop_long import TrendLongStop
from signum_strategy.gmo_coin.trend.policies.stop_short import TrendShortStop
from signum_strategy.gmo_coin.trend.policies.exit_long import TrendLongExit
from signum_strategy.gmo_coin.trend.policies.exit_short import TrendShortExit
from signum_strategy.gmo_coin.trend.policies.profit_long import TrendLongProfit
from signum_strategy.gmo_coin.trend.policies.profit_short import TrendShortProfit

__all__ = [
    "TrendLongStop", "TrendShortStop",
    "TrendLongExit", "TrendShortExit",
    "TrendLongProfit", "TrendShortProfit",
]
