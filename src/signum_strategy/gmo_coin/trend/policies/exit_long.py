
"""TrendLongExit — 추세 롱 청산 정책.

━━ 이 클래스의 역할 ━━
방향 식별자 1개만 담는다. **실제 판단 로직은 TrendExitBase.evaluate()**에 있다.

━━ 실제 로직 위치 ━━
  evaluate() → TrendExitBase.evaluate() → compute_exit_signal(side="buy")

━━ compute_exit_signal이 반환하는 액션 (롱 기준) ━━
  full_exit    ← EMA 기울기 음전환(추세 반전) OR RSI < rsi_breakdown(40) 과매도 급락
  tighten_stop ← RSI > rsi_overbought(75) OR RSI > rsi_extreme(80) OR 이익 ATR×2 달성
  hold         ← 위 조건 없음
"""

from signum_strategy.gmo_coin.trend.policies._exit_base import TrendExitBase


class TrendLongExit(TrendExitBase):
    """추세 롱 포지션 청산 정책. side='buy'.

    로직은 TrendExitBase.evaluate() → compute_exit_signal(side='buy') 위임.
    """

    def _side(self) -> str:
        """포지션 방향. compute_exit_signal의 side 파라미터로 전달."""
        return "buy"
