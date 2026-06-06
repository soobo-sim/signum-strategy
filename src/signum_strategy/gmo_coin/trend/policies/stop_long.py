
"""TrendLongStop — 추세 롱 SL 정책.

━━ 이 클래스의 역할 ━━
방향 전용 수식 4개만 담는다. **알고리즘은 TrendStopBase**에 있다.
이 파일을 읽고 "로직이 없다"고 느끼면 정상 — Template Method 패턴 의도적 설계.

━━ 실제 로직 위치 (TrendStopBase) ━━
  initial()      → entry ± atr × params["atr_multiplier_stop"]
  trail()        → compute_adaptive_trailing_mult() + compute_profit_based_mult()
                   breakeven 락, ratchet, structural ceiling 모두 포함
  tighten()      → compute_structural_sl_mult() — 구조적 SL 타이트닝
  is_triggered() → price <= sl

━━ 롱(buy) 방향 수식 의미 ━━
  _new_sl_from_mult : SL = price - atr*mult   (SL은 항상 현재가 아래)
  _is_ratchet_better: new_sl > current_sl     (SL은 위로만 이동 — 절대 낮추지 않음)
  _breakeven_apply  : max(new_sl, entry)      (수익 구간 진입 시 SL ≥ 진입가 강제)
"""

from __future__ import annotations

from signum_strategy.gmo_coin.trend.policies._stop_base import TrendStopBase


class TrendLongStop(TrendStopBase):
    """추세 롱 포지션 스탑 관리. side='buy'.

    모든 트레일링·타이트닝 알고리즘은 TrendStopBase에 위임한다.
    이 클래스가 담당하는 것: 롱 방향의 4가지 순수 수식.
    """

    def _direction(self) -> str:
        """포지션 방향. TrendStopBase 내부에서 side 판별에 사용."""
        return "buy"

    def _new_sl_from_mult(self, price: float, atr: float, mult: float) -> float:
        """SL = 현재가 - ATR × mult.

        롱은 SL이 가격 아래에 있어야 하므로 가격에서 ATR 배수를 뺀다.
        mult가 클수록 SL이 멀어져 손실 허용 폭이 커진다.
        """
        return price - atr * mult

    def _is_ratchet_better(self, new_sl: float, current_sl: float) -> bool:
        """래칫 방향 판정: 롱은 SL이 높아지는 방향만 허용.

        트레일링 스탑은 수익 방향으로만 당겨야 한다.
        롱은 SL이 올라가는 것이 유리 — 새 SL > 현재 SL 일 때만 갱신.
        """
        return new_sl > current_sl

    def _breakeven_apply(self, new_sl: float, entry: float) -> float:
        """손익분기 floor 적용: 롱은 SL을 최소 진입가 이상으로 올린다.

        unrealized 수익이 breakeven_trigger_atr × ATR 이상이 되면 호출됨.
        max(new_sl, entry) → SL이 진입가 아래로 내려가지 않도록 floor 역할.
        이후 추가 상승 시 trail()이 계속 SL을 높여 이익을 보호한다.
        """
        return max(new_sl, entry)  # 롱: floor = entry
