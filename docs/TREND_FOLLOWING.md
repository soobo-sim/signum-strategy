# 추세추종 전략 (Trend Following) — High Level Design

> 최종 업데이트: 2026-05-02 (P1+B+C ws_cross 진입, SL 신뢰성 강화, JIT Advisory Gate, 신호명 대칭화 반영)
> 적용 거래소: **GMO Coin** (BTC/JPY 레버리지) — `EXCHANGE=gmo_coin` 고정
> `trading_style = "trend_following"`
> 소스: `signum-engine/core/strategy/plugins/gmo_coin_trend/`

---

## 1. 전략 개요

강한 상승 추세에서 방향에 순응하여 포지션을 잡고, **추세가 살아있는 동안 100% 포지션 유지**,
고점 감지 후 신속 전량 청산으로 수익을 확보하는 전략.

**핵심 원칙: "Let winners run"** — 조기 부분 청산 없이 추세를 끝까지 탄다.

```
               ┌── 추세 시작 (EMA20 ↑, RSI 40~65)
               │       ▼
         ═══════════ entry_ok → market_buy (자동 진입)
               │
               │   가격 상승 중...
               │   ├── 적응형 트레일링 스탑 ratchet-up
               │   │   (이익에 비례해 선형 감쇠: initial 1.5 → min 0.3, decay 0.2/ATR)
               │   │
               │   └── [Phase 2] EMA 기울기 3캔들 연속 하락
               │           → 스탑 타이트닝 (ATR × tighten_stop_atr, 기본 1.0)
               │   └── [Phase 3] RSI + 볼륨 베어리시 다이버전스 감지 (새 캔들마다)
               │           가격↑ + RSI↓(gap≥3) → 스탑 타이트닝
               │           가격↑ + 거래량↓(15%+) → 스탑 타이트닝
               │           RSI+볼륨 동시 → 높은 신뢰도 (동일 동작, 로그 구분)
               │
               │   추세 약화/반전 감지
               │   ├── 과매수/기울기 둔화/이익목표 → 스탑 타이트닝 (1회)
               │   ├── EMA 기울기 음전환 → 전량 청산
               │   └── RSI < 40 → 전량 청산
               │
               │   하드 스탑
         ═══════════ 가격 ≤ 스탑로스 → 즉시 market_sell
               │
               └── 추세 종료
```

**적합한 시장**: 강한 방향성이 있는 시장 (BB width ≥ 6% 또는 range ≥ 10%)
**부적합한 시장**: 횡보장 (→ 박스권 역추세 전략으로 전환)

---

## 2. 아키텍처

```
┌──────────────────────────────────────────────────────┐
│                 TrendFollowingManager                 │
│              (싱글턴 오케스트레이터)                      │
│                                                      │
│  ┌───────────────────┐   ┌─────────────────────────┐ │
│  │  Task 1            │   │  Task 2                  │ │
│  │  CandleMonitor     │   │  StopLossMonitor         │ │
│  │  (60초 폴링)        │   │  (WS 틱 실시간)           │ │
│  │                    │   │                          │ │
│  │ • 시그널 계산        │   │ • 현재가 vs 스탑로스       │ │
│  │ • 진입/청산 판단     │   │ • 이탈 시 즉시 market_sell │ │
│  │ • 적응형 트레일링    │   │ • 실시간 가격 캐시        │ │
│  │ • EMA slope 이력    │   │                          │ │
│  └───────────────────┘   └─────────────────────────┘ │
│           │                         │                 │
│           ▼                         ▼                 │
│   인메모리 포지션 상태        ExchangeAdapter Protocol   │
│   {entry_price, amount,      (GmoCoinAdapter)           │
│    stop_loss_price}                                     │
│           │                                           │
│           ▼                                           │
│   DB 포지션 레코드 (ORM 팩토리)                          │
│   테이블: gmoc_trend_positions                          │
│                                                      │
│   TaskSupervisor                                      │
│   (태스크 등록/감시/자동 재시작)                           │
└──────────────────────────────────────────────────────┘
```

---

## 3. 생명주기

```
StrategyService.activate()
  └── trading_style == "trend_following"?
        └── TrendFollowingManager.start(pair/product_code, params)
              ├── _detect_existing_position() — 잔고로 기존 포지션 복원
              ├── _recover_db_position_id()   — DB 레코드 ID 복원
              ├── Task 1: _candle_monitor (asyncio.Task)
              └── Task 2: _stop_loss_monitor (asyncio.Task)

StrategyService.archive() / reject()
  └── TrendFollowingManager.stop(pair/product_code)
        └── 두 태스크 cancel + await

서버 재시작 (lifespan startup)
  └── DB에서 active 전략 조회 → 각각 Manager.start() 호출
      └── 잔고 ≥ min_coin_size이면 기존 포지션 복원 + 스탑로스 감시 재개
```

---

## 4. 핵심 로직 상세

### 4.1 시그널 계산 (_compute_trend_signal)

완성된 4H 캔들 데이터로 아래 지표를 산출:

#### 지표

| 지표 | 계산 방법 | 기본 기간 |
|------|----------|----------|
| **EMA20** | 지수 이동 평균 (k = 2/(20+1)) | 20캔들 |
| **EMA 기울기** | `(EMA_now - EMA_prev) / EMA_prev × 100` (%) | 1캔들 전 비교 |
| **ATR14** | True Range 14캔들 단순 평균 | 14캔들 |
| **RSI14** | Relative Strength Index | 14캔들 |
| **BB Width** | `4 × σ / SMA × 100` (%) — 변동성 측정 | 20캔들 |
| **Range %** | `(max_high - min_low) / first_close × 100` | 전체 lookback |

#### 시그널 결정 로직

