
"""TrendStopBase — 추세 전략 SL 공통 골격 (ABC).

OOP_STRATEGY_REFACTOR.md §2.2 참조.

서브클래스: TrendLongStop / TrendShortStop.
박스 체제: BoxLongStop / BoxShortStop (현재 GMO Coin 운영 범위 밖 — 추후 추가).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.strategy.contracts.regime import SignalSnapshot
    from core.strategy.contracts.stop import StopUpdate
    from core.strategy.contracts.types import Position


class TrendStopBase(ABC):
    """추세 전략 SL 공통 골격.

    ``trail()`` 내부에서 기존 ``compute_adaptive_trailing_mult`` /
    ``compute_profit_based_mult`` / ``compute_structural_sl_mult`` 을 그대로 위임한다.
    기존 ``_update_trailing_stop`` 메서드와 동등 출력을 보장하는 thin wrapper.
    """

    # ── 서브클래스가 구현하는 방향 전용 메서드 ───────────────────

    @abstractmethod
    def _direction(self) -> str:
        """``"buy"`` | ``"sell"``."""
        ...

    @abstractmethod
    def _new_sl_from_mult(self, price: float, atr: float, mult: float) -> float:
        """방향별 SL 계산: 롱 = price - atr*mult, 숏 = price + atr*mult."""
        ...

    @abstractmethod
    def _is_ratchet_better(self, new_sl: float, current_sl: float) -> bool:
        """래칫 방향: 롱 = 더 높으면 Better, 숏 = 더 낮으면 Better."""
        ...

    @abstractmethod
    def _breakeven_apply(self, new_sl: float, entry: float) -> float:
        """손익분기 floor/ceiling 적용: 롱 = max(new_sl, entry), 숏 = min(new_sl, entry)."""
        ...

    # ── 공통 구현 ────────────────────────────────────────────────

    def initial(self, entry: Decimal, atr: Decimal, params: dict) -> Decimal:
        """진입 직후 최초 SL = entry ± atr × atr_multiplier_stop."""
        mult = float(params.get("atr_multiplier_stop", 2.0))
        raw = self._new_sl_from_mult(float(entry), float(atr), mult)
        return Decimal(str(round(raw, 6)))

    def trail(
        self,
        pos: "Position",
        snapshot: "SignalSnapshot",
    ) -> "StopUpdate | None":
        """적응형 트레일링 SL ratchet — 기존 ``_update_trailing_stop`` 동등 출력."""
        from core.shared.signals import (
            compute_adaptive_trailing_mult,
            compute_profit_based_mult,
            compute_structural_sl_mult,
        )
        from core.strategy.contracts.stop import StopUpdate

        params = snapshot.params
        atr = snapshot.atr
        if atr is None or atr == 0:
            return None

        price = snapshot.current_price
        entry = float(pos.entry_price) if pos.entry_price else 0.0
        side = self._direction()

        profit_mult = compute_profit_based_mult(entry, price, atr, params, side=side)

        if pos.stop_tightened:
            # tighten 후에는 구조적 SL을 ceiling으로 — trailing이 덮어쓰지 않도록
            candles = list(snapshot.candles) if snapshot.candles else []
            tighten_ceiling = compute_structural_sl_mult(candles, price, atr, params, side=side)
            mult = min(tighten_ceiling, profit_mult)
            reason = "trail_structural_ceiling"
        else:
            adaptive_mult = compute_adaptive_trailing_mult(snapshot.ema_slope_pct, snapshot.rsi, params)
            mult = min(adaptive_mult, profit_mult)
            reason = "trail_adaptive"

        new_sl = round(self._new_sl_from_mult(price, atr, mult), 6)

        # 손익분기 바닥
        breakeven_trigger = float(params.get("breakeven_trigger_atr", 1.0))
        if entry > 0:
            unrealized = (entry - price) if side == "sell" else (price - entry)
            if unrealized >= atr * breakeven_trigger:
                new_sl = self._breakeven_apply(new_sl, entry)

        current_sl = float(pos.stop_loss_price) if pos.stop_loss_price is not None else None
        if current_sl is not None and not self._is_ratchet_better(new_sl, current_sl):
            return None  # ratchet: 나쁜 방향 갱신 없음

        return StopUpdate(
            new_sl=Decimal(str(new_sl)),
            reason=reason,
            mult=mult,
            structural=pos.stop_tightened,
        )

    def tighten(
        self,
        pos: "Position",
        snapshot: "SignalSnapshot",
        reason: str,
    ) -> "StopUpdate":
        """구조적 SL 타이트닝 — 기존 ``_apply_stop_tightening`` 동등 출력."""
        from core.shared.signals import compute_structural_sl_mult
        from core.strategy.contracts.stop import StopUpdate

        params = snapshot.params
        atr = snapshot.atr or 1.0
        price = snapshot.current_price
        side = self._direction()
        candles = list(snapshot.candles)

        mult = compute_structural_sl_mult(candles, price, atr, params, side=side)
        new_sl = round(self._new_sl_from_mult(price, atr, mult), 6)

        return StopUpdate(
            new_sl=Decimal(str(new_sl)),
            reason=reason,
            mult=mult,
            structural=True,
        )

    def is_triggered(
        self,
        pos: "Position",
        price: Decimal,
        sl: Decimal,
    ) -> bool:
        """WS 스탑로스 모니터용. 방향별 hit 판정."""
        if self._direction() == "buy":
            return price <= sl
        return price >= sl
