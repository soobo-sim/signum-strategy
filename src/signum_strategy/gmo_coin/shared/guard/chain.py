
"""EntryGuardChain — 진입 차단 가드 체인.

OOP_STRATEGY_REFACTOR.md §2.6 참조.

현재 ``_candle_loop.py`` 에 흩어진 진입 전제 조건들을 Chain of Responsibility 로 통합.
각 가드는 ``EntryGuard`` Protocol 을 구현하며, 체인 자체도 동일 Protocol 을 만족한다.

현재 구현된 가드 (등록 순서 = 평가 순서):
  1. ``RegimeAllowsGuard`` — RegimeGate.should_allow_entry()
  2. ``KeepRateGuard`` — keep_rate < critical_threshold
  3. ``SlippageGuard`` — bid/ask spread 초과 (옵션)

단계 10 에서 ``_candle_loop.py`` 진입 조건 분기를 이 체인으로 대체한다.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.strategy.contracts.guard import GuardResult
from core.strategy.contracts.types import Side

if TYPE_CHECKING:
    from core.strategy.contracts.guard import EntryGuard
    from core.strategy.contracts.regime import RegimeContext, SignalSnapshot

logger = logging.getLogger(__name__)


class EntryGuardChain:
    """``EntryGuard`` 목록을 순서대로 평가. 첫 차단에서 즉시 반환."""

    name = "EntryGuardChain"

    def __init__(self, guards: list["EntryGuard"] | None = None) -> None:
        self._guards: list["EntryGuard"] = guards or []

    def add(self, guard: "EntryGuard") -> "EntryGuardChain":
        """가드 등록. 메서드 체이닝 지원."""
        self._guards.append(guard)
        return self

    async def check(
        self,
        *,
        pair: str,
        side: Side,
        snapshot: "SignalSnapshot",
        ctx: "RegimeContext",
    ) -> GuardResult:
        """등록 순서로 가드 평가. 첫 번째 차단 결과를 반환."""
        for guard in self._guards:
            try:
                result = await guard.check(pair=pair, side=side, snapshot=snapshot, ctx=ctx)
            except Exception as e:
                logger.warning(
                    f"EntryGuardChain: guard={guard.name} 예외 → 차단 처리. error={e}"
                )
                return GuardResult(
                    allowed=False,
                    reason=f"{guard.name} 평가 실패: {e}",
                    block_code="GUARD_ERROR",
                )
            if not result.allowed:
                logger.debug(
                    f"EntryGuardChain: {pair} {side} 차단 by {guard.name} "
                    f"code={result.block_code} reason={result.reason}"
                )
                return result
        return GuardResult(allowed=True, reason=None, block_code=None)


def build_default_chain(
    *,
    include_regime: bool = True,
    include_keep_rate: bool = True,
    include_slippage: bool = True,
    include_cooldown: bool = True,
    approval_gate=None,
    guardrails=None,
) -> "EntryGuardChain":
    """기본 EntryGuardChain 팩토리.

    평가 순서 (안전·외부의존도 낮은 것 먼저):
      1. RegimeAllowsGuard   (ctx.allows_entry())
      2. CooldownGuard       (close_fail 백오프)
      3. KeepRateGuard       (증거금)
      4. SlippageGuard       (시장 스프레드)
      5. GuardrailGuard      (GR-01~06, decision 주입 시)  ★ Phase 5 결정 보류
      6. ApprovalGuard       (텔레그램 승인, decision 주입 시) ★ Phase 5 결정 보류
    """
    from signum_strategy.gmo_coin.shared.guard.approval import ApprovalGuard
    from signum_strategy.gmo_coin.shared.guard.cooldown import CooldownGuard
    from signum_strategy.gmo_coin.shared.guard.guardrail import GuardrailGuard
    from signum_strategy.gmo_coin.shared.guard.keep_rate import KeepRateGuard
    from signum_strategy.gmo_coin.shared.guard.regime_allows import RegimeAllowsGuard
    from signum_strategy.gmo_coin.shared.guard.slippage import SlippageGuard

    chain = EntryGuardChain()
    if include_regime:
        chain.add(RegimeAllowsGuard())
    if include_cooldown:
        chain.add(CooldownGuard())
    if include_keep_rate:
        chain.add(KeepRateGuard())
    if include_slippage:
        chain.add(SlippageGuard())
    if guardrails is not None:
        chain.add(GuardrailGuard(guardrails=guardrails))
    if approval_gate is not None:
        chain.add(ApprovalGuard(gate=approval_gate))
    return chain
