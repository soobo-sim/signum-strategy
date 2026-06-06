
"""CooldownGuard — 청산 실패 백오프 진입 차단 가드.

매니저의 ``_close_fail_until[pair]`` 를 ``snapshot.params['_runtime_cooldown_until']``
(epoch sec) 로 주입받아, 현재 시각이 만료 시각보다 이전이면 진입 차단.

배경: ``_stop_loss_monitor`` 가 SL 청산 5회 실패할 때마다 60초 쿨다운을 설정.
이 시간 동안은 추가 매수/진입을 막아 거래소 일시 장애 시 돈 더 잃는 것 방지.

가드 검증 시점은 ``_open_position`` 직전. 매니저가 가드 호출 직전에
``snapshot.params['_runtime_cooldown_until']`` 를 갱신해야 한다.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from core.strategy.contracts.guard import GuardResult
from core.strategy.contracts.types import Side

if TYPE_CHECKING:
    from core.strategy.contracts.regime import RegimeContext, SignalSnapshot


class CooldownGuard:
    """청산 실패 백오프 쿨다운 진입 차단."""

    name = "CooldownGuard"

    async def check(
        self,
        *,
        pair: str,
        side: Side,
        snapshot: "SignalSnapshot",
        ctx: "RegimeContext",
    ) -> GuardResult:
        params = snapshot.params
        cooldown_until = params.get("_runtime_cooldown_until")
        if cooldown_until is None:
            return GuardResult(allowed=True, reason=None, block_code=None)

        now = time.time()
        until = float(cooldown_until)
        if until <= 0 or now >= until:
            return GuardResult(allowed=True, reason=None, block_code=None)

        remaining = int(until - now)
        return GuardResult(
            allowed=False,
            reason=f"청산 실패 쿨다운 중 — {remaining}s 남음",
            block_code="COOLDOWN_ACTIVE",
        )