```
# ── 공통 사전 조건 계산 ────────────────────────────────
# 최근 N봉 고점/저점 근접 여부 (추격 진입 차단)
#   [롱] high_proximity_lookback: 기본 120봉 (4H × 120 ≈ 20일) — 2026-05-15 변경: 20→120
#         high_proximity_min_atr: 기본 2.0 — 2026-05-15 변경: 1.0→2.0
#   [숏] short_low_lookback:      기본 180봉 (4H × 180 ≈ 30일) — 2026-05-28 추가 (롱보다 보수적)
#         short_prox_min_atr:     기본 3.0 — 2026-05-28 추가 (롱 2.0보다 넓은 버퍼)
#         → 1개월 신저가 근방에서 숏 진입 시 R:R 불량 + 반등 위험을 추가로 차단
#         → extrema_candles(30일치) 제공 시 더 긴 기간 최저가 사용; 없으면 60봉 최저가 폴백
long_high_proximity_blocked  = (recent_120봉_high - reference_price) < 2.0 × ATR
short_low_proximity_blocked  = (reference_price  - recent_30일_low)  < 3.0 × ATR  # 30일 저점 기준

# Swing High/Low 구조적 저항/지지선 감지 (2026-05-15 추가)
#   swing_resistance_lookback: 90봉 내 로컬 최고점(피벗) 감지 (pivot_window 좌우 3봉 기준)
#   swing_resistance_min_touches: 동일 저항선 접촉 최소 2회 이상
#   swing_resistance_price_zone_pct: 현재가 기준 2% 이내에 저항선 존재 시 진입 차단
#   (좌우 대칭: low Swing Low 감지는 숨 지지선 역할 — 숨 바로 위에서 매도 릩쟁 차단)
_swing_resistance_blocked  = detect_swing_high_resistance(highs, price, params)
_swing_support_blocked     = detect_swing_low_support(lows, price, params)

# 절대 고점/저점 ATR 버퍼 차단 (2026-05-16 추가) — 3개월 극단 구간 신규 진입 영구 불허
#   absolute_extrema_lookback:   탐색 봉 수 (기본 540 = 3개월 × 4H), 데이터 부족 시 차단 안 함
#   absolute_extrema_atr_buffer: ATR 버퍼 배수 N (기본 2.0)
#   롱: 현재가 > (3개월 최고가 - ATR × N) → 진입 불허 (상방 여유 없음 — R:R 불량)
#   숏: 현재가 < (3개월 최저가 + ATR × N) → 진입 불허 (하방 여유 없음 — R:R 불량)
#   돌파 후 기존 포지션은 트레일링 스탑만 유지 (신규 진입만 차단)
_absolute_high_blocked = reference_price > (3개월_최고가 - absolute_extrema_atr_buffer × ATR)
_absolute_low_blocked  = reference_price < (3개월_최저가 + absolute_extrema_atr_buffer × ATR)

_long_entry_blocked   = _long_high_proximity_blocked  OR _swing_resistance_blocked OR _absolute_high_blocked
_short_entry_blocked  = _short_low_proximity_blocked  OR _swing_support_blocked    OR _absolute_low_blocked

# ── 롱 시그널 ──────────────────────────────────────────
if 가격 < EMA20:
    signal = "exit_warning"             ← 추세 이탈 (롱 청산)

elif 가격 > EMA20 AND EMA기울기 >= ema_slope_entry_min AND RSI 40~65
     AND regime_trending AND trending_score >= 1:
    if long_high_proximity_blocked:
        signal = "long_high_proximity"  ← 최근 고점 근접 — 추격 진입 차단 (2026-05-07 추가)
    else:
        signal = "long_setup"           ← 롱 진입 (2026-04-29 entry_ok에서 rename)

elif 가격 > EMA20 AND EMA기울기 > 0 AND RSI > 65:
    signal = "long_overheated"          ← 과매수, 눌림목 대기 (2026-04-29 wait_dip에서 rename)

elif 가격 > EMA20 AND EMA기울기 > 0 AND regime_ranging:
    signal = "ranging"              ← 명확한 횡보 구간, 진입 차단

# ── 숏 시그널 ──────────────────────────────────────────
elif 가격 > EMA20:  # 숏 포지션 청산 (가격 > EMA = 상승, 숏에 불리)
    signal = "short_caution"            ← 숏 추세 이탈 경고

elif 가격 < EMA20 AND EMA기울기 < short_slope_th AND RSI 35~60
     AND regime_trending AND trending_score >= 1:
    if short_low_proximity_blocked:
        signal = "short_low_proximity"  ← 최근 저점 근접 — 추격 진입 차단 (2026-05-07 추가)
    else:
        signal = "short_setup"          ← 숏 진입 (2026-04-29 entry_sell에서 rename)

elif RSI < 35:
    signal = "short_oversold"           ← 과매도, 반등 위험 (숏 진입 억제)

else:
    signal = "no_signal"

regime_trending = (BB_width ≥ bb_width_trending_min)  ← bb_width가 현재 변동성 싦줜 측정, 시장 상태 즉응
regime_ranging  = BB_width < bb_width_ranging_max AND range < range_pct_ranging_max  ← 명확한 횟보, 진입 차단
# unclear = trending도 ranging도 아닌 중간 영역 → 진입 허용 (EMA+RSI 필터가 충분)
# 기본값: bb_width_trending_min=3.0, bb_width_ranging_max=3.0, range_pct_ranging_max=5.0
# (2026-04-18 재보정, 2026-04-21 재설계)
# range_pct_trending_min은 trending 판정에서 제거됨 (lookback window max-min sticky 특성 문제).
# range_pct는 ranging 판정 보수적 확인(range_pct_ranging_max)에만 사용.
# 구현 노트 (2026-04-21): bb_width 단돁 trending 재설계.
#   range_pct는 lookback window 내 max-min 기반으로 무거운 바가 window를 빠져나가기 전까지 sticky.
#   bb_width(표준편차 기반)는 현재 분산에 즉각 반응 → 시장 상태를 정확히 반영.
#   ranging 판정에는 range_pct 유지 (명확한 횟보는 둘 다 좌아야 함 → 박스 전략 신중함 유지).
#   compute_trend_signal() / analysis_service.get_market_regime() 모두 signals.py::classify_regime() 호출.
```

#### 시그널 코드 일람 (보고서·로그 표시 기준)

> 보고서에 나오는 시그널 코드의 한국어 의미 대조표.
> 구현 위치: `core/shared/monitoring/report_formatter.py` `SIGNAL_KR` / `_EXIT_TRIGGER_KR`

**진입 시그널**

| 표시 설명 | 코드 |
|---------|------|
| 매수 진입 조건 전부 충족 (EMA 위 · 기울기 양수 · RSI 눌림목 · 추세 체제) | `long_setup` |
| 공매도 진입 조건 전부 충족 (EMA 아래 · 기울기 음수 · RSI 눌림목 · 추세 체제) | `short_setup` |

**진입 차단 시그널**

| 표시 설명 | 코드 |
|---------|------|
| 매수 조건 충족이나 최근 고점 바로 아래 — 반전 위험으로 진입 보류 | `long_high_proximity` |
| 공매도 조건 충족이나 최근 저점 바로 위 — 반등 위험으로 진입 보류 | `short_low_proximity` |
| 매수 조건 충족이나 RSI 과열 — 가격 눌림 기다리는 중 | `long_overheated` |
| 공매도 조건 충족이나 RSI 과매도 — 단기 반등 경계 중 | `short_oversold` |
| 시장 횡보 중 — BB폭 협소, 추세가 생길 때까지 관망 | `ranging` |

**경고 시그널 (포지션 보유 중이면 청산 트리거)**

| 표시 설명 | 코드 |
|---------|------|
| 가격이 이동평균선 아래로 하락 — 롱 보유 중이면 청산 경보 / 미보유면 공매도 진입 접근 중 | `long_caution` |
| 가격은 이동평균선 위이나 방향이 하락 전환 — 숏 보유 중이면 청산 경보 / 미보유면 공매도 전환 감시 | `short_caution` |

**대기 / 중립 시그널**

| 표시 설명 | 코드 |
|---------|------|
| 포지션 유지 — 진입/청산 조건 미충족 | `hold` |
| 신호 없음 | `no_signal` |

