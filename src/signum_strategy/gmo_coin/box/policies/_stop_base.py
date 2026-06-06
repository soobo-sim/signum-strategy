
"""BoxStopBase — 박스 역추세 전략 SL 공통 골격 (ABC).

박스권 전략은 경계 기반 SL을 사용한다:
  - 롱 진입 (near_lower): SL = box_lower * (1 - cushion_pct)
  - 숏 진입 (near_upper): SL = box_upper * (1 + cushion_pct)

박스 경계 정보가 없을 경우(box_lower/box_upper=None) ATR 기반 fallback을 사용한다.

Trail 전략: 박스 전략은 경계를 이탈하지 않는 한 SL을 적게 움직인다.
  - 가격이 중앙선(midpoint)을 통과하면 breakeven(entry) 수준으로 이동
  - ATR 기반 ratchet은 추세 전략보다 느슨한 배수 사용

서브클래스: BoxLongStop / BoxShortStop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.strategy.contracts.regime import SignalSnapshot
    from core.strategy.contracts.stop import StopUpdate
    from core.strategy.contracts.types import Position


class BoxStopBase(ABC):
    """박스 역추세 전략 SL 공통 골격."""

    # ── 서브클래스가 구현하는 방향 전용 메서드 ───────────────────

    @abstractmethod
    def _direction(self) -> str:
        """``"buy"`` | ``"sell"``."""
        ...

    @abstractmethod
    def _boundary_sl(self, box_bound: float, cushion_pct: float) -> float:
        """경계 기반 SL 계산.

        롱: box_lower * (1 - cushion_pct / 100)
        숏: box_upper * (1 + cushion_pct / 100)
        """
        ...

    @abstractmethod
    def _atr_fallback_sl(self, entry: float, atr: float, mult: float) -> float:
        """ATR fallback SL: 롱 = entry - atr*mult, 숏 = entry + atr*mult."""
        ...

    @abstractmethod
    def _box_bound_from_snapshot(self, snapshot: "SignalSnapshot") -> float | None:
        """스냅샷에서 관련 경계 추출: 롱 = box_lower, 숏 = box_upper."""
        ...

    @abstractmethod
    def _is_ratchet_better(self, new_sl: float, current_sl: float) -> bool:
        """래칫 방향: 롱 = 더 높으면 Better, 숏 = 더 낮으면 Better."""
        ...

    @abstractmethod
    def _has_crossed_midpoint(self, current_price: float, entry: float, midpoint: float) -> bool:
        """중앙선 통과 여부: 롱 = price > midpoint, 숏 = price < midpoint."""
        ...

    @abstractmethod
    def _breakeven_apply(self, new_sl: float, entry: float) -> float:
        """손익분기 floor/ceiling 적용: 롱 = max(new_sl, entry), 숏 = min(new_sl, entry)."""
        ...

    # ── 공통 구현 ────────────────────────────────────────────────

    def initial(self, entry: Decimal, atr: Decimal, params: dict) -> Decimal:
        """진입 직후 최초 SL.

        box_lower/box_upper 가 params 에 있으면 경계 기반 SL 우선.
        없으면 ATR fallback (entry ± atr × atr_multiplier_stop).
        """
        # params에서 박스 경계 확인 (진입 시점에 snapshot을 통해 전달 가능)
        box_lower = params.get("box_lower")
        box_upper = params.get("box_upper")
        cushion_pct = float(params.get("box_sl_cushion_pct", 0.5))

        bound = box_lower if self._direction() == "buy" else box_upper
        if bound is not None:
            raw = self._boundary_sl(float(bound), cushion_pct)
        else:
            # ATR fallback
            mult = float(params.get("atr_multiplier_stop", 2.0))
            raw = self._atr_fallback_sl(float(entry), float(atr), mult)

        return Decimal(str(round(raw, 6)))

    def trail(
        self,
        pos: "Position",
        snapshot: "SignalSnapshot",
    ) -> "StopUpdate | None":
        """박스 전략 트레일링 SL.

        박스권에서는 가격이 중앙선을 통과했을 때만 SL을 breakeven으로 이동.
        그 외에는 경계 기반 SL을 유지 (느슨한 ATR ratchet 적용).
        """
        from core.strategy.contracts.stop import StopUpdate

        params = snapshot.params
        atr = snapshot.atr
        if atr is None or atr == 0:
            return None

        price = snapshot.current_price
        entry = float(pos.entry_price) if pos.entry_price else 0.0
        box_upper = snapshot.box_upper
        box_lower = snapshot.box_lower

        current_sl = float(pos.stop_loss_price) if pos.stop_loss_price is not None else None

        # 중앙선 통과 시: breakeven으로 이동
        if box_upper is not None and box_lower is not None and entry > 0:
            midpoint = (box_upper + box_lower) / 2.0
            if self._has_crossed_midpoint(price, entry, midpoint):
                new_sl = self._breakeven_apply(entry, entry)  # breakeven = entry
                if current_sl is None or self._is_ratchet_better(new_sl, current_sl):
                    return StopUpdate(
                        new_sl=Decimal(str(round(new_sl, 6))),
                        reason="box_midpoint_crossed_breakeven",
                        mult=0.0,
                        structural=False,
                    )

        # 느슨한 ATR ratchet (box_trailing_atr_mult는 추세보다 큰 값 사용)
        mult = float(params.get("box_trailing_atr_mult", 3.0))
        bound = self._box_bound_from_snapshot(snapshot)

        if bound is not None:
            # 경계 기반 SL
            cushion_pct = float(params.get("box_sl_cushion_pct", 0.5))
            new_sl = round(self._boundary_sl(float(bound), cushion_pct), 6)
        else:
            # ATR fallback ratchet
            new_sl = round(self._atr_fallback_sl(price, float(atr), mult), 6)

        if current_sl is not None and not self._is_ratchet_better(new_sl, current_sl):
            return None

        return StopUpdate(
            new_sl=Decimal(str(round(new_sl, 6))),
            reason="box_trail",
            mult=mult,
            structural=False,
        )

    def tighten(
        self,
        pos: "Position",
        snapshot: "SignalSnapshot",
        reason: str,
    ) -> "StopUpdate":
        """SL 타이트닝: 박스 중앙선 또는 entry로 이동."""
        from core.strategy.contracts.stop import StopUpdate

        entry = float(pos.entry_price) if pos.entry_price else 0.0
        box_upper = snapshot.box_upper
        box_lower = snapshot.box_lower

        if box_upper is not None and box_lower is not None:
            midpoint = (box_upper + box_lower) / 2.0
            new_sl = self._breakeven_apply(midpoint, entry)
        else:
            new_sl = entry  # fallback to breakeven

        return StopUpdate(
            new_sl=Decimal(str(round(new_sl, 6))),
            reason=reason,
            mult=0.0,
            structural=True,
        )

    def is_triggered(self, pos: "Position", price: float) -> bool:
        """스탑로스 발동 여부."""
        sl = pos.stop_loss_price
        if sl is None:
            return False
        if self._direction() == "buy":
            return price <= sl
        return price >= sl
