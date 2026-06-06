
"""BoxShortProfit — 박스 숏 이익확보 정책.

박스 숏에서 이익확보 트리거:
  - 가격이 박스 중앙선을 통과(아래로)하면 → SL을 entry(breakeven)로 tighten

full_exit은 BoxShortExit 담당. 이 클래스는 tighten만 담당한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.strategy.contracts.exit import ExitDecision
    from core.strategy.contracts.regime import SignalSnapshot
    from core.strategy.contracts.types import Position


class BoxShortProfit:
    """박스 숏 이익확보 정책. side='sell'."""

    def evaluate(
        self,
        pos: "Position",
        snapshot: "SignalSnapshot",
    ) -> "ExitDecision":
        """중앙선 통과 시 tighten 신호 반환."""
        from core.strategy.contracts.exit import ExitDecision

        box_upper = snapshot.box_upper
        box_lower = snapshot.box_lower
        price = snapshot.current_price

        if box_upper is None or box_lower is None:
            return ExitDecision(action="hold", reason="box_profit_no_bounds")

        midpoint = (box_upper + box_lower) / 2.0

        # 가격이 중앙선 아래로 내려간 상태 + 아직 stop_tightened 아님
        if price < midpoint and not pos.stop_tightened:
            return ExitDecision(
                action="tighten",
                reason="box_short_midpoint_crossed",
                triggers={"midpoint": midpoint, "price": price},
            )

        return ExitDecision(action="hold", reason="box_short_profit_hold")