**청산 트리거 (exit trigger)**

| 표시 설명 | 코드 |
|---------|------|
| 이동평균선 방향 하락 전환 — 상승 추세 소진, 롱 청산 | `full_exit_ema_slope_long` |
| 이동평균선 방향 상승 전환 — 하락 추세 반전, 숏 청산 | `full_exit_ema_slope_short` |
| RSI 기준선 이하 이탈 — 모멘텀 소진, 강제 청산 | `full_exit_rsi_breakdown` |
| 전략 정책에 의한 청산 | `full_exit` |
| 이동평균선 아래로 하락 — 롱 청산 | `long_caution` |
| 이동평균선 위로 반등 — 숏 청산 | `short_caution` |
| 손절선 도달 — 즉시 청산 | `stop_loss` |
| 추적 손절선 도달 — 이익 보호 청산 | `trailing_stop` |
| 증거금 위험 수위 도달 — 강제 청산 | `risk_cut` |
| 이익 구간에서 재진입 조건 불성립 — 조기 청산 | `profit_early_exit` |
| 예상 박스 패턴 무효화 — 청산 | `preview_invalidated` |
| 시스템 레벨 청산 명령 | `orchestrator_exit` |

### 4.2 Task 1 — CandleMonitor (60초 주기)

매 사이클마다 DB에서 최신 완성 캔들 조회 → 시그널 재계산.

- **잔고 정합성 검사**: 30분마다 1회 `get_balance()` 실잔고 ↔ 인메모리 비교. 1% 초과 괴리 시 인메모리 갱신. 포지션 없음 / paper pair 시 스킵.

#### entry_timeframe=1h (P1 — 판단 빈도 4×, 2026-05-02 추가)

`entry_timeframe="1h"` 파라미터 설정 시 시그널 계산 로직이 달라진다:

```
기본 (entry_timeframe=None):
  basis_timeframe 캔들(4H)로 EMA/slope/RSI/regime/trending_score 모두 계산

entry_timeframe="1h" 시:
  ① EMA / slope / RSI → 1H 캔들로 계산 (4H보다 4배 빠른 판단)
  ② regime / trending_score → 4H 캔들로 계산 (오버라이드)
  ③ latest_candle_open_time → 4H 기준 유지 (RegimeGate 멱등성 보존)
  ④ 진입 신호 발생 주기: 최대 4H → 1H (캔들 완성 시 바로 평가)

효과: 강한 추세에서 4H 캔들이 확정되기 전에 1H 기준으로 진입 기회 포착.
주의: 1H 노이즈에 취약 → entry_mode=ws_cross 또는 limit_then_market과 조합 권장.
```

#### 청산 우선순위 (포지션 보유 시)

```
우선순위 1: exit_warning (가격 < EMA20)
            → 전량 청산

우선순위 2: full_exit
            조건: EMA 기울기 < 0 (음전환)
                  OR RSI < 40 (과매도 급락)
            → 전량 청산

우선순위 3: tighten_stop  [부분 청산 없음 — Let winners run]
            조건: RSI > rsi_extreme (기본 80)
                  OR 미실현 이익 > ATR × partial_exit_profit_atr
                  OR RSI > rsi_overbought (기본 75)
                  OR EMA 기울기 둔화 (< ema_slope_weak_threshold)
            → 스탑로스를 ATR × tighten_stop_atr 으로 좁힘 (1회)

[Phase 2] EMA 기울기 3캔들 연속 하락 감지 (새 캔들 도착 시 검사)
            → 스탑 타이트닝 (우선순위 3과 동일 동작)

[Phase 3] RSI + 볼륨 베어리시 다이버전스 감지 (새 캔들 도착 시 검사)
          조건: 피봇 고점A → 고점B에서 (피봇 간 거리 ≤ max_pivot_distance 캔들)
          - RSI 다이버전스:
              가격 고점B > 고점A (신고가)
            + RSI 고점B < 고점A - rsi_divergence_min_gap (에너지 소진)
          - 볼륨 다이버전스:
              가격 고점B > 고점A (신고가)
            + 거래량 고점B < 거래량 고점A × (1 - volume_divergence_min_drop)
          - 이중 (both): RSI + 볼륨 동시 충족 → 높은 신뢰도 (로그에 "높은 신뢰도" 표시)
            → 스탑 타이트닝 (볼륨/RSI/이중 모두 동일 동작 — 로그로만 구분)

[안전 가드 — 2026-05-27] _apply_stop_tightening: new_sl이 현재가를 역전하는 경우 타이트닝 skip
            - 롱: new_sl ≥ current_price(WS 실시간) → WARNING 로그 후 skip (즉각 SL 발동 방지)
            - 숏: new_sl ≤ current_price(WS 실시간) → WARNING 로그 후 skip
            원인: 2026-05-27 사고 — SL ¥12,114,991 > 현재가 ¥12,030,758 → 즉각 청산(-¥337)
            구현: MarginBaseManager._apply_stop_tightening() (Bug #225)

우선순위 4: 적응형 트레일링 스탑 ratchet-up
            추세 상태 + 이익 크기 양쪽에서 ATR 배수를 동적 조정:
            - _stop_tightened=True  : min(tighten_stop_atr, profit_mult)
                tighten_stop_atr가 상한(ceiling), 이익이 더 크면 profit_mult가 더 좁으므로 그쪽 사용.
            - 그 외: min(adaptive_mult, profit_mult) 사용
                adaptive_mult (추세 상태 기반):
                  - 성숙/과열 (RSI>75 OR 기울기<0.03%): ATR × trailing_stop_atr_mature (기본 1.2)
                  - 초기/가속 (그 외)               : ATR × trailing_stop_atr_initial (기본 1.5)
                profit_mult (이익 크기 기반 **선형 연속 감쇠**):
                  mult = max(trailing_stop_atr_min, trailing_stop_atr_initial - trailing_stop_decay_per_atr × profit_atr_ratio)
                  - profit_atr_ratio = unrealized / atr
                  - 이익 0     → 1.5 (initial)
                  - 이익 ATR×1 → 1.3
                  - 이익 ATR×3 → 0.9
                  - 이익 ATR×6 → 0.3 (floor)
                  이익이 조금이라도 생기면 즉시 배수가 줄기 시작함 (3-step 계단식 제거).
            손익분기 바닥 (breakeven floor):
              이익 >= ATR × breakeven_trigger_atr (기본 1.0) 이면
              스탑 ≥ 진입가 보장 (롱) / 스탑 ≤ 진입가 보장 (숏)
            → 기존 스탑보다 유리한 방향일 때만 갱신 (단방향 ratchet)

    [GMO Coin 전용] 거래소 ロスカットレート 동기화:
            스탑이 실제로 갱신된 경우, changeLosscutPrice API로 거래소 건옥의
            ロスカットレート를 즉시 동기화 (GmoCoinTrendManager._sync_losscut_price).
            피라미딩 복수 건옥은 get_positions()로 전체 조회 후 각각 동기화.
            실패 시 WARNING만 — 인메모리 스탑 로직에 영향 없음.
            효과: 봇 다운 / WS 끊김 시에도 거래소 자체 강제청산이 작동하는 최종 안전망.
```

