
"""BoxShortStop — 박스 숏 SL 정책."""

from __future__ import annotations

from signum_strategy.gmo_coin.box.policies._stop_base import BoxStopBase


class BoxShortStop(BoxStopBase):
    """박스 숏 포지션 스탑 관리. side='sell'.

    진입 위치: 박스 상단(near_upper).
    SL 위치: box_upper * (1 + cushion_pct) — 상단 경계 위.
    """

    def _direction(self) -> str:
        return "sell"

    def _boundary_sl(self, box_bound: float, cushion_pct: float) -> float:
        return box_bound * (1 + cushion_pct / 100)

    def _atr_fallback_sl(self, entry: float, atr: float, mult: float) -> float:
        return entry + atr * mult  # 숏: 위로

    def _box_bound_from_snapshot(self, snapshot: "object") -> float | None:
        return getattr(snapshot, "box_upper", None)

    def _is_ratchet_better(self, new_sl: float, current_sl: float) -> bool:
        return new_sl < current_sl  # 숏: SL이 더 낮을수록 Better

    def _has_crossed_midpoint(self, current_price: float, entry: float, midpoint: float) -> bool:
        # 숏: 가격이 중앙선 아래로 내려갔을 때
        return current_price < midpoint

    def _breakeven_apply(self, new_sl: float, entry: float) -> float:
        return min(new_sl, entry)  # 숏: ceiling = entry
