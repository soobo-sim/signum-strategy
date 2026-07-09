# 박스권 역추세 전략 (Box Mean Reversion) — High Level Design

> 최종 업데이트: 2026-04-02 (양방향 롱+숏 지원 구현 완료)
> 적용 거래소: **GMO Coin** (BTC/JPY 레버리지)
> `trading_style = "box_mean_reversion"`
> 소스: `signum-engine/core/strategy/plugins/gmo_coin_box/`

---

## 1. 전략 개요

횡보(박스권) 장에서 가격이 박스 하단에 접근하면 매수, 상단에 접근하면 매도하여 평균 회귀(mean reversion) 수익을 노리는 전략.

**direction_mode 파라미터** (2026-04-02 추가)
- `"long_only"` (기본): near_lower 롱 진입 / near_upper 롱 청산
- `"both"`: near_lower 롱 진입(+숏 청산) / near_upper 숏 진입(+롱 청산)
- `"short_only"`: near_upper 숏 진입 / near_lower 숏 청산

> ⚠️ `is_margin_trading=False`(현물) 시 `direction_mode` 무시하고 `long_only` 강제

```
박스 상단 ─────────────  near_upper → 롱 청산 / 숏 진입 (both/short_only)
              ↑  ↓
   중간 영역  (hold)
              ↑  ↓
박스 하단 ─────────────  near_lower → 롱 진입 (long_only/both) / 숏 청산 (both/short_only)
```

**적합한 시장**: 횡보장 / 가격 레인지가 명확한 구간
**부적합한 시장**: 강한 추세장 (→ 추세추종 전략으로 전환)

---

## 2. 아키텍처

```
┌─────────────────────────────────────────────────────┐
│                 BoxMeanReversionManager              │
│              (싱글턴 오케스트레이터)                    │
│                                                     │
│  ┌──────────────────┐   ┌────────────────────────┐  │
│  │  Task 1           │   │  Task 2                 │  │
│  │  BoxMonitor       │   │  EntryMonitor           │  │
│  │  (60초 폴링)       │   │  (WS 틱 실시간)          │  │
│  │                   │   │                         │  │
│  │ • 캔들 완성 감지    │   │ • is_price_in_box()     │  │
│  │ • validate box    │   │ • near_lower → 매수     │  │
│  │ • detect new box  │   │ • near_upper → 매도     │  │
│  └──────────────────┘   └────────────────────────┘  │
│           │                        │                 │
│           ▼                        ▼                 │
│   (감지/유효성 — 인라인)   (포지션 CRUD — 인라인)    │
└─────────────────────────────────────────────────────┘
         │                           │
         ▼                           ▼
  ┌──────────┐              ┌───────────────┐
  │ Candle   │              │ ExchangeAdapter │
  │ 데이터    │              │ Protocol        │
  └──────────┘              └───────────────┘
  DB 직접 조회            (GmoCoinAdapter)
  (ORM 팩토리 prefix)

  TaskSupervisor
  (태스크 등록/감시/자동 재시작)
```

---

## 3. 생명주기

```
StrategyService.activate()
  └── trading_style == "box_mean_reversion"?
        └── BoxMeanReversionManager.start(pair/product_code, params)
              ├── Task 1: _box_monitor (asyncio.Task)
              └── Task 2: _entry_monitor (asyncio.Task)

StrategyService.archive() / reject()
  └── BoxMeanReversionManager.stop(pair/product_code)
        └── 두 태스크 cancel + await

서버 재시작 (lifespan startup)
  └── DB에서 active 전략 조회 → 각각 Manager.start() 호출
```

---

## 4. 핵심 로직 상세

### 4.1 Task 1 — BoxMonitor (캔들 기반 박스 감지/유효성)

**주기**: 60초마다 폴링

#### 4.1.1 박스 감지 (detect_and_create_box)

```
입력: 완성된 4H 캔들 lookback_candles개 (기본 60개)
출력: 신규 박스 (upper_bound, lower_bound) 또는 None

1. 이미 active 박스 존재? → 스킵
2. 캔들 부족 (< min_touches × 2)? → 스킵
3. 고점 클러스터 탐색:
   - 모든 캔들의 high 값을 수집
   - tolerance_pct 이내 가격끼리 클러스터링
   - min_touches 이상 반복된 가장 높은 클러스터 = upper_bound
4. 저점 클러스터 탐색:
   - 모든 캔들의 low 값을 수집
   - 동일 방식으로 lower_bound 결정
5. upper > lower 검증
6. 박스 폭 최소 기준 검증:
   min_width_pct = tolerance_pct × 2 + fee_rate_pct × 2
   (양쪽 진입/청산 구간 + 왕복 수수료 커버)
7. DB 저장 (status="active")
```

**클러스터링 알고리즘**:
- 몸통(open, close) 기준 우선, 꼬리(high, low) 보조
- `tolerance_pct` 이내 가격을 동일 클러스터로 묶음
- 클러스터 내 가격들의 중앙값을 대표값으로 사용

#### 4.1.2 박스 유효성 검사 (validate_active_box)