#### 실시간 가격 보정

```
캔들모니터는 60초마다 4H 캔들을 확인하지만,
StopLossMonitor가 캐시한 실시간 가격을 참조하여
exit_warning을 즉각 보정한다.

if 실시간가격 < EMA20:
    signal = "exit_warning" 로 오버라이드 (4H 시그널과 무관)
```

#### 진입 (포지션 없을 때)

```
if signal == long_setup / short_setup:
    ① entry_mode=market  → 즉시 market_buy / market_sell
    ② entry_mode=limit   → limit_price = 현재가 ∓ ATR × limit_offset_atr_ratio
                           limit order 발주 → _pending_limit_orders[pair] 저장
                           미체결 → 매 60초 체결 확인
                           limit_timeout_sec 초과 → 자동 취소 (fallback 없음)
    ③ entry_mode=limit_then_market
                         → limit order 먼저 시도. 타임아웃 + 시그널 유효 시 market fallback
    ④ entry_mode=ws_cross (2026-05-02 추가)
                         → 60초 캔들 루프에서 ②③④⑤ 조건 충족 시 arm 상태 설정.
                           StopLossMonitor WS 틱에서 현재가가 EMA를 돌파하는 순간
                           _trigger_ws_entry fire-and-forget → limit_then_market 실행
                           armed 상태가 armed_expire_sec 초 경과하면 자동 disarm

ws_cross 진입 조건 ②~⑤ (CandleMonitor 60초 루프에서 평가):
    ② regime = trending AND trending_score >= 1
    ③ EMA 기울기 조건 충족 (숏: slope < short_slope_th, 롱: slope >= 0)
    ④ RSI 범위 조건 충족
    ⑤ 시그널이 long_setup / short_setup
    → 충족 시: _armed_entry_ema, _armed_direction, _armed_expire_at 설정
    → 미충족 시: armed 해제

limit order 관리 (매 60초 사이클):
    - COMPLETED → _finalize_limit_entry: 포지션 등록 + DB 기록
    - 시그널 변경 (exit_warning 등) → 즉시 cancel_order
    - 타임아웃 초과 → cancel_order → 즉시 market fallback (ws_cross 한정)
    - PENDING/OPEN → 다음 사이클 대기

invest_jpy = 가용 JPY × position_size_pct%
초기 스탑로스 = entry_price ∓ ATR × atr_multiplier_stop
```

#### WS 실시간가 ↔ EMA 방향 일치 검증 (진입 최종 가드, 2026-05-24 추가)

4H 캔들 종가(reference_price) 기반 시그널은 stale 할 수 있다.
진입 직전 WS 실시간가(`_latest_price[pair]`)와 EMA를 재비교해 방향이 모순이면 차단.

```
숏 신호 + WS > EMA  → 진입 차단  (이미 EMA 위 → 체결 즉시 ema_above_ws 청산 예정)
롱 신호 + WS < EMA  → 진입 차단  (이미 EMA 아래 → 체결 즉시 price_below_ema_ws 청산 예정)
WS 미수신(None)     → 폴백, 차단 안 함 (기존 4H 기반 동작 유지)
```

구현: `GmoCoinTrendBase._ws_ema_direction_ok()` (오버라이드) + `ExecutionMixin._on_entry_signal()` (훅 호출)
로그: `[EntryGuard] {pair}: WS ¥X vs EMA ¥Y → 숏/롱 진입 차단 (stale 4H 신호)`

#### ~~미완성 캔들 프리뷰 진입~~ (제거됨 — 2026-04-27)

> `preview_entry_enabled`, `preview_min_tick_count`, `entry_preview` 시그널, `is_preview` 플래그 전량 제거됨.
> entry_preview dead code는 `_candle_loop`, `_execution_mixin`, `rule_based`, `jit_advisory` 전 경로에서 삭제.

### 4.3 Task 2 — StopLossMonitor (WS 틱 실시간)

```
WS 채널:
  GMO Coin: /public/v1/trades?symbol=BTC_JPY

매 틱마다:
  1. 실시간 가격 캐시 갱신 (_latest_price)
  2. **EMA 이탈 즉각 청산 (2026-05-15 추가)**:
     - 포지션 보유 중 WS 틱에서 EMA 이탈 실시간 감지
     - 롱: price < EMA − cushion → price_below_ema_ws 정리 (판단 SSoT: _is_ema_exit_triggered)
     - 숏: price > EMA + cushion → ema_above_ws 정리
     - 60초 캔들 루프보다 빠르게 즉각 감지/청산
     - cushion, cooling period 판단은 _is_ema_exit_triggered()에 위임 (4H 루프와 SSoT 공유)
  3. 포지션 있음 AND 스탑로스 설정되었음?
     - 현재가 ≤ 스탑로스 (롱) 또는 현재가 ≥ 스탑로스 (숏) → SL 발동 후보
     - **재진입 게이트** (2026-05-06 추가): _should_execute_sl()로 진입 조건 3개 검사
         · 가격 vs EMA (롱: price > EMA / 숏: price < EMA)
         · EMA 기울기 (롱: slope ≥ ema_slope_entry_min / 숏: slope < ema_slope_short_threshold)
         · trending_score ≥ trending_score_entry_min (기본 1)
       → **셋 다 충족** = SL 직후 즉시 재진입할 상황 → SL 억제 (다음 틱에서 재평가)
       → **하나라도 미충족** → 즉시 market 청산 (하드 스탑)
       RSI는 의도적으로 제외 (과매수/과매도는 곳 정상화되면 재진입 조건이 되어 부적절)
       캐시 미존재(재시작 직후) → 보수적으로 SL 발동 허용
     - **SL 청산 후 재진입 쿨다운** (2026-05-28 추가 — Bug #226): SL 청산 확정 후 30분(1800초) 재진입 차단
         · _sl_reentry_cooldown_until[pair] = now + ExitReason.STOP_LOSS.reentry_cooldown_sec(1800)
         · _runtime_cooldown_until = max(_close_fail_until, _sl_reentry_cooldown_until)로 guard chain 주입
         · 재시작 시 초기화됨 (의도된 동작 — 인메모리 임시 방어)
         원인: 2026-05-23 5번 연속 SL — SL 청산 직후 동일 방향 즉시 재진입 반복
     - **진입가 오기록 수정** (2026-05-27 추가 — Bug #227): MARKET 주문 체결 후 실제 체결가를 get_executions로 확인
         · 기존: order.price or price → MARKET 주문 응답에 체결가 없음(None) → WS 캐시 가격 사용 → 1분 딜레이 오기록
         · 수정: get_executions(order_id) → executionPrice 사용, 조회 실패 시 WS 가격 폴백
         · 진입(_open_position) + 피라미딩(add_position) 2경로 모두 적용
         원인: 2026-05-27 사고 — entry_price=¥12,114,991 vs 실제 체결가=¥12,077,156 (¥37,835 오차)
         구현: GmoCoinBaseManager._open_position (gmo_coin_base.py)
  4. entry_mode=ws_cross AND armed 상태? (2026-05-02 추가)
     - _armed_entry_ema, _armed_direction 설정됨
     - 숏: 현재가 ≤ _armed_entry_ema → _trigger_ws_entry(fire-and-forget)
     - 롱: 현재가 ≥ _armed_entry_ema → _trigger_ws_entry(fire-and-forget)
     - armed_expire_at 경과 시 → 자동 disarm (_armed_direction = None)
     - 진입 완료 후 → disarm

_trigger_ws_entry 흐름:
  limit @ EMA ± entry_limit_offset_atr × ATR 발주
  → limit_timeout_sec 내 미체결 → 즉시 market fallback
  → COMPLETED → 포지션 등록 + SL 동기화 + disarm
```

