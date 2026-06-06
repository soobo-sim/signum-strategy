
"""TrendExitBase — 추세 청산 정책 공통 골격 (ABC).

OOP_STRATEGY_REFACTOR.md §2.3 참조.

기존 ``compute_exit_signal()`` 를 그대로 위임하는 thin wrapper.
서브클래스: TrendLongExit / TrendShortExit.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.strategy.contracts.exit import ExitDecision
    from core.strategy.contracts.regime import SignalSnapshot
    from core.strategy.contracts.types import Position


class TrendExitBase(ABC):
    """추세 청산 공통 골격. ``compute_exit_signal`` thin wrapper."""

    @abstractmethod
    def _side(self) -> str:
        """``"buy"`` | ``"sell"``."""
        ...

    def evaluate(
        self,
        pos: "Position",
        snapshot: "SignalSnapshot",
    ) -> "ExitDecision":
        """``compute_exit_signal`` 위임 → ExitDecision 변환."""
        from core.shared.signals import compute_exit_signal
        from core.strategy.contracts.exit import ExitDecision

        params = snapshot.params
        result = compute_exit_signal(
            ema_slope_pct=snapshot.ema_slope_pct,
            rsi=snapshot.rsi,
            atr=snapshot.atr,
            current_price=snapshot.current_price,
            entry_price=float(pos.entry_price) if pos.entry_price else None,
            params=params,
            side=self._side(),
        )

        action_raw = result.get("action", "hold")
        # compute_exit_signal returns "full_exit" | "tighten_stop" | "hold"
        # ExitAction = "full_exit" | "partial_exit" | "tighten" | "hold"
        if action_raw == "tighten_stop":
            action = "tighten"
        elif action_raw == "full_exit":
            action = "full_exit"
        else:
            action = "hold"

        return ExitDecision(
            action=action,  # type: ignore[arg-type]
            reason=result.get("reason", ""),
            triggers=result.get("triggers", {}),
        )
