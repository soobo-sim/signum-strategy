
"""RegimeAllowsGuard — 체제 게이트 진입 허용 가드."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.strategy.contracts.guard import GuardResult
from core.strategy.contracts.types import Side

if TYPE_CHECKING:
    from core.strategy.contracts.regime import RegimeContext, SignalSnapshot


class RegimeAllowsGuard:
    """``ctx.allows_entry()`` == False 이면 차단.

    unclear 체제 · warm-up 중 · 비활성 전략 체제에서 진입 차단.
    """

    name = "RegimeAllowsGuard"

    async def check(
        self,
        *,
        pair: str,
        side: Side,
        snapshot: "SignalSnapshot",
        ctx: "RegimeContext",
    ) -> GuardResult:
        if not ctx.allows_entry():
            return GuardResult(
                allowed=False,
                reason=f"체제 차단: {ctx.name} — 진입 불허",
                block_code="REGIME_BLOCKED",
            )
        return GuardResult(allowed=True, reason=None, block_code=None)
