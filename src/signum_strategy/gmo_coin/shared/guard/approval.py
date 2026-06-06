
"""ApprovalGuard — AutoApprovalGate Decision-time 승인 래퍼.

> ⚠️ **현재 매니저에 미연결**. Phase 5 통합 시 도입 여부 재검토 필요.
>
> 의미 차이:
>   - 기존 ``AutoApprovalGate.request_approval(decision)`` — Decision DTO 기반,
>     ``judge/decision`` 레이어에서 호출.
>   - ``EntryGuard.check(snapshot=..., ctx=...)`` — SignalSnapshot 기반,
>     ``_open_position`` 직전 호출.
>
> 두 흐름이 분리돼 있어 ApprovalGuard 를 EntryGuardChain 에 끼우면 동일 승인이
> 두 번 일어날 위험. 실제 사용은 Phase 5 에서 결정.

Decision 객체는 ``snapshot.params['_runtime_decision']`` 으로 주입돼야 한다.
없으면 fail-open (통과).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.strategy.contracts.guard import GuardResult
from core.strategy.contracts.types import Side

if TYPE_CHECKING:
    from core.judge.execution.approval import AutoApprovalGate
    from core.strategy.contracts.regime import RegimeContext, SignalSnapshot


class ApprovalGuard:
    """``AutoApprovalGate`` 를 EntryGuard 로 감싸는 어댑터."""

    name = "ApprovalGuard"

    def __init__(self, gate: "AutoApprovalGate") -> None:
        self._gate = gate

    async def check(
        self,
        *,
        pair: str,
        side: Side,
        snapshot: "SignalSnapshot",
        ctx: "RegimeContext",
    ) -> GuardResult:
        decision: Any = snapshot.params.get("_runtime_decision")
        if decision is None:
            # 매니저가 Decision 을 주입하지 않은 경우 통과 (fail-open)
            return GuardResult(allowed=True, reason=None, block_code=None)

        try:
            approved = await self._gate.request_approval(decision)
        except Exception as e:  # noqa: BLE001
            return GuardResult(
                allowed=False,
                reason=f"승인 요청 예외: {e}",
                block_code="APPROVAL_ERROR",
            )

        if approved:
            return GuardResult(allowed=True, reason=None, block_code=None)
        return GuardResult(
            allowed=False,
            reason="승인 거부",
            block_code="APPROVAL_DENIED",
        )
