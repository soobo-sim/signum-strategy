# BRD Brief — strategy-abstraction-refactor

> 상태: `BRIEF_REVIEW_PENDING` — 아래 내용을 확인 후 "BRD 작성해줘"라고 말씀해 주세요.

## 1. 대상 서비스
- 서비스명: `signum-strategy`
- 출력 경로 (예정): `signum-strategy/docs/20260705_01_strategy-abstraction-refactor/BRD.md`

## 2. 문제 / 동기
- 기존 `signum-engine` BRD와 같은 방향의 `전략 추상화 리팩터`를 `signum-strategy` 저장소 관점으로 정리한다.
- `signum-strategy`는 전략 구현 저장소이지만, 현재 구조상 엔진 내부 구현(`core.*`)에 깊게 의존하고 있어 레포 독립성, 구조 이해도, 테스트 용이성이 낮다.
- 실제 확인 결과:
	- `src/` 내 `core.*` import가 89건 존재한다.
	- `pyproject.toml`에서 `signum-engine`, `signum-adapters`를 직접 의존성으로 선언한다.
	- 전략 정책 일부는 계약 타입(`core.strategy.contracts.*`)을 사용하지만, 전략 매니저는 `core.strategy.managers.gmo_coin_base` 등 엔진 구현체에 직접 기대고 있다.
- 이번 BRD의 핵심 목표는 `추상화와 구현을 분리`하여, `signum-strategy`를 엔진 내부 구현에서 더 독립적인 전략 패키지로 정리하는 것이다.

## 3. 비즈니스 목표 & 성공 지표
| Goal ID | 목표 | 측정 가능한 성공 지표 |
|---------|------|---------------------|
| G1 | `signum-strategy`를 엔진 내부 구현에서 더 독립적인 전략 패키지로 정리 | 새 전략 추가 시 `signum-engine` 수정 없이 `signum-strategy` 내부 변경만으로 완료 가능 |
| G2 | 전략 정책/판단 로직의 테스트 가능성 강화 | 전략 정책/판단 로직을 DB·HTTP 없이 단위 테스트 가능 |
| G3 | 전략 구조 파악을 쉽게 만든다 | `signum-strategy`의 엔진 직접 의존은 축소하고, 남는 의존도는 `contracts/ports` 계층으로만 수렴하도록 목표를 정의 |
| G4 | 레포 간 참조 방향을 안정화한다 | 의존 방향 원칙을 BRD에 명시하고, Architecture/HLD에서 `signum-engine ↔ signum-strategy ↔ signum-adapters` 참조 규칙을 검토 가능한 상태로 만든다 |

## 4. 범위
### In-scope
- 전략 매니저/정책 구조 추상화
- `signum-strategy`와 `signum-engine` 사이의 전략 계약 경계 재정의
- 레포 독립성 향상을 위한 구조 정리
- `signum-engine`, `signum-adapters`와의 참조 방향을 정리하고 Architecture/HLD 리뷰 포인트로 명시

### Out-of-scope
- 신규 전략 추가
- 기존 매매 규칙 변경
- DB 스키마 변경
- `signum-adapters` 구현 변경은 기본적으로 제외한다. ⚠️
- `signum-engine` 대규모 구조 개편은 기본적으로 제외하고, 전략 계약 경계 조정에 필요한 최소 변경만 허용하는 방향으로 본다. ⚠️

## 5. 제약 & 가정
- 현재 전략 동작/매매 결과는 유지해야 한다.
- 기존 `signum-engine` 계약과 호환을 유지해야 한다.
- `signum-strategy`는 여전히 전략 구현 저장소이며, 공개 import 경로는 가능하면 유지하는 방향으로 본다. ⚠️
- `signum-strategy`는 가능하면 `engine runtime implementation`이 아니라 `strategy contracts / ports`에만 의존하는 방향으로 정리한다. ⚠️
- `signum-adapters`는 전략 로직을 직접 참조하지 않고, 거래소 I/O를 담당하는 독립 레이어로 유지하는 방향을 우선 가정한다. ⚠️
- 레포 간 참조 방향의 상세 확정은 BRD에서 원칙만 고정하고, 구체 클래스/패키지 분리는 Architecture 또는 HLD에서 검토하는 것이 적절하다.

## 6. 우선순위 기준
- P1: 엔진-전략 경계를 명확히 하고 레포 독립성 확보에 직접 필요한 기반 작업
- P2: 구조 이해도와 테스트 용이성을 높이는 작업
- P3: 구조 안정화 후 확장성과 문서성을 높이는 작업

## 7. 미결 질문 / 확인 필요 사항
- ⚠️ `signum-adapters` 구현 변경은 제외, `signum-engine`은 전략 계약 경계 조정에 필요한 최소 변경만 허용으로 볼지 최종 확인 필요
- ⚠️ 공개 import 경로 유지(`signum_strategy.gmo_coin...`)를 명시 목표로 둘지 확인 필요
- ⚠️ Architecture/HLD 리뷰에서 검토할 의존 방향 초안:
	- `signum-engine` → 전략 실행 오케스트레이션, lifecycle, registry 담당
	- `signum-strategy` → 전략 매니저/정책/가드/사이징의 구현 담당
	- `signum-strategy`는 엔진의 구체 구현이 아니라 안정된 `contracts/ports`만 참조
	- `signum-adapters` → 거래소 I/O와 외부 연동 담당, 전략 규칙은 참조하지 않음
