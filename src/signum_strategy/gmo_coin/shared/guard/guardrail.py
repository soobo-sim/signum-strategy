
"""GuardrailGuard — AiGuardrails (GR-01~GR-06) Decision-time 어댑터.

> ⚠️ **현재 매니저에 미연결**. Phase 5 통합 시 도입 여부 재검토 필요.
>
> 의미 차이:
>   - 기존 ``AiGuardrails.check(decision, signal_snapshot_dto)`` — Decision DTO 기반,
>     ``judge/safety`` 레이어에서 호출. 진입 액션만 검사.
>   - ``EntryGuard.check(snapshot=..., ctx=...)`` — 다른 SignalSnapshot 클래스.
>
> 동일 GR-01~06 검증이 두 번 일어날 위험. 매니저 ``_open_position`` 단일 호출
> 통합 후 어느 한쪽으로 정리할지 Phase 5 에서 결정.

Decision 과 (judge 도메인) SignalSnapshot DTO 는 각각
``snapshot.params['_runtime_decision']``, ``snapshot.params['_runtime_judge_snapshot']``
로 주입돼야 한다. 없으면 fail-open (통과).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.strategy.contracts.guard import GuardResult
from core.strategy.contracts.types import Side

if TYPE_CHECKING:
    from core.judge.safety.guardrails import IGuardrail
    from core.strategy.contracts.regime import RegimeContext, SignalSnapshot


class GuardrailGuard:
    """``AiGuardrails`` (GR-01~06) 을 EntryGuard 로 감싸는 어댑터."""

    name = "GuardrailGuard"

    def __init__(self, guardrails: "IGuardrail") -> None:
        self._guardrails = guardrails

    async def check(
        self,
        *,
        pair: str,
        side: Side,
        snapshot: "SignalSnapshot",
        ctx: "RegimeContext",
    ) -> GuardResult:
        decision: Any = snapshot.params.get("_runtime_decision")
        judge_snapshot: Any = snapshot.params.get("_runtime_judge_snapshot")
        if decision is None or judge_snapshot is None:
            return GuardResult(allowed=True, reason=None, block_code=None)

        try:
            result = await self._guardrails.check(decision, judge_snapshot)
        except Exception as e:  # noqa: BLE001
            return GuardResult(
                allowed=False,
                reason=f"Guardrail 평가 예외: {e}",
                block_code="GUARDRAIL_ERROR",
            )

        if result.approved:
            return GuardResult(allowed=True, reason=None, block_code=None)
        return GuardResult(
            allowed=False,
            reason=result.rejection_reason or "Guardrail 거부",
            block_code="GUARDRAIL_BLOCKED",
        )