---

### 4.4 Task 3 (제거됨 — 2026-03-16)

기존 PartialExitMonitor (15분 주기, 1H RSI 기반 부분 청산) 제거.

**이유**: 레이첼 분석 — "Let winners run" 원칙. 조기 부분 청산은 강한 상승장에서
수익 기회를 크게 축소한다. RSI 80은 강한 추세에서 수주 유지될 수 있음.

RSI + 볼륨 다이버전스 기반 스탑 타이트닝은 Phase 3으로 CandleMonitor(Task 1) 내부에 구현됨.
부분 청산(포지션 분할 매도)은 여전히 하지 않는다 — 스탑만 조인 뒤 추세 이탈 시 전량 청산.

---

### 4.5 부분 청산 (_partial_close_position)

> **현재 미사용.** Phase 3은 스탑 타이트닝만 수행(포지션 유지) — 부분 매도 없음.

---

### 4.7 Whipsaw Protection (2026-04-19)

연속 손실 분석 결과 추가된 휩쏘(급등 후 급반전) 보호 장치.

#### (A) 4H 캔들 교체 후 cooling period

```
새 4H 캔들이 감지되면 _last_candle_change_time[pair] 타임스탬프를 기록.
이후 candle_change_cooling_sec(기본 300초) 동안 exit_warning 발동을 억제.

이유: 캔들 교체 직후는 시가 변동폭이 크고, exit_warning이 노이즈로 발동될 가능성이 높음.
     cooling period 동안에는 exit_warning 대신 no_signal로 처리.
```

#### (B) 진입 직후 grace period

```
포지션 진입 후 entry_grace_period_sec(기본 900초=15분) 동안
기울기 하락(EMA slope 3캔들 연속 하락) 및 다이버전스에 의한 tighten_stop 억제.

이유: 진입 직후 자연스러운 조정에서 스탑이 즉시 좁혀지는 것을 방지.
     opened_at 없으면 패스 (포지션 복원 시 grace period 미적용).

적용 로직:
    if pos.opened_at and (now - pos.opened_at).seconds < entry_grace_period_sec:
        → tighten_stop 스킵 (기울기 하락 / 다이버전스 양쪽 모두)
```

#### (C) ema_slope_weak_threshold 조정

```
기본값 0.03 → 0.05로 상향.

⚠️ 주의: 이 값 상향은 tighten_stop 발동 범위를 오히려 확장함 (역효과 가능).
   실질 보호는 개선 B의 grace period가 담당.
   ema_slope_weak_threshold는 성숙 판정 경계값으로 사용되므로
   FX처럼 slope 변동이 작은 시장에서는 항상 mature 판정 가능성에 주의.
```

#### (D) exit_warning ATR 쿠션 + 숏 방향 버그 수정

```
_check_exit_warning()에 exit_ema_atr_cushion(기본 0.1) 적용:

    롱: price < ema - atr × cushion  → exit_warning
    숏: price > ema + atr × cushion  → exit_warning

이유: 단순 EMA 크로스 직후 즉시 exit_warning 발동을 방지.
     ATR의 10%만큼 쿠션을 두어 노이즈성 크로스를 필터링.

숏 방향 버그 수정 (2026-04-19):
    기존: price < ema → exit_warning  (숏 포지션에서 EMA 위에 있으면 유리)
    수정: price > ema + cushion → exit_warning  (숏에게 불리한 방향으로 이탈 시만 발동)

SSoT 리팩토링 (2026-05-15):
    쿠션+cooling period+side 분기 로직을 _is_ema_exit_triggered()으로 집약.
    _check_exit_warning (4H 루프)와 _stop_loss_monitor (WS 틱) 양쪽에서 호출.
```

#### ERR-578: GMO Coin ロスカットレート 동기화 실패 처리

```
원인: GMO Coin changeLosscutPrice API가 현재 거래소 설정 로스컷보다
      낮은(롱) 또는 높은(숏) 값으로의 변경을 거부.
      에러: "Specify losscutprice greater than X"

대응:
    - 인메모리 스탑로스는 그대로 유지 (봇의 소프트 스탑은 정상 작동)
    - logger.warning 레벨로 기록 (기존 info → warning 상향)
    - asyncio.ensure_future(_send_telegram("⚠️ SL 동기화 실패")) 비동기 경고 발송
    - 거래소 강제청산(ロスカット)은 기존 설정값으로 유지됨 — 안전망은 작동

봇 로직에 영향 없음 — StopLossMonitor의 하드 스탑은 인메모리 기준으로 동작.

SL 신뢰성 강화 (2026-05-01, Phase 1~4):
  Phase 1: _handle_losscut_sync_failure() 메서드 분리
  Phase 2: _sync_losscut_price(max_retries=3) 재시도 (0.5s×attempt 간격)
  Phase 3: _open_position 진입 즉시 + _add_to_position 피라미딩 후 losscut 동기화
  Phase 4: _detect_existing_position 오버라이드 — DB SL 복원 + 거래소 재동기화
```

---

### 4.9 피라미딩 손실 차단 게이트 (2026-05-15)

```
add_to_position() 호출 시 pyramid_min_profit_pct 검사:

    현재 포지션 수익률
    롱: (current_price - entry_price) / entry_price × 100
    숏: (entry_price - current_price) / entry_price × 100

    수익률 < pyramid_min_profit_pct(기본 0.0%) → 피라미딩 차단
    수익률 ≥ pyramid_min_profit_pct → 피라미딩 허용

이유: 손실 포지션에 추가 포지션을 싸애도 평균단가가 낮아질 뿐, 전체 리스크가 컵집함.
기본값 0.0: 손실 포지션(수익률 < 0%)에는 절대 추가하지 않음.
```

### 4.10 DB orphan open 정리 (2026-05-15)

```
_record_close() 호출 시:

    DB에서 현재 pair의 status='open' 레코드를 전수 조회.
    현재 정리하는 position_id가 아닌 레코드가 있으면:
        status = 'orphan_closed', closed_at = now()
        ← 재시작/코드 실패 등으로 미청산된 DB 레코드가 open로 남는 것을 방지.
```

