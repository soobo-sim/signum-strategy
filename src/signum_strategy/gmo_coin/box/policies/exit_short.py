
"""BoxShortExit — 박스 숏 청산 정책.

박스 전략의 숏 포지션 청산 조건:
  1. 박스 이탈 (box invalidation): check_box_invalidation 판정 시 즉시 청산
  2. 목표가 도달: 현재가가 box_lower 근처 (near_lower) 도달 시 청산
  3. 박스 자체 미감지: box_detected=False 상태 지속 시 청산

추세 전략의 EMA/RSI 기반 청산은 박스 전략에 적용하지 않는다.
"""

from __future__ import annotations

from signum_strategy.gmo_coin.box.policies._exit_base import BoxExitBase


class BoxShortExit(BoxExitBase):
    """박스 숏 포지션 청산 정책. side='sell'."""

    def _target_location(self) -> str:
        return "near_lower"

    def _target_reason(self) -> str:
        return "box_short_target_reached"

    def _target_trigger(self, box_upper: float, box_lower: float) -> tuple[str, float]:
        return ("box_lower", box_lower)
