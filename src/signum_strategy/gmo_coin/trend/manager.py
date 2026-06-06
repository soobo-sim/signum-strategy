
"""
GmoCoinTrendManager — GMO Coin 레버리지 추세추종 매니저.

GmoCoinBaseManager 상속. 추세추종 시그널 타입만 선언.
GMO Coin 고유 주문 실행 로직(open/close/trailing/losscut 등)은
GmoCoinBaseManager에서 상속.

상속 체인:
    BaseStrategyManager → MarginBaseManager → GmoCoinBaseManager → GmoCoinTrendManager

GmoCoinBoxManager와 형제 관계:
    GmoCoinBaseManager
        ├── GmoCoinTrendManager   (trend_following)
        └── GmoCoinBoxManager     (box_mean_reversion)
"""
from __future__ import annotations

from core.strategy.managers.gmo_coin_base import GmoCoinBaseManager


class GmoCoinTrendManager(GmoCoinBaseManager):
    """GMO Coin 레버리지 추세추종 매니저. 롱/숏 양방향."""

    _task_prefix = "gmoc_trend"
    _log_prefix = "[TrendMgr]"
    # _supports_short = True — GmoCoinBaseManager에서 상속

    def _get_strategy_type(self) -> str:
        return "trend_following"

    def _make_regime_classifier(self):
        """추세추종 전략 정책."""
        from signum_strategy.gmo_coin.shared.regime.classifier import GateRegimeClassifier
        from signum_strategy.gmo_coin.trend.policies import TrendLongStop, TrendShortStop
        from signum_strategy.gmo_coin.trend.policies import TrendLongExit, TrendShortExit
        from signum_strategy.gmo_coin.trend.policies import TrendLongProfit, TrendShortProfit
        return GateRegimeClassifier(
            regime_gate=None,
            manager_type="trend_following",
            stop_long=TrendLongStop(),
            stop_short=TrendShortStop(),
            exit_long=TrendLongExit(),
            exit_short=TrendShortExit(),
            profit_long=TrendLongProfit(),
            profit_short=TrendShortProfit(),
        )

    def _valid_entry_signals(self) -> frozenset[str]:
        return frozenset({"long_entry", "short_entry", "entry_preview"})

    def _build_record_kwargs(self, **kwargs) -> dict:
        """추세추종 포지션 DB 컬럼 매핑."""
        return {
            "entry_order_id": kwargs.get("order_id"),
            "entry_price": kwargs["price"],
            "entry_size": kwargs["size"],
            "entry_collateral_jpy": round(kwargs.get("collateral_jpy", 0), 2),
            "stop_loss_price": kwargs.get("stop_loss_price"),
            "strategy_id": kwargs.get("strategy_id"),
            "trend_strategy_id": self._trend_strategy_id,
            "box_strategy_id": self._box_strategy_id,
        }

    def _ws_ema_direction_ok(self, pair: str, signal: str, signal_data: dict) -> bool:
        """추세 전략: WS 실시간가가 EMA와 방향 모순이면 False 반환 → 진입 차단.

        숏 신호(short_entry) — WS가 EMA 위: 실시간가는 이미 EMA 위 → 즉시 ema_above_ws 청산 예정
        롱 신호(long_entry)  — WS가 EMA 아래: 실시간가는 이미 EMA 아래 → 즉시 price_below_ema_ws 청산 예정

        ws_price 또는 ema가 없으면(WS 미수신·재시작 직후) True 반환 — 차단하지 않는다.
        """
        ws_price = self._latest_price.get(pair)
        ema = signal_data.get("ema")
        if not ws_price or not ema or ema <= 0:
            return True  # 데이터 부재 → 폴백, 차단 안 함
        if signal == "short_entry" and ws_price > ema:
            return False
        if signal == "long_entry" and ws_price < ema:
            return False
        return True

