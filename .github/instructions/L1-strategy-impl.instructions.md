---
applyTo: "src/signum_strategy/**"
---

# Space L1: strategy-impl — 전략 구현 레이어

## 이 Space의 범위

`src/signum_strategy/` 의 모든 파일:
- `gmo_coin/trend/manager.py` — GmoCoinTrendManager
- `gmo_coin/trend/policies/` — TrendLongStop / TrendShortStop / TrendLongExit / TrendShortExit / TrendLongProfit / TrendShortProfit
- `gmo_coin/box/manager.py` — GmoCoinBoxManager
- `gmo_coin/box/policies/` — BoxLongStop / BoxShortStop / BoxLongExit / BoxShortExit / BoxLongProfit / BoxShortProfit
- `gmo_coin/shared/guard/` — GuardPolicy 구현체
- `gmo_coin/shared/regime/` — GateRegimeClassifier, RegimeContext 구현
- `gmo_coin/shared/sizing/` — MarginSizer

연계 테스트: `tests/`

---

## 핵심 원칙

이 레이어는 **signum-engine의 Protocol 계약을 구현한다**.
Protocol 정의(`core.strategy.contracts.*`)는 signum-engine 의존성에서 가져오며,
**이 레포에서는 Protocol을 수정하지 않는다**.

- 새 Policy 클래스를 추가하면 반드시 `core.strategy.contracts.*` Protocol을 implements하는지 확인한다.
- `if side == "buy"` / `if direction == "long"` 분기를 메서드 내에 작성하지 않는다. Long/Short은 별도 클래스로 분리한다.
- **대칭성 필수**: TrendLong 수정 시 TrendShort, BoxLong 수정 시 BoxShort 동일 적용.
- `frozen=True` dataclass 패턴을 유지한다. **가변 상태 필드 추가 금지**.

---

## 활성화 체크리스트

코드를 수정하거나 생성하기 전에 아래를 순서대로 확인한다:

- [ ] 새 Policy 클래스가 `core.strategy.contracts.*` Protocol을 구현하는가?
- [ ] Long/Short 분기가 별도 클래스로 분리되어 있는가? (if side== 분기 금지)
- [ ] Trend 수정 시 Box, Long 수정 시 Short 대칭 확인 완료?
- [ ] 하드코드된 수치가 없는가? (params dict 또는 상수로 분리)
- [ ] DB import (AsyncSession, ORM 모델) 없이 순수 로직으로만 구성되어 있는가?
- [ ] signum-engine 내부 구현(non-protocol) 직접 import 없는가?

---

## 공통 OOP 원칙

1. **SSoT** — 동일 개념(계산·정책·상수)이 코드베이스에서 한 곳에만 존재하는가?
2. **영향 대칭성** — 롱/숏, trending/ranging 전체 4가지 조합에 동일하게 적용됐는가?
3. **값·의미 일치** — 변수명과 실제 값의 의미가 100% 일치하는가?
4. **분기 클래스화** — `if side=="BUY"` / `if regime=="trending"` 분기가 클래스 계층으로 표현됐는가?
5. **Protocol/ABC 명시** — 새 구현체의 인터페이스가 `core.strategy.contracts.*`에 정의됐는가?
6. **의존성 격리** — DB / signum-engine 내부 / 하드코드 API 키 없음.

---

## 상속 계층 참조

```
BaseStrategyManager (signum-engine: core.strategy.managers.base)
  └── MarginBaseManager (signum-engine: core.strategy.managers.margin_base)
        └── GmoCoinBaseManager (signum-engine: core.strategy.managers.gmo_coin_base)
              ├── GmoCoinTrendManager  ← 이 레포: src/signum_strategy/gmo_coin/trend/manager.py
              └── GmoCoinBoxManager   ← 이 레포: src/signum_strategy/gmo_coin/box/manager.py
```

## 참조 문서

- `signum-engine/docs/TREND_FOLLOWING.md` — 추세추종 전략 설계
- `signum-engine/docs/BOX_MEAN_REVERSION.md` — 박스 역추세 전략 설계
- `signum-strategy/README.md` — 레포 구조 및 키 임포트 패턴