### 4.6 포지션 복원 (서버 재시작)

```
1. _detect_existing_position():
   - 거래소 잔고 조회
   - 해당 통화 amount > 0.001 이면 포지션 존재로 판단
   - entry_price는 None (불확실) → stop_loss_price도 None
   - 다음 캔들 시그널 계산 시 스탑로스 재설정

2. _recover_db_position_id():
   - DB에서 status="open" 포지션 레코드 조회
   - ID 복원 → 이후 청산 시 기존 레코드에 기록
```

---

### 4.8 4H Entry Gate 캐싱 (2026-04-19)

매 60초 `_candle_monitor()` 실행 시 `_compute_signal()`을 매번 호출하던 구조를
4H 캔들 교체 시에만 계산하고 이후 캐시를 사용하도록 변경.

```
_4h_signal_cache[pair]:  4H 캔들 교체 시에만 _compute_signal() 실행
                         이후 캔들 key 동일하면 캐시 반환

캐시 히트 시 보정:
  - current_price: WS 실시간 가격(_latest_price)으로 덮어씀
  - exit_signal: realtime price 기반으로 재계산 (profit_target 정확도 유지)

포지션 보유 중 entry signal 치환:
  - long_setup, short_setup, long_overheated, long_caution, ranging → hold
  - 이유: 캐시된 entry 판단이 포지션 보유 상태에서 재발동되는 것을 방지

캐시 초기화 타이밍:
  - 새 4H 캔들 감지 시 (candle_key 변경)
  - 전략 stop() 시
```

효과: `_compute_signal()` 호출 빈도 매분 → 4H당 1회로 대폭 감소.
관련 테스트: `tests/unit/test_signal_cache.py` (12케이스 — SC/EF/RE)

---

## 5. 전략 파라미터 (strategy.parameters)

| 파라미터 | 기본값 | 설명 |
|---------|-------|------|
| `trading_style` | `"trend_following"` | 전략 유형 식별자 |
| `pair` | `"btc_jpy"` | 거래 대상 (GMO Coin: 소문자) |
| `basis_timeframe` | `"4h"` | 시그널 계산 캔들 주기 |
| `position_size_pct` | `60` | JPY 가용 잔고 대비 투입 비율 (%) |
| `atr_multiplier_stop` | `2.0` | 초기 스탑로스: 현재가 - ATR × 이 값 |
| `trailing_stop_atr_initial` | `1.5` | 적응형 트레일링 초기 배수 (이익 0일 때) |
| `trailing_stop_decay_per_atr` | `0.2` | 이익 ATR 1배당 배수 감소량 (선형 감쇠) |
| `trailing_stop_atr_min` | `0.3` | profit_mult 하한값 (floor) |
| `trailing_stop_atr_mature` | `1.2` | adaptive_mult 성숙/과열기 배수 (RSI>75 OR 기울기<weak_threshold) |
| `tighten_stop_atr` | `1.0` | tighten 발동 시 배수 상한(ceiling) — 이익이 크면 profit_mult가 더 좁아질 수 있음 |
| `breakeven_trigger_atr` | `1.0` | 손익분기 바닥 발동 임계값: 이익 >= ATR × 이 값 이면 스탑 ≥ 진입가 보장 |
| `profit_mult_threshold_1` | ~~1.0~~ | ⚠️ **제거됨** — 선형 감쇠로 대체 |
| `profit_mult_threshold_2` | ~~2.0~~ | ⚠️ **제거됨** — 선형 감쇠로 대체 |
| `rsi_overbought` | `75` | 과매수 임계값 (tighten_stop 트리거) |
| `rsi_extreme` | `80` | 극단 과매수 임계값 (tighten_stop 트리거) |
| `rsi_breakdown` | `40` | 급락 (전량 청산) 임계값 |
| `ema_slope_weak_threshold` | `0.05` | EMA 기울기 둔화 임계값 (%) — tighten_stop 트리거 및 adaptive_mult 성숙 판정 경계. **주의: 값이 클수록 mature 판정 빈도 증가 → trailing_stop_atr_initial 무효화 가능성** |
| `candle_change_cooling_sec` | `300` | 4H 캔들 교체 후 exit_warning 억제 기간(초) — 캔들 교체 직후 노이즈성 exit_warning 방지 |
| `entry_grace_period_sec` | `900` | 진입 직후 tighten_stop 억제 기간(초) — 진입 후 15분간 기울기 하락·다이버전스 tighten_stop 발동 금지 |
| `exit_ema_atr_cushion` | `0.1` | EMA 대비 exit_warning 발동 완충 배수 — `price < ema - atr×cushion` 조건으로 노이즈 크로스 필터링 |
| `partial_exit_profit_atr` | `2.0` | 이익 목표 ATR 배수 — tighten_stop 트리거 (부분 청산 아님) |
| `jpy_floor` | `1000` | 최소 투입 JPY |
| `divergence_enabled` | `true` | RSI + 볼륨 다이버전스 감지 ON/OFF (Phase 3) |
| `pivot_left` | `2` | 피봇 좌측 비교 캔들 수 |
| `pivot_right` | `2` | 피봇 우측 비교 캔들 수 (확정까지 `right×4h` 지연) |
| `rsi_divergence_min_gap` | `3.0` | RSI 고점 차이 최소값 (노이즈 필터) |
| `max_pivot_distance` | `15` | 두 피봇 간 최대 캔들 거리 (4H×15=60시간) |
| `divergence_lookback` | `40` | 피봇 탐색 캔들 수 (4H×40≈7일, `_compute_signal` limit 결정에도 사용) |
| `volume_divergence_enabled` | `true` | 볼륨 다이버전스 감지 ON/OFF (divergence_enabled와 독립) |
| `volume_divergence_min_drop` | `0.15` | 거래량 최소 감소율 (0.15=15%, 노이즈 필터) |
| `ema_slope_entry_min` | `0.0` | EMA slope 진입 최소 임곗값 (%) — 현물은 음수 허용으로 조기 진입 가능 |
| `trending_score_entry_min` | `1` | trending_score 진입 최소값 — SL 재진입 게이트(Task 2)에서도 동일 기준 사용 |
| `entry_mode` | `"market"` | 진입 주문 방식: `"market"` / `"limit"` / `"limit_then_market"` / `"ws_cross"` |
| `entry_timeframe` | `None` | 시그널 계산 캔들 주기 오버라이드: `"1h"` → 1H 캔들로 EMA/slope/RSI 계산, 4H 체제 유지. `None`이면 `basis_timeframe` 사용 (2026-05-02 추가) |
| `limit_offset_atr_ratio` | `0.15` | limit order 가격 오프셋 = ATR × 이 값 (매수이므로 현재가 아래) |
| `limit_timeout_sec` | `300` | limit order 미체결 시 자동 취소까지 대기 시간 (초) |
| `entry_limit_offset_atr` | `0.05` | ws_cross 지정가 오프셋 배수 — EMA ± ATR × 이 값으로 limit 발주 (ws_cross 전용, 2026-05-02 추가) |
| `armed_expire_sec` | `14400` | ws_cross armed 상태 유효 시간(초). 경과 시 자동 disarm (2026-05-02 추가) |
| ~~`preview_entry_enabled`~~ | ~~`false`~~ | ⚠️ **제거됨 (2026-04-27)** — entry_preview dead code 전량 제거 |
| ~~`preview_min_tick_count`~~ | ~~`3`~~ | ⚠️ **제거됨 (2026-04-27)** |
| `high_proximity_lookback` | `120` | 롱 고점 근접 차단 lookback 봉 수 (4H×120≈20일) |
| `high_proximity_min_atr` | `2.0` | 롱 고점 근접 차단 ATR 배수 (2026-05-15: 1.0→2.0) |
| `short_low_lookback` | `180` | 숏 저점 근접 차단 lookback 봉 수 (4H×180≈30일) — 2026-05-28 추가: 롱보다 긴 기간 |
| `short_prox_min_atr` | `3.0` | 숏 저점 근접 차단 ATR 배수 — 2026-05-28 추가: 롱(2.0)보다 보수적 (1개월 신저가 숏 차단) |
| `bb_width_trending_min` | `4.0` | 체제 판정 trending 최소 BB width (%) — **trending 판정 유일 지표** (2026-05-09: 3.0→4.0 상향) |
| `range_pct_trending_min` | `6.0` | trending_score +1 임계값 — `compute_trending_score` 내부에서 사용. `classify_regime` trending 판정에는 미사용 (2026-04-21 제거) |
| `atr_pct_trending_min` | `1.0` | trending_score ATR% 임계값 (+1). 2026-05-15 실측 교정: 구 2.5%는 역사적 2.3% 충족 → 1.0%는 69.3% 충족 |
| `slope_pct_trending_min` | `0.10` | trending_score \|EMA slope\|% 임계값 (+1). 2026-05-15 실측 교정: 구 4.0%는 역사적 0% 충족(4H 최대 1.3%) → 0.10%는 57.6% 충족 |
| `bb_width_ranging_max` | `3.0` | 체제 판정 ranging 최대 BB width (%) |
| `range_pct_ranging_max` | `5.0` | 체제 판정 ranging 최대 range (%) — BB 수축 경로 |
| `range_tight_ranging_max` | `2.5` | ranging 폴백 임계값 (%) — 박스 미감지 시 가격 밀집 기준 |
| `box_range_coverage_mult` | `1.2` | 박스 감지 시 ranging 임계값 배수 — `range_pct < box_width × 이 값` |

