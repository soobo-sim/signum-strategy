
"""RegimeContext 구현체 — 체제별 정책 팩토리.

OOP_STRATEGY_REFACTOR.md §2.1 참조.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.strategy.contracts.types import Side

if TYPE_CHECKING:
    from core.strategy.contracts.exit import ExitPolicy, ProfitProtectionPolicy
    from core.strategy.contracts.stop import StopStrategy


@dataclass(frozen=True)
class TrendingContext:
    """추세 체제 컨텍스트. ``allows_entry`` = RegimeGate 위임 결과."""

    name: str = "trending"
    _allows: bool = True
    _stop_long: "StopStrategy | None" = field(default=None, compare=False, repr=False)
    _stop_short: "StopStrategy | None" = field(default=None, compare=False, repr=False)
    _exit_long: "ExitPolicy | None" = field(default=None, compare=False, repr=False)
    _exit_short: "ExitPolicy | None" = field(default=None, compare=False, repr=False)
    _profit_long: "ProfitProtectionPolicy | None" = field(default=None, compare=False, repr=False)
    _profit_short: "ProfitProtectionPolicy | None" = field(default=None, compare=False, repr=False)

    def allows_entry(self) -> bool:  # noqa: D102
        return self._allows

    def stop_strategy(self, side: Side) -> "StopStrategy":
        impl = self._stop_long if side == "buy" else self._stop_short
        if impl is None:
            raise NotImplementedError(
                f"TrendingContext: stop_strategy({side!r}) not injected. "
                "Wire via GateRegimeClassifier after Step 4."
            )
        return impl

    def exit_policy(self, side: Side) -> "ExitPolicy":
        impl = self._exit_long if side == "buy" else self._exit_short
        if impl is None:
            raise NotImplementedError(
                f"TrendingContext: exit_policy({side!r}) not injected. "
                "Wire via GateRegimeClassifier after Step 5."
            )
        return impl

    def profit_protection(self, side: Side) -> "ProfitProtectionPolicy":
        impl = self._profit_long if side == "buy" else self._profit_short
        if impl is None:
            raise NotImplementedError(
                f"TrendingContext: profit_protection({side!r}) not injected. "
                "Wire via GateRegimeClassifier after Step 5."
            )
        return impl


@dataclass(frozen=True)
class RangingContext:
    """횡보(박스) 체제 컨텍스트."""

    name: str = "ranging"
    _allows: bool = True
    _stop_long: "StopStrategy | None" = field(default=None, compare=False, repr=False)
    _stop_short: "StopStrategy | None" = field(default=None, compare=False, repr=False)
    _exit_long: "ExitPolicy | None" = field(default=None, compare=False, repr=False)
    _exit_short: "ExitPolicy | None" = field(default=None, compare=False, repr=False)
    _profit_long: "ProfitProtectionPolicy | None" = field(default=None, compare=False, repr=False)
    _profit_short: "ProfitProtectionPolicy | None" = field(default=None, compare=False, repr=False)

    def allows_entry(self) -> bool:  # noqa: D102
        return self._allows

    def stop_strategy(self, side: Side) -> "StopStrategy":
        impl = self._stop_long if side == "buy" else self._stop_short
        if impl is None:
            raise NotImplementedError(
                f"RangingContext: stop_strategy({side!r}) not injected."
            )
        return impl

    def exit_policy(self, side: Side) -> "ExitPolicy":
        impl = self._exit_long if side == "buy" else self._exit_short
        if impl is None:
            raise NotImplementedError(
                f"RangingContext: exit_policy({side!r}) not injected."
            )
        return impl

    def profit_protection(self, side: Side) -> "ProfitProtectionPolicy":
        impl = self._profit_long if side == "buy" else self._profit_short
        if impl is None:
            raise NotImplementedError(
                f"RangingContext: profit_protection({side!r}) not injected."
            )
        return impl


@dataclass(frozen=True)
class UnclearContext:
    """불명확 체제 컨텍스트. ``allows_entry`` = False.

    진입 차단 체제 — 정책 호출 시 NotImplementedError.
    loop.py는 try/except로 감싸 기존 값(signal_data)을 유지한다.
    """

    name: str = "unclear"

    def allows_entry(self) -> bool:  # noqa: D102
        return False

    def stop_strategy(self, side: Side) -> "StopStrategy":  # noqa: D102
        raise NotImplementedError("UnclearContext: stop_strategy는 진입 차단 체제에서 호출 금지.")

    def exit_policy(self, side: Side) -> "ExitPolicy":  # noqa: D102
        raise NotImplementedError("UnclearContext: exit_policy는 진입 차단 체제에서 호출 금지.")

    def profit_protection(self, side: Side) -> "ProfitProtectionPolicy":  # noqa: D102
        raise NotImplementedError("UnclearContext: profit_protection은 진입 차단 체제에서 호출 금지.")