```
매 폴링 사이클마다 실행 (새 캔들 감지 시)

검사 항목:
1. 종가 이탈 검사:
   - close < lower_bound × (1 - tolerance%)  → "4h_close_below_lower"
   - close > upper_bound × (1 + tolerance%)  → "4h_close_above_upper"

2. 수렴 삼각형 감지:
   - 최근 캔들(최대 20개)의 고점 → 선형 회귀 기울기
   - 최근 캔들(최대 20개)의 저점 → 선형 회귀 기울기
   - 고점 기울기 < 0 AND 저점 기울기 > 0 → "converging_triangle"

무효화 시:
  - 박스 status → "invalidated"
  - 열린 포지션 있으면 → market_sell 즉시 손절
```

### 4.2 Task 2 — EntryMonitor (WS 틱 기반 진입/청산)

```
WS 채널: GMO Coin ticker (실시간 체결가)

매 틱마다:
  1. is_price_in_box(price) 호출
     → "near_lower" | "near_upper" | "middle" | "outside" | None

  2. 진입 조건:
     - box_state == "near_lower"
     - 이전 상태 ≠ "near_lower" (중복 발동 방지)
     - 열린 포지션 없음
     → market_buy 실행

  ⚠️ 재시작 시 prev_state 초기화 (dd31a65):
     - 포지션 없음 → prev_state = None → 이미 near_lower에 있으면 즉시 진입
     - 포지션 있음 → prev_state = 현재 zone (중복 청산 방지)

  3. 청산 조건:
     - box_state == "near_upper"
     - 이전 상태 ≠ "near_upper" (중복 발동 방지)
     - 열린 포지션 있음
     → market_sell 실행 (이익 실현)
```