> **제거된 파라미터**: `trailing_stop_atr` (1.5 고정) → `trailing_stop_atr_initial/mature`로 교체.
> `profit_mult_threshold_1/2` (3-step 계단식) → `trailing_stop_decay_per_atr` + `trailing_stop_atr_min` (선형 감쇠)로 교체.
> `partial_exit_rsi_pct`, `partial_exit_profit_pct` — 부분 청산 제거로 미사용.
> **체제 판정 재설계 (2026-04-21)**: trending = bb_width_pct 단돁 판정으로 변경. range_pct는 sticky 특성(스타일: lookback max-min 기반) 문제로 trending 조건에서 제거. ranging 판정의 보수적 확인에만 유지.
> **체제 판정 기본값 (2026-04-18 추가 재보정)**: bb_width_trending_min=4.0 (2026-05-09 3.0→4.0 상향), range_pct_ranging_max=5.0, bb_width_ranging_max=3.0
> **체제 판정 개선 (2026-05-14)**: ranging (b) 임계값 동적화. 박스 감지 시 `range_pct < box_width_pct × box_range_coverage_mult` (기본 1.2)로 판정. 넓은 박스에서 가격이 박스 안에 머물면 ranging 인정. 박스 미감지 시 기존 `range_tight_ranging_max = 2.5%` 폴백 유지.

---

## 6. 데이터 모델 (DB)

### 포지션 테이블 (`gmoc_trend_positions`)

> ⚠️ `ck_trend_positions` / `bf_trend_positions` 구버전 테이블 — 삭제됨 (2026-04-25)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | Serial PK | |
| `pair` | String | 거래 대상 (`btc_jpy`) |
| `side` | String | `"BUY"` / `"SELL"` (롱/숏) |
| `entry_order_id` | String | 진입 주문 ID |
| `entry_price` | Decimal | 진입가 |
| `entry_amount` | Decimal | 진입 수량 |
| `entry_jpy` | Decimal | 투입 JPY |
| `exit_order_id` | String | 청산 주문 ID (nullable) |
| `exit_price` | Decimal | 청산가 (nullable) |
| `exit_amount` | Decimal | 청산 수량 (nullable) |
| `exit_jpy` | Decimal | 회수 JPY (nullable) |
| `exit_reason` | String | 청산 사유 코드 (nullable) |
| `realized_pnl` | Decimal | 실현 손익 |
| `trailing_stop_price`| Decimal | 현재 트레일링 스탑 가격 |
| `status` | String | `open` / `closed` |
| `created_at` | DateTime | |

### 인메모리 포지션 상태

```python
_position[pair] = {
    "entry_price": float,      # 진입가
    "entry_amount": float,     # 현재 보유 수량
    "stop_loss_price": float,  # 현재 스탑로스 가격
}
# + _db_position_id[pair] = int         (DB 레코드 PK)
# + _latest_price[pair] = float         (WS 실시간 가격 캐시)
# + _stop_tightened[pair] = bool        (스탑 타이트닝 발동 여부)
# + _ema_slope_history[pair] = list     (최근 3캔들 EMA 기울기 이력, Phase 2)
# + _ema_slope_last_key[pair] = str     (마지막 처리한 캔들 open_time, Phase 2+3 중복 실행 방지)
# + _last_atr[pair] = float             (마지막 계산된 ATR — ws_cross 지정가 계산용)
# ws_cross 전용 armed 상태 (2026-05-02 추가)
# + _armed_entry_ema[pair] = float|None (진입 대기 EMA 가격, None=비활성)
# + _armed_direction[pair] = str|None   ('long' | 'short' | None)
# + _armed_expire_at[pair] = float      (unix timestamp, armed_expire_sec 후 만료)
```

---

## 7. GMO Coin 어댑터 (GmoCoinAdapter)

> ⚠️ CoincheckAdapter / BitFlyerAdapter — 2026-04-15 삭제. GMO Coin 단일 거래소 운영.

| 항목 | 값 |
|------|----|
| 식별자 | `pair` (소문자: `btc_jpy`) |
| ORM 테이블 | `gmoc_trend_positions` |
| 주문 시그니처 | HMAC-SHA256 (`timestamp + METHOD + path + body`) |
| MARKET_BUY | `amount=JPY 금액` 전달 |
| MARKET_SELL | `amount=coin_size` 전달 |
| 포지션 종료 | MARKET_SELL (롱) / MARKET_BUY (숏) |
| 레버리지 SL | `changeLosscutPrice` API로 거래소 건옥 동기화 |

