
"""BoxExitBase — 박스 역추세 전략 청산 공통 골격 (ABC).

공통 로직:
  1. 박스 미감지 (box_upper/lower=None) → 즉시 full_exit
  2. 박스 이탈 (check_box_invalidation) → 즉시 full_exit
  3. 목표가 도달 여부 → 서브클래스 위임 (_target_location)
  4. 기본: hold

서브클래스: BoxLongExit (목표=near_upper) / BoxShortExit (목표=near_lower).

참고: OOP_STRATEGY_REFACTOR.md §1 #5 — BoxExitBase → BoxLong/ShortExit.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.strategy.contracts.exit import ExitDecision
    from core.strategy.contracts.regime import SignalSnapshot
    from core.strategy.contracts.types import Position


class BoxExitBase(ABC):
    """박스 역추세 전략 청산 공통 골격."""

    # ── 서브클래스가 구현하는 방향 전용 메서드 ───────────────────

    @abstractmethod
    def _target_location(self) -> str:
        """목표가 위치: 롱='near_upper', 숏='near_lower'."""
        ...

    @abstractmethod
    def _target_reason(self) -> str:
        """목표가 도달 reason: 'box_long_target_reached' | 'box_short_target_reached'."""
        ...

    @abstractmethod
    def _target_trigger(self, box_upper: float, box_lower: float) -> tuple[str, float]:
        """목표가 trigger: (key, value).

        롱: ('box_upper', box_upper)
        숏: ('box_lower', box_lower)
        """
        ...

    # ── 공통 구현 ────────────────────────────────────────────────

    def evaluate(
        self,
        pos: "Position",
        snapshot: "SignalSnapshot",
    ) -> "ExitDecision":
        """박스 청산 조건 공통 평가 골격."""
        from core.shared.box_signals import check_box_invalidation, classify_price_in_box
        from core.strategy.contracts.exit import ExitDecision

        params = snapshot.params
        box_upper = snapshot.box_upper
        box_lower = snapshot.box_lower
        price = snapshot.current_price

        # 박스 미감지 → 청산
        if box_upper is None or box_lower is None:
            return ExitDecision(
                action="full_exit",
                reason="box_not_detected",
                triggers={"box_detected": False},
            )

        # 박스 이탈 체크 (4H 종가 기반)
        candles = list(snapshot.candles) if snapshot.candles else []
        if candles:
            # 통일된 이름 우선, 구 이름 폴백 (DB 마이그레이션 전까지 하위 호환)
            tolerance_pct = float(params.get("box_tolerance_pct", params.get("tolerance_pct", 0.5)))
            triangle_lookback = int(params.get("triangle_lookback", 20))
            highs = [float(c["high"]) if isinstance(c, dict) else float(c.high) for c in candles]
            lows = [float(c["low"]) if isinstance(c, dict) else float(c.low) for c in candles]
            invalidation = check_box_invalidation(
                check_price=price,
                candle_highs=highs,
                candle_lows=lows,
                upper=box_upper,
                lower=box_lower,
                tolerance_pct=tolerance_pct,
                triangle_lookback=triangle_lookback,
            )
            if invalidation is not None:
                return ExitDecision(
                    action="full_exit",
                    reason=f"box_invalidated:{invalidation}",
                    triggers={"invalidation": invalidation},
                )

        # 목표가 도달 체크
        near_bound_pct = float(params.get("near_bound_pct", 0.5))
        location = classify_price_in_box(
            check_price=price,
            upper=box_upper,
            lower=box_lower,
            near_bound_pct=near_bound_pct,
        )
        if location == self._target_location():
            trig_key, trig_val = self._target_trigger(box_upper, box_lower)
            return ExitDecision(
                action="full_exit",
                reason=self._target_reason(),
                triggers={"location": location, trig_key: trig_val},
            )

        return ExitDecision(action="hold", reason="box_exit_hold")