> ⚠️ **EMA 이탈 청산 비적용 (BUG #183, 2026-05-20 수정)**
>
> 추세추종 전략(`GmoCoinTrendManager`)에서 상속되는 WS 실시간 EMA 이탈 청산
> (`price_below_ema_ws` / `ema_above_ws`)은 박스 전략에서 **비적용**.
>
> 이유: `box_near_lower` 롱 진입 시 price < EMA는 구조적으로 항상 성립 →
> EMA 이탈 조건이 즉시 True가 되어 진입 직후 청산 → 재진입 무한 루프 발생.
>
> 박스 전략의 청산 전담: TP(박스 상단) / SL(ATR 기반) / 박스 이탈(`box_outside`).

### 4.3 가격 위치 판정 (is_price_in_box)

```
tolerance = tolerance_pct / 100

near_lower 구간: lower × (1 - tol) ≤ price ≤ lower × (1 + tol)
near_upper 구간: upper × (1 - tol) ≤ price ≤ upper × (1 + tol)
outside:         price < lower × (1 - tol)  또는  price > upper × (1 + tol)
middle:          그 외 (박스 중간)
```

---

## 5. 전략 파라미터 (strategy.parameters)

| 파라미터 | 기본값 | 설명 |
|---------|-------|------|
| `trading_style` | `"box_mean_reversion"` | 전략 유형 식별자 |
| `pair` | — | 거래 대상 (예: `btc_jpy`) |
| `basis_timeframe` | `"4h"` | 박스 감지 및 유효성 검사 캔들 주기 |
| `box_tolerance_pct` | `0.4` | 박스 경계 허용 오차 (%) |
| `box_min_touches` | `3` | 클러스터 인정 최소 터치 횟수 |
| `box_lookback_candles` | `60` | 감지에 사용할 캔들 수 |
| `fee_rate_pct` | `0.04` | taker 수수료율 (%) |
| `box_min_width_pct` | (계산값) | 박스 최소 폭 (명시적 오버라이드 가능) |
| `position_size_pct` | `20.0` | 가용 잔고 대비 투입 비율 (%) |
| `min_order_jpy` | `500` | 최소 주문 금액 (JPY) |
| `bb_width_trending_min` | `4.0` | 체제 판정 trending 최소 BB width (%) |
| `bb_width_ranging_max` | `3.0` | 체제 판정 ranging 최대 BB width (%) |
| `range_pct_ranging_max` | `5.0` | 체제 판정 ranging 최대 가격 이동폭 (%) — BB 수축 경로 |
| `range_tight_ranging_max` | `4.5` | 체제 판정 ranging 폴백 임계값 (%) — 박스 미감지 시 |
| `box_range_coverage_mult` | `1.5` | 박스 감지 시 ranging 임계값 배수 — `range_pct < box_width × 이 값` |

> **체제 판정 (2026-05-14 개선 → 2026-05-14 임계값 재보정)**: 박스가 감지된 경우 ranging (b) 임계값을 박스 폭 기반으로 동적 계산.
> `range_pct < box_width_pct × box_range_coverage_mult` (기본 1.5, 2971 4H 캔들 기반 상향).
> 넓은 박스에서도 가격이 박스 안에 머물면 ranging으로 올바르게 판정.
> 박스 미감지 시 `range_tight_ranging_max = 4.5%` 폴백 (구 2.5%는 전이 구간 100% 차단 — 무의미).

---

## 6. 데이터 모델 (DB)

### 박스 테이블 (`gmoc_boxes`)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | Serial PK | |
| `pair` / `product_code` | String | 거래 대상 |
| `upper_bound` | Decimal | 박스 상단 |
| `lower_bound` | Decimal | 박스 하단 |
| `upper_touch_count` | Integer | 상단 터치 횟수 |
| `lower_touch_count` | Integer | 하단 터치 횟수 |
| `tolerance_pct` | Decimal | 감지 시 사용된 허용 오차 |
| `basis_timeframe` | String | 캔들 주기 |
| `status` | String | `active` / `invalidated` |
| `detected_from_candle_count` | Integer | 감지에 사용된 캔들 수 |
| `detected_at_candle_open_time` | DateTime | 마지막 캔들 시각 |
| `invalidation_reason` | String | 무효화 사유 (nullable) |
| `created_at` | DateTime | |

### 포지션 테이블 (`gmoc_box_positions`)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | Serial PK | |
| `pair` | String | |
| `box_id` | FK → gmoc_boxes | 소속 박스 |
| `side` | String | `"BUY"` / `"SELL"` |
| `entry_order_id` | String | 진입 주문 ID |
| `entry_price` | Decimal | 진입가 |
| `entry_amount` | Decimal | 진입 수량 (코인) |
| `entry_jpy` | Decimal | 투입 JPY (증거금 기준) |
| `exit_order_id` | String | 청산 주문 ID (nullable) |
| `exit_price` | Decimal | 청산가 (nullable) |
| `exit_amount` | Decimal | 청산 수량 (nullable) |
| `exit_jpy` | Decimal | 회수 JPY (nullable) |
| `exit_reason` | String | 청산 사유 (nullable) |
| `realized_pnl` | Decimal | 실현 손익 (자동 계산) |
| `status` | String | `open` / `closed` |
| `created_at` | DateTime | |

---

## 7. 거래소 어댑터 (ExchangeAdapter Protocol)

signum-engine은 `ExchangeAdapter` Protocol로 거래소 차이를 추상화한다.
`BoxMeanReversionManager`는 거래소를 알지 못하며, `GmoCoinAdapter`에 의존한다.

| 항목 | GMO Coin |
|------|----------|
| 시장 유형 | 암호화폐 레버리지 24/7 |
| 식별자 | `pair` (소문자: `btc_jpy`) |
| ORM 모델 프리픽스 | `gmoc_` |
| 수수료 | 0.04% |
| WS 틱 채널 | ticker (실시간 체결가) |
| 포지션 종류 | 롱+숏 양방향 |

> 상세: `adapters/gmo_coin/client.py`

---

## 8. 안전장치 (Safety Mechanisms)

| 장치 | 설명 |
|------|------|
| 중복 발동 방지 | `prev_position_state` 추적 — 같은 상태 재진입 시 스킵 |
| 1박스 1포지션 | `has_open_position()` 확인 후 진입 |
| 수수료 커버 검증 | 박스 폭 < `tolerance×2 + fee×2` 이면 박스 생성 거부 |
| 최소 주문 금액 | `invest_jpy < min_order_jpy` 시 스킵 |
| 박스 무효화 자동 손절 | `validate_active_box()` 이탈 감지 시 포지션 즉시 청산 |
| 수렴 삼각형 감지 | 고점↓ + 저점↑ 패턴 → 박스 무효화 (돌파 예상) |
| 태스크 헬스 체크 | `get_task_health()` — 태스크 생존 여부 모니터링 |
| 무효화 쿨다운 | 무효화 직후 8캔들(32시간) 재감지 금지. `_last_invalidation_time` 인메모리 추적 |
| 포지션 보유 중 박스 생성 금지 | `_detect_and_create_box()` 상단 포지션 가드. 신규 박스는 포지션 없을 때만 |
| 거래소 역지정주문 SL (GMO FX) | FX 진입 직후 거래소 STOP 주문 자동 등록 (`_register_exchange_stop_loss`). 서버 다운 시에도 거래소가 포지션 보호 |
| SL 이중 체결 방지 | 서버 SL 발동 → `_cancel_exchange_stop_loss()` 먼저 호출 → 거래소 STOP 주문 취소 후 청산 |

### 8.1. 박스 수명 정책 (2026-04-04 추가)

**자동 교체 안 함** — 기존 무효화 3중 방어(가격 이탈/체제 점검/Kill)로 충분.

**소프트 경고**: 박스 age > 120캔들(~20일) 시
- 15분 보고 텍스트에 `⚠️ 장기 박스 (N일째)` 표시


**무효화 쿨다운** (`_BOX_COOLDOWN_CANDLES = 8`)
- 박스 무효화 시 `_last_invalidation_time[pair]` 기록
- 이후 8캔들(32시간) 동안 `_detect_and_create_box()` → `None` 반환
- 목적: 무효화 직후 노이즈성 즉시 재형성 방지

**포지션 가드**
- 포지션 보유 중 `_detect_and_create_box()` 호출 시 즉시 `None` 반환
- 진입 근거(박스)가 포지션 보유 중 바뀌는 것 방지

> 백테스트(`engine.py`)에도 동일한 8캔들 쿨다운 로직 반영 (`last_invalidation_idx` 추적)

### 8.2. 거래소 역지정주문 SL 이중화 ~~(GMO FX 전용 — 2026-04-15 GMO FX 제거로 폐기)~~

> ⚠️ **이 섹션은 GMO FX 제거로 더 이상 적용되지 않습니다.**

**배경**: 서버 SL(10초 감시)은 SPOF(단일 장애점). 서버 다운 시 포지션이 무방비 상태.

**구조**:
```
FX 진입 성공
  └─ _register_exchange_stop_loss(pair, direction, position_id, size, sl_price)
       └─ adapter.close_order_stop(STOP) → order_id 반환 → _exchange_sl_orders[pair] 저장

서버 SL 발동 / 손절 청산
  └─ _close_position_market()
       ├─ _cancel_exchange_stop_loss(pair) → adapter.cancel_order(order_id)  ← 이중 체결 방지
       └─ _close_position_market_fx() → adapter.close_position()

거래소 STOP 체결 (서버 장애 시)
  └─ _sync_exchange_sl_status(pair) [60초 모니터]
       └─ adapter.get_positions() → 포지션 없음 감지 → _record_close_position("exchange_stop_loss")
```

**sl_price 계산**:
- `long`: `exec_price × (1 − stop_loss_pct / 100)`
- `short`: `exec_price × (1 + stop_loss_pct / 100)`

**내부 상태**: `_exchange_sl_orders: Dict[str, Optional[str]]` — pair별 거래소 SL 주문 ID 추적. `stop(pair)` 시 cleanup.

**graceful 처리**: `close_order_stop()` 실패 시 WARNING 로그 후 포지션 유지 (서버 SL 단독으로 계속). 거래 차단 안 함.

### 8.3. IFD-OCO 지정가 주문 ~~(GMO FX 전용 — 2026-04-15 GMO FX 제거로 폐기)~~

> ⚠️ **이 섹션은 GMO FX 제거로 더 이상 적용되지 않습니다.**

**배경**: MARKET 주문은 슬리피지 발생 + 서버가 실행 주체 → SPOF. IFD-OCO는 거래소가 주문 전체를 관리.

**IFD-OCO 작동 방식**:
| 단계 | 내용 |
|------|------|
| 발주 | `POST /v1/ifoOrder` → `rootOrderId` 반환. 진입 LIMIT + TP LIMIT + SL STOP 3개 서브주문 |
| 1차 체결 | 진입 LIMIT 체결 → 거래소가 OCO(TP/SL) 활성화 |
| TP/SL | 거래소가 자동 체결. 서버는 60초 폴링으로 감지 |

**상태 머신**:
```
None → pending (발주 직후, 메모리만)
pending → first_filled (1차 체결, DB box_position 생성)
first_filled → completed_tp | completed_sl (TP/SL 체결)
pending | first_filled → cancelled (강제 취소)
```

**서버 재시작 복원**:
- `pending`: DB row 없음 → 복원 불가. 거래소 GTC 주문은 남아있으므로 다음 60초 폴링에서 first_fill 감지
- `first_filled`: DB `ifdoco_status='first_filled'` → `start()` 시 `_ifdoco_orders[pair]` 복원

**내부 상태**:
- `_ifdoco_orders: Dict[str, Optional[str]]` — pair별 rootOrderId
- `_ifdoco_meta: Dict[str, Optional[Dict]]` — direction·price·box_id 등 메타

**박스 무효화 시**: `_cancel_active_ifdoco(pair)` 선행 후 포지션 강제 청산

**백테스트**: `use_ifdoco=True` → `entry_slippage=0.0`. SL·무효화·주말 청산은 기존 `config.slippage_pct` 유지.

### 8.4. 지정가 진입 (Limit Entry) — GMO Coin 박스 전략

**배경**: 시장가 진입 시 슬리피지로 체결가가 midpoint 부근에 위치 → breakeven SL 즉시 발동 위험 (#196).
`entry_mode = "limit"` 설정 시 `GmoCoinBoxManager._open_position_limit()`로 지정가 진입.

**지정가 계산**:
| 방향 | 신호 | 지정가 공식 |
|------|------|------------|
| 숏 | `box_near_upper` | `box_upper × (1 − near_bound_pct / 100)` |
| 롱 | `box_near_lower` | `box_lower × (1 + near_bound_pct / 100)` |

> 예: `box_upper=12,400,000`, `near_bound_pct=0.5%` → 숏 지정가 = `12,338,000`

**지정가 취소 트리거**:
| 트리거 | 처리 |
|--------|------|
| 박스 경계 변경 (`box_key` 불일치) | 취소 → 다음 사이클 재발행 |
| 신호 변경 | 취소 → 진입 중단 |
| 타임아웃 (`limit_timeout_sec`, 기본 300초) | 취소 → 진입 중단 |
| 거래소 측 취소 | pending 제거 → 다음 사이클 재시도 가능 |

**안전장치 4겹** (`_open_position_limit` 내):

| # | 조건 | 파라미터 | 기본값 |
|---|------|---------|--------|
| 1 | 방향 정합성: 숏 limit_price ≤ midpoint / 롱 ≥ midpoint | — | — |
| 2 | midpoint 최소 거리: `|limit − midpoint| / box_width < min_pct` | `min_limit_midpoint_cushion_pct` | 8.0% |
| 3 | WS 현재가 괴리율: `|limit − ws_price| / ws_price > max_pct` | `max_limit_price_deviation_pct` | 5.0% |
| 4 | 체결 후 재확인: exec_price가 midpoint 반대편에 위치하면 즉시 청산 | — | — |

> 안전장치 3은 `_latest_price[pair]` (WS 실시간가) 미수신 상태이면 스킵.
> 안전장치 4는 `_finalize_limit_entry()` 내 실행.

**내부 상태**:
- `PendingLimitOrder.box_key: tuple[float, float]` — `(box_upper, box_lower)`, 박스 경계 변경 감지용
- `_pending_limit_orders[pair]` — execution_mixin 관리
| 단계 | 내용 |
|------|------|
| 발주 | `POST /v1/ifoOrder` → `rootOrderId` 반환. 진입 LIMIT + TP LIMIT + SL STOP 3개 서브주문 |
| 1차 체결 | 진입 LIMIT 체결 → 거래소가 OCO(TP/SL) 활성화 |
| TP/SL | 거래소가 자동 체결. 서버는 60초 폴링으로 감지 |

**상태 머신**:
```
None → pending (발주 직후, 메모리만)
pending → first_filled (1차 체결, DB box_position 생성)
first_filled → completed_tp | completed_sl (TP/SL 체결)
pending | first_filled → cancelled (강제 취소)
```

**서버 재시작 복원**:
- `pending`: DB row 없음 → 복원 불가. 거래소 GTC 주문은 남아있으므로 다음 60초 폴링에서 first_fill 감지
- `first_filled`: DB `ifdoco_status='first_filled'` → `start()` 시 `_ifdoco_orders[pair]` 복원

**내부 상태**:
- `_ifdoco_orders: Dict[str, Optional[str]]` — pair별 rootOrderId
- `_ifdoco_meta: Dict[str, Optional[Dict]]` — direction·price·box_id 등 메타

**박스 무효화 시**: `_cancel_active_ifdoco(pair)` 선행 후 포지션 강제 청산

**백테스트**: `use_ifdoco=True` → `entry_slippage=0.0`. SL·무효화·주말 청산은 기존 `config.slippage_pct` 유지.

---

## 9. 소스 파일 맵 (signum-engine 통합)

| 파일 | 경로 |
|------|------|
| 전략 매니저 (통합) | `core/strategy/plugins/gmo_coin_box/` |
| 거래소 어댓터 | `adapters/gmo_coin/client.py` |
| ORM 모델 팩토리 | `adapters/database/models.py` (`create_box_model`, `create_box_position_model`) |
| 태스크 관리 | `core/task/supervisor.py` (`TaskSupervisor`) |
| 헬스 모니터링 | `core/monitoring/health.py` (`HealthChecker`) |
| API 라우트 | `api/routes/boxes.py` (`_box_pos_to_dict` — ifdoco/exchange_sl 직렬화 포함) |
| BFF 분석실 (direction_mode) | `trading-dashboard/bff/app/routes/strategy_analysis.py` (`get_box_data`) |
| 분석실 컴포넌트 | `trading-dashboard/web/src/components/analysis/BoxDetail.tsx` |
| 백테스트 엔진 | `core/backtest/engine.py` (`run_backtest`, `weekend_close`/`use_ifdoco` 파라미터 반영) |
| 엔트리포인트 | `main.py` (`EXCHANGE` 환경변수) |

> ~~기존 경로 (레거시): `coincheck-trader/app/services/box_mean_reversion_manager.py`~~ — 2026-03-25 삭제됨

---

## 10. 관련 문서

- [추세추종 전략 설계](TREND_FOLLOWING.md) — 추세장 전환 시 사용

---

## 10.1 버그 수정 이력

### [Bug #227 — 진입가 오기록] MARKET 주문 체결가 미확인 (2026-05-27)

- **현상**: `gmoc_box_positions.entry_price`에 체결 시점이 아닌 1분 후 WS 캐시 가격이 기록됨
  - 실제 체결가: ¥12,077,156 / 기록된 entry_price: ¥12,114,991 (¥37,835 오차)
- **원인**: `GmoCoinBaseManager._open_position`에서 `exec_price = order.price or price`
  - MARKET 주문 응답에 체결가 없음(price=None) → WS 캐시 현재가 폴백
- **수정**: `place_order` 후 `get_executions(order_id)` → `executionPrice` 사용, 조회 실패 시 WS 가격 폴백
- **적용 범위**: 진입(`_open_position`) + 피라미딩(add_position) 2경로 — Trend/Box 공통 기반 클래스 수정

---

## 11. ~~GMO FX 호환성 분석 (2026-04-15 GMO FX 제거로 폐기)~~

> ⚠️ **이 섹션은 GMO FX 제거로 더 이상 유효하지 않습니다. 아래 내용은 역사적 기록으로만 보존합니다.**

## 11. (폐기 섹션)

> **status: review** — Rachel / Samantha 리뷰 대기.
> 코드 수정 전 반드시 이 섹션 검증 완료 필요.

### 11.1 현물 vs FX 실행 모델 — 근본 차이

현재 `BoxMeanReversionManager`는 **현물(spot) 거래** 전제로 구현되어 있다. GMO FX는 **증거금(margin) 외환 거래**이며, 실행 모델이 근본적으로 다르다.

| 관점 | 현물 (BF) | 증거금 FX (GMO) |
|------|-----------|-----------------|
| 매수 | JPY로 코인 구매 → 코인 잔고 증가 | 신규 건옥(position) 성립, 코인 잔고 없음 |
| 매도 | 보유 코인 매도 → JPY 잔고 증가 | 기존 건옥 결제(close) → 증거금 반영 |
| 잔고 모델 | 통화별 잔고 (JPY, BTC, XRP …) | 단일 JPY 증거금 (equity / availableAmount) |
| 수량 단위 | 코인 수량 (소수점, e.g. 0.001 BTC) | 통화 수량 (정수, e.g. 1000 USD) |
| 포지션 종료 | `place_order(MARKET_SELL, coin_amount)` | `close_position(positionId, size)` |
| 수수료 | 거래 통화 차감 (taker fee) | 스프레드 내포 (명시적 차감 없음) |

### 11.2 코드 경로별 문제점

#### ⛔ ISSUE-1: `_open_position_market` — amount 문맥 불일치

**현재 코드** (L396-404):
```python
invest_jpy = jpy_available * position_size_pct / 100
order = await self._adapter.place_order(
    OrderType.MARKET_BUY, pair, invest_jpy,
)
```

**문제**: BF `MARKET_BUY`는 `amount=JPY금액`을 받아 내부에서 코인 수량 변환한다. 그러나 GMO FX `place_order`는 `amount=통화수량(정수)`을 기대한다 (docstring: "amount는 항상 통화 수량"). 만약 `invest_jpy = 200,000`을 넘기면 GMO FX는 이를 **USD 200,000통화**로 해석하여 거대한 주문을 넣는다.

**수정 방향**: GMO FX에서는 `invest_jpy / current_price`로 통화 수량을 산출하고, 정수로 반올림(내림)해야 한다. 레버리지 적용 시 `invest_jpy * leverage / price`로 확장 가능.

#### ⛔ ISSUE-2: `_close_position_market` — 현물 매도 로직이 FX에 부적합

**현재 코드** (L436-465):
```python
# 1. 코인 잔고 조회
currency = pair.split("_")[0].lower()    # "usd"
coin_available = balance.get_available(currency)  # ← FX: 0 반환

# 2. 수수료 차감 후 매도
sell_amount = math.floor(coin_available / (1 + fee_rate) * 1e8) / 1e8

# 3. MARKET_SELL 주문
order = await self._adapter.place_order(
    OrderType.MARKET_SELL, pair, sell_amount,
)
```

**문제 3건**:
1. **잔고 조회 실패**: GMO FX Balance에는 `jpy` 키만 존재한다. `get_available("usd")` → `CurrencyBalance(0, 0)` 반환 → `coin_available = 0` → "잔고 부족" 경고 후 청산 실패.
2. **수수료 차감 불필요**: FX 수수료는 스프레드에 내포되어 있다. `coin_available / (1 + fee_rate)` 로직은 현물 전용.
3. **MARKET_SELL 대신 closeOrder 필요**: FX에서는 신규 매도(SELL) 포지션이 성립될 뿐, 기존 매수 포지션 결제가 아니다. 기존 포지션 결제는 `close_position(symbol, side, positionId, size)` API를 사용해야 한다.

**수정 방향**: GMO FX에서는 `get_positions()` → 해당 pair의 open position → `close_position(positionId)` 호출.

#### ⛔ ISSUE-3: `FxPosition`에 `positionId` 필드 누락

**현재 코드** (`core/exchange/types.py:164-175`):
```python
@dataclass(frozen=True)
class FxPosition:
    product_code: str
    side: str
    price: float
    size: float
    pnl: float
    # ... (positionId 없음)
```

**문제**: `close_position(positionId)` 호출에 필수인 `positionId`가 FxPosition 타입에 없다. `get_positions()` 응답은 API로부터 `positionId`를 받지만 매핑하지 않고 버린다.

**수정 방향**: `FxPosition`에 `position_id: Optional[int] = None` 추가. `get_positions()`에서 `item.get("positionId")` 매핑.

#### ⚠️ ISSUE-4: 박스 무효화 시 손절 — closeOrder 필요

**현재 코드** (`_box_monitor` L161):
```python
if pos:
    await self._close_position_market(pair, pos, reason)
```

ISSUE-2와 동일한 문제. 박스 무효화 시 긴급 손절도 `closeOrder`를 사용해야 한다. `MARKET_SELL`을 보내면 기존 매수 건옥이 결제되지 않고 **신규 매도 건옥이 생성**되어 양방향 포지션이 열릴 수 있다.

#### ⚠️ ISSUE-5: BUG-008/009 dust 로직 — FX에서 불필요

**현재 코드** (L469-481):
```python
# BUG-009: 청산 후 dust 잔고 감지
dust = balance_after.get_available(currency_lower)  # "usd" → 0
if 0 < dust < min_size:
    logger.info(...)
```

**영향**: FX에서는 통화 잔고 개념이 없으므로 항상 0 반환. 실해 없지만 의미 없는 코드 실행.

**수정 방향**: FX일 때 스킵하거나, adapter에 `is_margin_trading` 프로퍼티 추가하여 분기.

#### ⚠️ ISSUE-6: `_record_open_position` — position_id 미저장

현재 DB 포지션 레코드에 거래소 `positionId`를 저장하는 컬럼이 없다. 진입 후 청산 시 `positionId`를 알아야 하므로:
- 방법 A: `gmo_box_positions`에 `exchange_position_id` 컬럼 추가 (Alembic 마이그레이션)
- 방법 B: 청산 시점에 `get_positions()`로 조회하여 매칭 (진입가/수량으로 특정)

### 11.3 수정 대상 요약

| # | 파일 | 영향도 | 설명 |
|---|------|--------|------|
| ISSUE-1 | `box_mean_reversion.py` `_open_position_market` | ⛔ **Critical** | amount=JPY를 통화수량으로 변환 필요 |
| ISSUE-2 | `box_mean_reversion.py` `_close_position_market` | ⛔ **Critical** | `place_order(SELL)` → `close_position(positionId)` 교체 |
| ISSUE-3 | `core/exchange/types.py` `FxPosition` | ⛔ **Critical** | `position_id` 필드 추가 + `get_positions()` 매핑 |
| ISSUE-4 | `box_mean_reversion.py` `_box_monitor` 손절 | ⛔ **Critical** | ISSUE-2와 동일 경로. 미수정 시 양방향 포지션 100% 발생 |
| ISSUE-5 | `box_mean_reversion.py` dust 로직 | ℹ️ Low | FX에서 무해하지만 불필요. 분기 추가 가능 |
| ISSUE-6 | DB 스키마 + `_record_open_position` | ⚠️ High | 거래소 positionId 저장 또는 런타임 조회 |
| ISSUE-7 | `box_mean_reversion.py` EntryMonitor 청산 | ⛔ **Critical** | near_upper 청산도 closeOrder 필요 (ISSUE-2와 동일 패턴) |
| ISSUE-8 | `box_mean_reversion.py` + cron/스케줄러 | ⚠️ High | 금요일 장 마감 전 자동 청산 필요 (주말 갭 리스크) |

> ⛔ **ISSUE 1~4, 7 미수정 상태 배포 절대 금지** (3인 전원 일치)
> 최악 시나리오: ISSUE-1 미수정 시 1거래 -¥13,500 (-27%)

### 11.4 제안 구현 설계

#### 진입 (`_open_position_market` FX 분기)

```
if adapter.is_margin_trading:
    # 1. 투입 JPY / 현재가 = 통화 수량
    size_raw = invest_jpy * leverage / price
    # 2. 1,000통화 단위 내림 (GMO FX 최소 단위)
    size = math.floor(size_raw / 1000) * 1000
    # 3. 최소 수량 검증
    if size < min_lot_size: skip
    # 3. speedOrder (MARKET_BUY, size)
    order = adapter.place_order(MARKET_BUY, pair, size)
    # 4. get_positions() → 매칭 → positionId 확보
    # 5. DB 기록 (exchange_position_id 포함)
else:
    # 기존 현물 로직 유지
```

#### 청산 (`_close_position_market` FX 분기)

```
if adapter.is_margin_trading:
    # 1. get_positions(pair) → open position 조회
    # 2. 매칭 (DB entry_price/amount 또는 저장된 positionId)
    # 3. close_position(symbol, close_side, positionId, size)
    #    close_side = "SELL" if position.side == "BUY" else "BUY"
    # 4. DB 청산 기록
else:
    # 기존 현물 로직 유지
```

### 11.5 정상 동작 확인 항목 (구현 후 검증)

| # | 검증 항목 | 방법 |
|---|----------|------|
| V-1 | `_open_position_market`: invest_jpy가 통화 수량으로 올바르게 변환되는가 | 단위 테스트 (price=150, invest_jpy=300000, leverage=3 → size=6000) |
| V-2 | `_close_position_market`: closeOrder가 정확한 positionId를 지정하는가 | 단위 테스트 + 모의 API |
| V-3 | 박스 무효화 시 긴급 손절이 closeOrder를 사용하는가 | 통합 테스트 |
| V-4 | 트라이얼 기간 종료 후 fee_rate_pct가 0.04%로 전환되는가 | 단위 테스트 (datetime mock) |
| V-5 | `get_positions()` → FxPosition.position_id 매핑 정상인가 | 단위 테스트 |
| V-6 | 양방향 포지션 미발생 확인 (SELL 주문이 아닌 closeOrder) | 통합 테스트 |
| V-7 | 현물(BF) 기존 로직 회귀 없음 | 기존 79개 박스 테스트 통과 |
| V-8 | EntryMonitor near_upper 청산이 closeOrder를 사용하는가 (ISSUE-7) | 통합 테스트 |
| V-9 | 금요일 자동 청산이 정상 실행되는가 (ISSUE-8) | 스케줄 테스트 (datetime mock) |
| V-10 | 1,000통화 단위 내림이 정확한가 | 단위 테스트 (size_raw=2999→2000, 999→skip) |
| V-11 | 증거금 부족 시 진입 거부 + 에러 핸들링 | 단위 테스트 |
| V-12 | 레버리지 변경 시 size 계산 정확성 | 단위 테스트 (lever=1,3,5) |
| V-13 | closeOrder 실패 시 리트라이 + 알림 | 통합 테스트 |

### 11.6 해결 완료 항목

| 날짜 | 항목 | 설명 |
|------|------|------|
| 2026-03-30 | pair 대소문자 정규화 | `boxes.py` 5개 엔드포인트 + `main.py` 전략 시작 시 `normalize_pair()` 적용 |
| 2026-03-30 | 대시보드 태스크 헬스체크 | `healthcheck_builders.py` 태스크 키 대소문자 무관 매칭 |
| 2026-03-30 | fee_rate_pct 자동 전환 | `GmoFxAdapter.fee_rate_pct` 프로퍼티 — 트라이얼 만료 자동 감지 |
| 2026-03-30 | **3인 리뷰 반영** | ISSUE-4 Critical 격상, ISSUE-7/8 추가, size 1,000단위 내림, §7 테이블 5행, V-8~V-13 추가 |
| 2026-03-31 | **ISSUE-1** | `_open_position_market` FX 분기: invest_jpy→통화수량 변환, 1,000단위 내림 |
| 2026-03-31 | **ISSUE-2** | `_close_position_market_fx`: closeOrder(positionId) 사용 |
| 2026-03-31 | **ISSUE-3** | `FxPosition.position_id` 추가 + `get_positions()` 매핑 |
| 2026-03-31 | **ISSUE-4** | `_box_monitor` 손절경로 → `_close_position_market` 디스패치로 FX 대응 |
| 2026-03-31 | **ISSUE-5** | dust 체크를 `_close_position_market_spot`에만 한정 |
| 2026-03-31 | **ISSUE-6** | `exchange_position_id` 컬럼 추가 + `_record_open_position`에서 저장 |
| 2026-03-31 | **ISSUE-7** | `_entry_monitor` near_upper 청산도 `_close_position_market` 경유 → 자동 FX 대응 |
| 2026-03-31 | **ISSUE-8** | `_box_monitor`에 주말 자동 청산 + `_entry_monitor`에 FX 진입 차단 (`session.py` 재사용) |
| 2026-03-31 | **V-1~V-13** | 단위 테스트 13건 추가 (FX 변환, 1000단위 내림, 레버리지별 size, closeOrder 실패, 주말 차단 등) |
| 2026-04-06 | **BOX_IFDOCO_MIGRATION** | `use_ifdoco` 분기 + IFD-OCO 7메서드 신규. `scripts/migrate_ifdoco.sql` 적용 완료. |
| 2026-04-06 | **테스트 1117 passed** | `test_box_ifdoco.py` S1~S8 24개 (발주/체결/취소/백테스트/어댑터/DB복원/무효화취소/both재발주). 회귀 0 |
| 2026-04-06 | **리팩토링** | `_linear_slope` dead code 제거. `test_box_mean_reversion.py` `linear_slope` 직접 호출로 수정 |
| 2026-04-06 | **BOX_IFDOCO_UX** | `_box_pos_to_dict` ifdoco 3필드 추가. BFF `get_box_data` direction_mode/stop_loss_pct 파이프라인. `BoxDetail.tsx` direction_mode 기반 조건부 렌더링 + IFD-OCO TP/SL 가격 표시. engine 1121 passed, BFF 77 passed |
| 2026-05-14 | **체제 판정 박스 폭 동적화** | `classify_regime()`에 `box_width_pct` 옵션 추가. ranging (b-1): `range_pct < box_width × 1.2` 배수 기반. GateRegimeClassifier + GmoCoinBoxManager 연동. pytest 2003 passed |
| 2026-05-14 | **ranging 임계값 데이터 기반 상향** | 2971 4H 캔들 분석: 전이 구간 range_pct < 2.5% 캔들 0개(무의미). `range_tight_ranging_max` 2.5→**4.5%** (30th pct), `box_range_coverage_mult` 1.2→**1.5**. pytest 2003 passed |

---

## 12. AI-Native 통합 — Advisory 기반 (2026-04-11)

> **상태**: 설계 완료, 미구현. 상세: [`ai-native/02_JUDGMENT_ENGINE.md`](../ai-native/02_JUDGMENT_ENGINE.md), [`ai-native/03_EXECUTION_MODEL.md`](../ai-native/03_EXECUTION_MODEL.md)

### 12.1 현재 한계

박스전략은 현재 **100% 룰베이스**. `ExecutionOrchestrator`, advisory, guardrail 미사용. 모든 진입/청산 판단이 `_tick_monitor` 내부 가격 비교로만 이루어진다.

### 12.2 AI 개입 5-Layer 모델 — "사전 판단"

tick 루프는 건드리지 않고, AI가 **tick 진입 전에 사전 판단**을 advisory로 저장한다. `_tick_monitor`는 advisory를 참조만 한다:

| Layer | AI 판단 | advisory 반영 방식 | tick 루프 영향 |
|-------|---------|-------------------|---------------|
| **① 박스 허가/차단** | "이 박스에서 트레이딩 할까?" | advisory.action = `hold` → tick 진입 차단 | `_tick_monitor` 진입 전 advisory 체크 |
| **② 방향 결정** | "long_only / short_only / both" | advisory.direction_mode override | 기존 `direction_mode` 파라미터 대체 |
| **③ 크기 조절** | 확신도 기반 position_size_pct | advisory.size_pct | `_open_position_market` 참조 |
| **④ SL 폭 조절** | 매크로 기반 stop_loss_pct 조절 | advisory.adjustments.stop_loss_pct | 매 tick SL 체크 시 advisory값 사용 |
| **⑤ TP 판단** | "상단 청산 vs 홀드(추세 전환)" | advisory.adjustments.take_profit_ratio | 박스 경계 비율 적용 |

```
[4H 캔들 완성 — 정기 점검]
  
  레이첼: "이 박스 유효, long_only, 확신도 MID, SL 넓게"
    → POST /api/advisories:
        action=entry_long, direction_mode=long_only,
        size_pct=0.30, stop_loss_pct=2.0

[tick 루프 — 매 tick]
  
  가격 near_lower?
    → advisory 체크: action=entry_long? ✅
    → direction_mode: long_only? ✅
    → size_pct: 0.30 적용
    → stop_loss_pct: 2.0 적용
    → 진입 실행
```
