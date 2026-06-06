
"""GateRegimeClassifier — 체제 판정 단일 진실.

OOP_STRATEGY_REFACTOR.md §2.1 / 마이그레이션 단계 3 참조.

단계 3은 100% 호환 (thin wrapper):
  - ``classify_regime()`` (core/shared/signals.py) 를 그대로 위임.
  - RegimeGate.should_allow_entry() 로 진입 허용 여부를 결정.
  - 추세 정책(TrendLongStop 등)과 박스 정책(BoxLongStop 등)을 별도로 주입.
  - trending → TrendingContext, ranging → RangingContext 에 각각 연결.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from core.strategy.contracts.regime import RegimeClassifier, RegimeContext, SignalSnapshot
from signum_strategy.gmo_coin.shared.regime.contexts import (
    RangingContext,
    TrendingContext,
    UnclearContext,
)

if TYPE_CHECKING:
    from core.strategy.contracts.exit import ExitPolicy, ProfitProtectionPolicy
    from core.strategy.contracts.stop import StopStrategy

logger = logging.getLogger(__name__)


class GateRegimeClassifier:
    """``RegimeClassifier`` Protocol 구현. ``RegimeGate`` 위임으로 100% 호환.

    Args:
        regime_gate: 현재 매니저가 보유하는 ``RegimeGate`` 인스턴스 (None 허용).
        manager_type: ``"trend_following"`` | ``"box_mean_reversion"``.
        stop_long: 추세 롱 스탑 전략 (단계 4 이후 주입).
        stop_short: 추세 숏 스탑 전략 (단계 4 이후 주입).
        exit_long: 추세 롱 청산 정책 (단계 5 이후 주입).
        exit_short: 추세 숏 청산 정책 (단계 5 이후 주입).
        profit_long: 추세 롱 이익보호 정책 (단계 5 이후 주입).
        profit_short: 추세 숏 이익보호 정책 (단계 5 이후 주입).
        box_stop_long: 박스 롱 스탑 전략 (단계 4 이후 주입).
        box_stop_short: 박스 숏 스탑 전략 (단계 4 이후 주입).
        box_exit_long: 박스 롱 청산 정책 (단계 5 이후 주입).
        box_exit_short: 박스 숏 청산 정책 (단계 5 이후 주입).
        box_profit_long: 박스 롱 이익보호 정책 (단계 5 이후 주입).
        box_profit_short: 박스 숏 이익보호 정책 (단계 5 이후 주입).
    """

    def __init__(
        self,
        *,
        regime_gate: Any | None = None,
        manager_type: str = "trend_following",
        # 추세 정책
        stop_long: "StopStrategy | None" = None,
        stop_short: "StopStrategy | None" = None,
        exit_long: "ExitPolicy | None" = None,
        exit_short: "ExitPolicy | None" = None,
        profit_long: "ProfitProtectionPolicy | None" = None,
        profit_short: "ProfitProtectionPolicy | None" = None,
        # 박스 정책 (ranging 체제용)
        box_stop_long: "StopStrategy | None" = None,
        box_stop_short: "StopStrategy | None" = None,
        box_exit_long: "ExitPolicy | None" = None,
        box_exit_short: "ExitPolicy | None" = None,
        box_profit_long: "ProfitProtectionPolicy | None" = None,
        box_profit_short: "ProfitProtectionPolicy | None" = None,
    ) -> None:
        self._gate = regime_gate
        self._manager_type = manager_type
        # 추세 정책
        self._stop_long = stop_long
        self._stop_short = stop_short
        self._exit_long = exit_long
        self._exit_short = exit_short
        self._profit_long = profit_long
        self._profit_short = profit_short
        # 박스 정책
        self._box_stop_long = box_stop_long
        self._box_stop_short = box_stop_short
        self._box_exit_long = box_exit_long
        self._box_exit_short = box_exit_short
        self._box_profit_long = box_profit_long
        self._box_profit_short = box_profit_short

    def classify(self, snapshot: SignalSnapshot) -> RegimeContext:
        """``classify_regime()`` 위임 → 적절한 컨텍스트 반환.

        ``allows_entry`` 는 RegimeGate.should_allow_entry 로 결정한다.
        gate 가 None 이면 체제 자체(trending/ranging/unclear)로 결정한다.

        박스가 감지된 경우(box_upper/box_lower) 박스 폭 % 를 classify_regime 에 전달.
        ranging (b) 판정 임계값이 박스 폭 기반으로 동적 계산된다.
        """
        from core.shared.signals import classify_regime

        # 박스 폭 계산 — 박스가 감지된 경우에만 전달 (ranging 판정 임계값 동적화)
        box_width_pct: float | None = None
        if (
            snapshot.box_upper is not None
            and snapshot.box_lower is not None
            and snapshot.box_lower > 0
        ):
            box_width_pct = (snapshot.box_upper - snapshot.box_lower) / snapshot.box_lower * 100

        regime, _, _ = classify_regime(
            snapshot.bb_width_pct,
            snapshot.range_pct,
            snapshot.params,
            box_width_pct=box_width_pct,
        )

        if regime == "trending":
            if self._gate is not None:
                allows = self._gate.should_allow_entry(self._manager_type)
            else:
                allows = True
            return TrendingContext(
                _allows=allows,
                _stop_long=self._stop_long,
                _stop_short=self._stop_short,
                _exit_long=self._exit_long,
                _exit_short=self._exit_short,
                _profit_long=self._profit_long,
                _profit_short=self._profit_short,
            )

        if regime == "ranging":
            if self._gate is not None:
                allows = self._gate.should_allow_entry(self._manager_type)
            else:
                allows = True
            return RangingContext(
                _allows=allows,
                _stop_long=self._box_stop_long,
                _stop_short=self._box_stop_short,
                _exit_long=self._box_exit_long,
                _exit_short=self._box_exit_short,
                _profit_long=self._box_profit_long,
                _profit_short=self._box_profit_short,
            )

        # unclear
        return UnclearContext()

    def set_regime_gate(self, gate: Any | None) -> None:
        """매니저가 ``RegimeGate`` 를 lifespan 시점에 주입할 때 호출.

        ``__init__`` 에서는 None 으로 생성되고, ``BaseStrategyManager.set_regime_gate``
        가 동일 인스턴스를 양쪽(매니저·classifier)에 동기 주입한다.
        """
        self._gate = gate

    def update_policies(
        self,
        *,
        stop_long: "StopStrategy | None" = None,
        stop_short: "StopStrategy | None" = None,
        exit_long: "ExitPolicy | None" = None,
        exit_short: "ExitPolicy | None" = None,
        profit_long: "ProfitProtectionPolicy | None" = None,
        profit_short: "ProfitProtectionPolicy | None" = None,
        box_stop_long: "StopStrategy | None" = None,
        box_stop_short: "StopStrategy | None" = None,
        box_exit_long: "ExitPolicy | None" = None,
        box_exit_short: "ExitPolicy | None" = None,
        box_profit_long: "ProfitProtectionPolicy | None" = None,
        box_profit_short: "ProfitProtectionPolicy | None" = None,
    ) -> None:
        """정책 구현체를 단계 4~5 완료 후 한 번에 주입한다."""
        if stop_long is not None:
            self._stop_long = stop_long
        if stop_short is not None:
            self._stop_short = stop_short
        if exit_long is not None:
            self._exit_long = exit_long
        if exit_short is not None:
            self._exit_short = exit_short
        if profit_long is not None:
            self._profit_long = profit_long
        if profit_short is not None:
            self._profit_short = profit_short
        if box_stop_long is not None:
            self._box_stop_long = box_stop_long
        if box_stop_short is not None:
            self._box_stop_short = box_stop_short
        if box_exit_long is not None:
            self._box_exit_long = box_exit_long
        if box_exit_short is not None:
            self._box_exit_short = box_exit_short
        if box_profit_long is not None:
            self._box_profit_long = box_profit_long
        if box_profit_short is not None:
            self._box_profit_short = box_profit_short
