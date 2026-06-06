
"""TrendShortProfit — 추세 숏 이익확보 정책.

━━ 이 클래스의 역할 ━━
포지션 보유 중 이익 보호 목적의 **tighten_stop 전용** 평가.
full_exit는 반환하지 않는다 — 청산은 TrendShortExit 담당.

━━ 실제 로직 위치 ━━
  evaluate() → compute_exit_signal(side="sell") → action == "tighten_stop" 인 경우만 통과

━━ tighten_stop 트리거 조건 (숏 기준) ━━
  ① RSI < rsi_oversold(25)        — 과매도 구간 진입
  ② RSI < rsi_oversold_extreme(20) — 극단 과매도
  ③ 이익 > ATR × profit_atr_mult(2.0) — 충분한 이익 달성
  ④ EMA 기울기 -slope_weak ~ 0%  — 모멘텀 둔화
  → SL을 price + ATR × tighten_stop_atr(1.0) 로 끌어내려 이익 일부 확보

━━ TrendShortExit와의 관계 ━━
  loop.py PR-5에서 호출. exit_signal이 이미 hold일 때만 tighten으로 업그레이드.
  exit_policy가 full_exit/tighten_stop을 먼저 결정했으면 ProfitProtection은 무시됨.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.strategy.contracts.exit import ExitDecision
    from core.strategy.contracts.regime import SignalSnapshot
    from core.strategy.contracts.types import Position


class TrendShortProfit:
    """추세 숏 이익확보 정책. side='sell'."""

    def evaluate(
        self,
        pos: "Position",
        snapshot: "SignalSnapshot",
    ) -> "ExitDecision":
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
            side="sell",
        )

        action_raw = result.get("action", "hold")
        if action_raw == "tighten_stop":
            return ExitDecision(
                action="tighten",
                reason=result.get("reason", "이익확보 타이트닝"),
                triggers=result.get("triggers", {}),
            )
        return ExitDecision(action="hold", reason="이익확보 대기 중")
