
"""KeepRateGuard — 증거금 유지율 진입 차단 가드."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.strategy.contracts.guard import GuardResult
from core.strategy.contracts.types import Side

if TYPE_CHECKING:
    from core.strategy.contracts.regime import RegimeContext, SignalSnapshot


class KeepRateGuard:
    """``snapshot.params['keep_rate_entry_min']`` 미달 시 진입 차단.

    현재 keep_rate 는 ``snapshot.params`` 에 런타임 주입
    (``_on_candle_extra_checks`` 에서 ``_last_keep_rate`` 경유).
    """

    name = "KeepRateGuard"

    async def check(
        self,
        *,
        pair: str,
        side: Side,
        snapshot: "SignalSnapshot",
        ctx: "RegimeContext",
    ) -> GuardResult:
        params = snapshot.params
        min_rate = params.get("keep_rate_entry_min")
        if min_rate is None:
            return GuardResult(allowed=True, reason=None, block_code=None)

        keep_rate = params.get("_runtime_keep_rate")
        if keep_rate is None:
            # keep_rate 주입 없으면 통과 (fail-open)
            return GuardResult(allowed=True, reason=None, block_code=None)

        if float(keep_rate) < float(min_rate):
            return GuardResult(
                allowed=False,
                reason=f"keep_rate={keep_rate:.2f} < 진입 최소값={min_rate}",
                block_code="KEEP_RATE_LOW",
            )
        return GuardResult(allowed=True, reason=None, block_code=None)
