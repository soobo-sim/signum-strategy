
"""TrendShortExit — 추세 숏 청산 정책.

━━ 이 클래스의 역할 ━━
방향 식별자 1개만 담는다. **실제 판단 로직은 TrendExitBase.evaluate()**에 있다.

━━ 실제 로직 위치 ━━
  evaluate() → TrendExitBase.evaluate() → compute_exit_signal(side="sell")

━━ compute_exit_signal이 반환하는 액션 (숏 기준) ━━
  full_exit    ← EMA 기울기 양전환(추세 반전) OR RSI > rsi_breakout_short(60) 과매수 급등
  tighten_stop ← RSI < rsi_oversold(25) OR RSI < rsi_oversold_extreme(20) OR 이익 ATR×2 달성
  hold         ← 위 조건 없음
"""

from signum_strategy.gmo_coin.trend.policies._exit_base import TrendExitBase


class TrendShortExit(TrendExitBase):
    """추세 숏 포지션 청산 정책. side='sell'.

    로직은 TrendExitBase.evaluate() → compute_exit_signal(side='sell') 위임.
    """

    def _side(self) -> str:
        """포지션 방향. compute_exit_signal의 side 파라미터로 전달."""
        return "sell"