> 상세: `adapters/gmo_coin/client.py`

---

## 8. 안전장치 (Safety Mechanisms)

| 장치 | 설명 |
|------|------|
| 하드 스탑로스 | WS 틱 기반 즉시 청산 (60초 지연 없음) |
| 적응형 트레일링 스탑 ratchet-up | 추세 상태에 따라 ATR 배수 동적 조정 (초기 2.0 / 성숙 1.2) |
| 스탑 타이트닝 | 과매수/기울기 둔화/이익 목표 감지 시 스탑 간격 축소 |
| **EMA 기울기 경고** | **3캔들 연속 기울기 하락 → 스탑 타이트닝 (Phase 2)** |
| **RSI 다이버전스 경고** | **피봇 고점 가격↑+RSI↓(gap≥3) → 스탑 타이트닝 (Phase 3)** |
| **볼륨 다이버전스 경고** | **피봇 고점 가격↑+거래량↓(15%+) → 스탑 타이트닝 (Phase 3)** |
| **이중 다이버전스 경고** | **RSI+볼륨 동시 → 높은 신뢰도 스탑 타이트닝 (Phase 3)** |
| 실시간 가격 보정 | 4H 캔들 대기 없이 EMA 이탈 즉시 감지 |
| 포지션 복원 | 서버 재시작 시 잔고 기반 자동 복원 |
| **거래소 ロスカットレート 동기화** | **(GMO Coin 전용)** 트레일링 스탑 갱신 시 `changeLosscutPrice`로 거래소 건옥 즉시 동기화. 봇 다운 / WS 끊김 시 거래소 자체 강제청산 작동 |
| **ERR-578 처리** | **(GMO Coin 전용)** 로스컷 동기화 실패 시 인메모리 SL 유지 + WARNING 로그 + 텔레그램 경고. 소프트 스탑은 정상 작동 |
| **4H 캔들 교체 cooling** | 캔들 교체 직후 `candle_change_cooling_sec`초 exit_warning 억제 — 노이즈성 추세 이탈 오탐 방지 |
| **진입 grace period** | 진입 후 `entry_grace_period_sec`초 tighten_stop 억제 — 자연스러운 조정에서 스탑 즉시 좁힘 방지 |
| **exit_warning ATR 쿠션** | `exit_ema_atr_cushion` 배수만큼 쿠션 적용. 롱: `price < ema - atr×cushion`, 숏: `price > ema + atr×cushion` |
| dust 처리 | min_coin_size 미만 잔고는 포지션 없음으로 취급 + DB 종료 |
| 최소 투입 금액 | `invest_jpy < jpy_floor` 시 진입 스킵 |
| regime 필터 | `regime_trending=False` 시 진입 거부. trending = `BB_width >= bb_width_trending_min`(기본 3.0%). ranging 확정 시 추가 차단 (`BB_width < 3.0% AND range < 5.0%`) |

---

## 9. 청산 사유 코드

| 코드 | 설명 |
|------|------|
| `exit_warning` | 가격 < EMA20 — 추세 이탈 |
| `full_exit_ema_slope` | EMA 기울기 음전환 — 추세 반전 |
| `full_exit_rsi_breakdown` | RSI < 40 — 급락 |
| `stop_loss` | 하드 스탑로스 (WS 실시간) |
| ~~`partial_exit_rsi_extreme`~~ | ~~RSI > 80 — 극단 과매수 부분 청산~~ (제거됨) |
| ~~`partial_exit_profit_target`~~ | ~~이익 > ATR×2 — 부분 청산~~ (제거됨) |

> 부분 청산 사유 코드는 사용하지 않는다. Phase 3(RSI + 볼륨 다이버전스)는 스탑 타이트닝만 발동 — 포지션 유지 후 추세 이탈 시 전량 청산.

---

## 10. 소스 파일 맵 (signum-engine)

| 파일 | 경로 |
|------|------|
| 전략 매니저 | `core/strategy/plugins/gmo_coin_trend/manager.py` |
| 판단 믹스인 | `core/strategy/_judge_mixin.py` |
| 실행 믹스인 | `core/strategy/_execution_mixin.py` |
| 캔들 루프 | `core/strategy/_candle_loop.py` |
| 시그널 함수 | `core/strategy/signals.py` (`compute_trend_signal`, `compute_trending_score`) |
| 체제 판정 | `core/strategy/signals.py` (`classify_regime`) |
| WS 진입 트리거 | `core/strategy/plugins/gmo_coin_trend/manager.py` (`_trigger_ws_entry`, `_open_position_limit`) |
| 거래소 어댑터 | `adapters/gmo_coin/client.py` |
| ORM 모델 | `adapters/database/models.py` (`create_trend_position_model`) |
| 태스크 관리 | `core/task/supervisor.py` (`TaskSupervisor`) |
| SL 신뢰성 | `plugins/gmo_coin_trend/manager.py` (`_sync_losscut_price`, `_handle_losscut_sync_failure`) |
| JIT Advisory | `core/judge/jit_advisory/gate.py` (`JitAdvisoryGate`) |
| 헬스 모니터링 | `core/monitoring/health.py` (`HealthChecker`) |
| API 라우트 | `api/routes/` |
| 엔트리포인트 | `main.py` (`EXCHANGE=gmo_coin` 고정) |

---

## 11. 관련 문서

- [박스권 역추세 전략 설계](BOX_MEAN_REVERSION.md) — 횡보장 전환 시 사용
- [GMO Coin 운영 가이드](GMO_COIN.md)

---

## 12. AI-Native 통합 — JIT Advisory Gate (2026-05-01 구현)

### 12.1 현재 상태 (`TRADING_MODE=jit`)

`ExecutionOrchestrator`가 진입 시그널(`long_setup` / `short_setup`) 감지 시 `JitAdvisoryGate`를 호출.
OpenClaw Rachel에 단발 LLM 자문을 요청하고 결과에 따라 진입 허용/거부/조절.

| 결과 | 동작 |
|------|------|
| `GO` | 진입 허용 (size=params 그대로) |
| `NO_GO` | 진입 거부 |
| `ADJUST` | size 재조정 (advisory.size_pct 적용) |
| timeout / fail | fail-soft GO — size × 0.7 으로 진입 허용 |

> RegimeGate bypass: `TRADING_MODE=jit` + warm-up 완료 시 `_jit_bypass_gate=True`. JIT Advisory가 실질적 억제 담당.
> exit/tighten/hold 시그널은 JIT 호출 없이 통과.

### 12.2 DB 기록

| 테이블 | 설명 |
|--------|------|
| `jit_advisories` | 자문 요청/응답 이력 (pair, direction, go/no_go, size_pct, reasoning) |
| SL 동적 변경 없음 | force_exit=true → 즉시 전량 청산 |

**적용 방식**: `_candle_monitor` 매 루프에서 advisory의 `trailing_atr_multiplier`가 있으면 adaptive trailing 계산 결과를 override.
