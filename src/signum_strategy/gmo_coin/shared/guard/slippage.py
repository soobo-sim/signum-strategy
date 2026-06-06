
"""SlippageGuard — bid/ask 스프레드 초과 진입 차단 가드."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.strategy.contracts.guard import GuardResult
from core.strategy.contracts.types import Side

if TYPE_CHECKING:
    from core.strategy.contracts.regime import RegimeContext, SignalSnapshot


class SlippageGuard:
    """스프레드가 ``max_slippage_pct`` 를 초과하면 진입 차단.

    ``snapshot.params`` 에 ``max_slippage_pct`` (%) 가 설정돼 있고
    ``snapshot.spread_pct`` 가 주입돼 있을 때만 동작한다.
    둘 중 하나라도 없으면 fail-open (통과).
    """

    name = "SlippageGuard"

    async def check(
        self,
        *,
        pair: str,
        side: Side,
        snapshot: "SignalSnapshot",
        ctx: "RegimeContext",
    ) -> GuardResult:
        params = snapshot.params
        max_slippage_pct = params.get("max_slippage_pct")
        spread_pct = params.get("_runtime_spread_pct")

        if max_slippage_pct is None or spread_pct is None:
            return GuardResult(allowed=True, reason=None, block_code=None)

        if float(spread_pct) > float(max_slippage_pct):
            return GuardResult(
                allowed=False,
                reason=f"스프레드={spread_pct:.4f}% > 상한={max_slippage_pct}%",
                block_code="HIGH_SLIPPAGE",
            )
        return GuardResult(allowed=True, reason=None, block_code=None)
