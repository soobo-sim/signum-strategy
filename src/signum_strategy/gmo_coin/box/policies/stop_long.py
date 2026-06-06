
"""BoxLongStop — 박스 롱 SL 정책."""

from __future__ import annotations

from signum_strategy.gmo_coin.box.policies._stop_base import BoxStopBase


class BoxLongStop(BoxStopBase):
    """박스 롱 포지션 스탑 관리. side='buy'.

    진입 위치: 박스 하단(near_lower).
    SL 위치: box_lower * (1 - cushion_pct) — 하단 경계 아래.
    """

    def _direction(self) -> str:
        return "buy"

    def _boundary_sl(self, box_bound: float, cushion_pct: float) -> float:
        return box_bound * (1 - cushion_pct / 100)

    def _atr_fallback_sl(self, entry: float, atr: float, mult: float) -> float:
        return entry - atr * mult  # 롱: 아래로

    def _box_bound_from_snapshot(self, snapshot: "object") -> float | None:
        return getattr(snapshot, "box_lower", None)

    def _is_ratchet_better(self, new_sl: float, current_sl: float) -> bool:
        return new_sl > current_sl  # 롱: SL이 더 높을수록 Better

    def _has_crossed_midpoint(self, current_price: float, entry: float, midpoint: float) -> bool:
        # 롱: 가격이 중앙선 위로 올라갔을 때
        return current_price > midpoint

    def _breakeven_apply(self, new_sl: float, entry: float) -> float:
        return max(new_sl, entry)  # 롱: floor = entry
