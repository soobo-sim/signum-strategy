---
status: approved
author: 아키, 큐니 검증
created_at: 2026-05-10
updated_at: 2026-05-10
---

# 전략 파라미터 그룹 사양서

> **설계 배경**: 전략의 모든 파라미터를 역할별 객체(그룹)로 분리해 DB에 영구 보존한다.
> `gmoc_param_strategies` 테이블이 각 그룹을 FK로 참조하며, 환경변수 `STRATEGY_ID`로 전략을 선택한다.
> 코드에서는 `ParamStrategy.merged_params()`가 모든 그룹을 하나의 flat dict로 병합해 반환하므로
> 기존 `params.get("key")` 패턴을 그대로 사용할 수 있다.

---

## 전략 구성 다이어그램

```
STRATEGY_ID (환경변수)
      │
      ▼
gmoc_param_strategies          ← 전략 레코드 1개
      │
      ├─ indicator_params_id   ──▶ gmoc_indicator_params       [A] 지표 계산
      ├─ regime_params_id      ──▶ gmoc_regime_params           [B] 체제 판정
      ├─ stop_params_id        ──▶ gmoc_stop_params             [E] 스탑 관리
      ├─ execution_params_id   ──▶ gmoc_execution_params        [H] 실행 조건
      ├─ exchange_params_id    ──▶ gmoc_exchange_constraint_params [I] 거래소 제약
      ├─ risk_guard_params_id  ──▶ gmoc_risk_guard_params       [J] 외부 리스크 (nullable)
      │
      │  [추세 전략만]
      ├─ trend_entry_params_id ──▶ gmoc_trend_entry_params      [C] 추세 진입
      ├─ trend_exit_params_id  ──▶ gmoc_trend_exit_params       [D] 추세 청산
      │
      │  [박스 전략만]
      ├─ box_detect_params_id  ──▶ gmoc_box_detect_params       [F] 박스 감지
      └─ box_entry_exit_params_id ▶ gmoc_box_entry_exit_params  [G] 박스 진입·청산
```

> 두 전략이 동일한 파라미터 객체를 **공유 가능**하다.
> 예: 추세·박스 전략이 모두 같은 `IndicatorParams(id=1)` 참조 가능.

---

## [A] IndicatorParams — 지표 계산 파라미터

> **역할**: RSI, EMA, ATR, 볼린저밴드를 계산할 때 몇 개의 캔들을 기준으로 할지를 결정한다.
> 지표 계산의 기초 단계이므로, 모든 판단(체제·진입·청산·스탑)이 이 값에서 출발한다.

| 파라미터 | 기본값 | 단위 | 설명 |
|---------|--------|------|------|
| `ema_period` | 20 | 캔들 수 | EMA(지수이동평균) 계산 기간. 값이 클수록 느린 평균선 → 주요 추세 파악용. 작을수록 빠른 평균선 → 단기 방향 파악용. |
| `atr_period` | 14 | 캔들 수 | ATR(평균 실제 범위) 계산 기간. 최근 N캔들의 평균 변동폭을 산출. 스탑 배치·포지션 사이즈 계산에 사용됨. |
| `rsi_period` | 14 | 캔들 수 | RSI(상대강도지수) 계산 기간. 14캔들 기준이 업계 표준. 너무 짧으면 노이즈, 너무 길면 신호 지연. |
| `bb_period` | 20 | 캔들 수 | 볼린저밴드 계산 기간. 상단·하단밴드 폭(bb_width)이 체제 판정에 사용됨. |

**DB 테이블**: `gmoc_indicator_params`

---

## [B] RegimeParams — 체제 판정 파라미터

> **역할**: 현재 시장이 "추세장"인지 "횡보장"인지를 판별하는 기준값을 정의한다.
> 이 판정 결과에 따라 추세 전략 또는 박스 전략의 진입을 허용할지 결정한다.
>
> 판정 기준은 두 가지 지표를 조합한다:
> - **BB폭** (bb_width): 볼린저밴드 상단과 하단의 차이 %. 폭이 넓으면 변동성 큰 추세장.
> - **Range%** (range_pct): 최근 N캔들 고가-저가 차이 / 현재가. 범위가 넓으면 추세장.

### 추세장 판정 조건

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `bb_width_trending_min` | 4.0 | BB폭이 이 값 이상이면 "추세장 가능성" 있음. 볼린저밴드가 벌어진 정도 %. |
| `range_pct_trending_min` | 6.0 | Range%가 이 값 이상이면 "추세장" 확정. BB폭과 AND 조건. |

### 횡보장 판정 조건

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `bb_width_ranging_max` | 3.0 | BB폭이 이 값 미만이면 "횡보장 가능성" 있음. |
| `range_pct_ranging_max` | 5.0 | Range%가 이 값 이하이면 "횡보장" 후보. |
| `range_tight_ranging_max` | 2.5 | Range%가 이 값 이하이면 "좁은 횡보장" 확정. (더 엄격한 기준) |

> **판정 흐름**: `bb_width >= trending_min AND range_pct >= trending_min` → 추세장  
> `bb_width < ranging_max AND range_pct <= ranging_max` → 횡보장  
> 둘 다 아니면 → unclear (진입 보류)

**DB 테이블**: `gmoc_regime_params`

---

## [C] TrendEntryParams — 추세 진입 조건 파라미터

> **역할**: 추세장으로 판정된 상황에서 "지금 이 가격에 진입해도 되는가"를 결정하는 세부 조건들이다.
> RSI 범위, EMA 기울기, 고가 근접도, 다이버전스 감지 기준 등을 포함한다.

### 롱(매수) 진입 조건

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `entry_rsi_min` | 35.0 | 롱 진입 가능한 RSI 하한. RSI가 이 값보다 낮으면 너무 약한 상태 → 진입 차단. |
| `entry_rsi_max` | 65.0 | 롱 진입 가능한 RSI 상한. RSI가 이 값보다 높으면 이미 과매수 구간 → 진입 차단. |
| `ema_slope_entry_min` | 0.08 | 롱 진입에 필요한 최소 EMA 기울기 (상승률). 값이 클수록 더 강한 상승 추세에서만 진입. |

### 숏(매도) 진입 조건

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `entry_rsi_min_short` | 35.0 | 숏 진입 가능한 RSI 하한. |
| `entry_rsi_max_short` | 60.0 | 숏 진입 가능한 RSI 상한. 롱보다 좁게 설정 (숏은 더 보수적). |
| `ema_slope_short_threshold` | -0.05 | 숏 진입에 필요한 최대 EMA 기울기 (음수 = 하락). 이 값보다 더 음수여야 숏 진입 허용. |

### 고가 근접도 필터 (High Proximity)

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `high_proximity_lookback` | 120 | 최근 몇 캔들 내의 고가를 "저항선"으로 볼지. 120봉 × 4H ≈ 20일 (2026-05-15 변경: 20→120). |
| `high_proximity_min_atr` | 2.0 | 현재가에서 최근 고가까지의 거리가 ATR × 이 값 이상이어야 롱 진입 허용. 저항선 바로 밑에서 진입하는 것을 방지. (2026-05-15 변경: 1.0→2.0). |
| `swing_resistance_lookback` | 90 | Swing High 저항선 감지 lookback 캔들 수. |
| `swing_resistance_tolerance_pct` | 1.5 | 동일 저항선으로 묶을 고가 클러스터 허용 범위 (%). |
| `swing_resistance_min_touches` | 2 | 저항선으로 인정할 최소 터치 횟수. |
| `swing_resistance_pivot_window` | 3 | 로컬 최고점(pivot) 검출 윈도우 크기. |
| `swing_resistance_price_zone_pct` | 2.0 | 현재가 기준 이 % 이내에 저항선이 있으면 진입 차단. |

### 절대 고점/저점 ATR 버퍼 차단 (Absolute Extrema)

> 3개월 기간 중 최고가/최저가 근방에서는 R:R(리스크 대비 수익 비율)이 불량하므로 신규 진입을 영구 불허한다.
> 롱: 현재가 > (3개월 최고가 − ATR × N) → 차단
> 숏: 현재가 < (3개월 최저가 + ATR × N) → 차단

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `absolute_extrema_lookback` | 540 | 절대 고점/저점을 계산할 과거 캔들 수. 540봉 × 4H = 약 3개월. 데이터 부족 시 차단하지 않음. |
| `absolute_extrema_atr_buffer` | 2.0 | 절대 고점/저점에서 ATR × N 이내로 진입 시 차단. N이 클수록 더 멀리서부터 차단. |

### 추세 강도 필터

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `trending_score_entry_min` | 1 | 복합 추세 점수(EMA slope + BB width 등 여러 지표 합산)가 이 값 이상이어야 진입. |

### Limit 주문 오프셋

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `limit_offset_atr_ratio` | 0.15 | 추세 전략에서 limit 주문 발행 시 현재가 대비 ATR × 이 비율만큼 유리한 방향으로 오프셋. |

### 다이버전스 감지 (Divergence)

> 가격이 신고가를 갱신했는데 RSI는 신고가를 못 찍으면 → 하락 다이버전스(숏 신호).
> 가격이 신저가를 갱신했는데 RSI는 신저가를 못 찍으면 → 상승 다이버전스(롱 신호).

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `divergence_enabled` | True | 다이버전스 감지 기능 전체 활성화 여부. |
| `pivot_left` | 2 | 피봇 포인트(고점/저점) 감지 시 왼쪽 확인 캔들 수. 좌우 N개 캔들 중 최고(최저)이어야 피봇 인정. |
| `pivot_right` | 2 | 피봇 포인트 감지 시 오른쪽 확인 캔들 수. |
| `rsi_divergence_min_gap` | 3.0 | 두 피봇 간 RSI 차이가 이 값 이상이어야 다이버전스로 인정. 노이즈 필터. |
| `max_pivot_distance` | 15 | 두 피봇 간 최대 캔들 거리. 너무 오래된 피봇은 무효화. |
| `divergence_lookback` | 40 | 다이버전스 탐색 최대 캔들 범위. |

**DB 테이블**: `gmoc_trend_entry_params`

---

## [D] TrendExitParams — 추세 청산 조건 파라미터

> **역할**: 보유 중인 추세 포지션을 "언제 청산할지"를 결정하는 조건들이다.
> RSI 과매수/과매도 수준, EMA 기울기 약화, 부분청산 트리거 기준을 포함한다.

### 롱 포지션 청산 조건 (RSI 기반)

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `rsi_overbought` | 75.0 | RSI가 이 값을 초과하면 스탑을 타이트닝(수익 보호 강화). 과열 신호. |
| `rsi_extreme` | 80.0 | RSI가 이 값을 초과하면 즉시 전량 청산. 극단적 과매수. |
| `rsi_breakdown` | 40.0 | RSI가 이 값 미만으로 추락하면 추세 붕괴로 판단 → 즉시 청산. |

### 숏 포지션 청산 조건 (RSI 기반, 롱과 대칭)

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `rsi_oversold` | 25.0 | RSI가 이 값 미만이면 스탑 타이트닝. (롱의 `rsi_overbought` 반전값) |
| `rsi_oversold_extreme` | 20.0 | RSI가 이 값 미만이면 즉시 전량 청산. (롱의 `rsi_extreme` 반전값) |
| `rsi_breakout_short` | 60.0 | RSI가 이 값 초과하면 숏 추세 붕괴 → 즉시 청산. (롱의 `rsi_breakdown` 반전값) |

### EMA 기울기 약화 판정

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `ema_slope_weak_threshold` | 0.05 | EMA 기울기가 이 값 미만으로 떨어지면 추세 약화로 판단. 단독으로 청산하지는 않고 다른 조건과 결합. |
| `exit_ema_atr_cushion` | 0.1 | EMA 기반 청산 판단 시 ATR × 이 값만큼 쿠션을 줌. 미세 노이즈에 의한 오청산 방지. |

### 부분청산 (Partial Exit) 조건

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `partial_exit_profit_atr` | 2.0 | 수익이 ATR × 이 값에 도달하면 부분청산 검토. |
| `profit_mult_threshold_1` | 1.0 | 1단계 부분청산 수익 배수. ATR × 1.0 수익 시 포지션 일부 익절. |
| `profit_mult_threshold_2` | 2.0 | 2단계 부분청산 수익 배수. ATR × 2.0 수익 시 추가 익절. |

**DB 테이블**: `gmoc_trend_exit_params`

---

## [E] StopParams — 스탑 관리 파라미터

> **역할**: 추세·박스 전략 공통으로 사용되는 손절(Stop Loss) 설정이다.
> 초기 SL 배치, 트레일링 스탑 강도, 손익분기점 이동 조건, SL 발동 후 재진입 조건을 포함한다.

### 초기 스탑 배치

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `atr_multiplier_stop` | 2.0 | 진입 시 초기 SL 거리 = ATR × 이 배수. 추세 전략 기본값. **박스 전략은 별도 레코드에서 1.5 사용.** |

### 트레일링 스탑 (Trailing Stop)

> 이익이 쌓이면 SL을 점점 유리한 방향으로 이동시켜 수익을 보호한다.

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `trailing_stop_atr_initial` | 1.5 | 포지션 초기(미성숙)의 트레일링 SL 거리 = ATR × 이 배수. 넓게 유지해 노이즈에 의한 조기 손절 방지. |
| `trailing_stop_atr_mature` | 1.2 | 포지션이 충분히 수익을 확보한 후(성숙)의 트레일링 SL 거리. 좁혀서 수익 보호 강화. |
| `trailing_stop_atr_min` | 0.3 | 트레일링 SL 거리의 최소값. 아무리 좁혀져도 이 이하로는 내려가지 않음. |
| `trailing_stop_decay_per_atr` | 0.2 | 수익이 ATR 1단위 늘어날수록 트레일링 SL 배수를 이 값만큼 줄임. 수익 누적 → SL 점진적 타이트닝. |
| `tighten_stop_atr` | 1.0 | 과매수/과매도 신호 시 SL을 타이트닝할 때 사용하는 ATR 배수. |

### 손익분기점 이동 (Breakeven)

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `breakeven_trigger_atr` | 1.0 | 수익이 ATR × 이 값에 도달하면 SL을 진입가(손익분기점)로 이동. 이후 최악의 경우 본전 보장. |

### SL 발동 후 재진입 억제

> 트레일링 스탑에 맞고 청산된 직후에는 반대 방향 추세가 아닌 한 무분별한 재진입을 막는다.

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `trailing_stop_reentry_slope_min` | 0.03 | 트레일링 스탑 청산 후 재진입하려면 EMA 기울기가 이 값 이상이어야 함. 약한 추세에서 재진입 차단. |
| `trailing_stop_reentry_window_sec` | 3600 | 트레일링 스탑 청산 후 이 시간(초) 이내에는 재진입 억제 적용. (기본 1시간) |

**DB 테이블**: `gmoc_stop_params`  
**주의**: 박스 전략용 레코드는 `atr_multiplier_stop=1.5` 로 별도 생성.

---

## [F] BoxDetectParams — 박스 감지 파라미터

> **역할**: 횡보장에서 "박스권이 형성되었는가"를 판별하는 기준이다.
> 박스권이란 가격이 일정 범위(상단·하단) 안에서 반복적으로 왕복하는 구간을 말한다.
> 박스가 인정되어야 박스 전략 진입이 가능하다.

### 박스 형성 조건

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `box_lookback` | 60 | 박스 감지를 위해 최근 몇 캔들을 분석할지. 너무 짧으면 박스를 못 찾고, 너무 길면 오래된 구간이 포함됨. |
| `box_tolerance_pct` | 0.5 | 박스 상단·하단을 클러스터(군집)로 인정할 때 허용 오차 %. 고점들이 서로 이 % 이내에 있으면 같은 저항선으로 봄. |
| `box_min_touches` | 3 | 박스 상단(저항) 또는 하단(지지)에 가격이 최소 몇 번 접촉해야 유효한 박스로 인정. |
| `box_min_width_pct` | 1.0 | 박스 폭(상단-하단 차이)이 최소 이 % 이상이어야 유효. 너무 좁은 박스는 수익 기회가 없어 제외. |
| `box_cluster_percentile` | 100.0 | 고가·저가 클러스터 계산 시 극단값 필터링 percentile. 100이면 전체 사용(필터 없음). |

### 박스 무효화 조건 — 수렴 삼각형 감지

> 박스가 형성된 것처럼 보이지만 실제로 고가가 낮아지고 저가가 높아지는 "수렴" 구간이면,
> 박스가 아니라 삼각수렴이므로 박스 전략 진입을 막는다.

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `triangle_lookback` | 20 | 삼각수렴 감지를 위해 분석할 캔들 수. |
| `triangle_min_candles` | 8 | 삼각수렴으로 판정하기 위한 최소 캔들 수. 이보다 적으면 수렴 패턴으로 보지 않음. |

**DB 테이블**: `gmoc_box_detect_params`

---

## [G] BoxEntryExitParams — 박스 진입·청산 파라미터

> **역할**: 박스권이 확인된 상황에서 "어느 위치에서 진입하고, 어느 위치에서 청산할지"를 결정한다.
> 박스 하단 근처에서 롱, 박스 상단 근처에서 숏을 진입한다.

### 경계 밴드 설정

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `near_bound_pct` | 0.5 | 박스 상단·하단에서 이 % 이내에 가격이 있을 때 "경계 근처"로 판정. 진입·청산 신호 발생 조건. |
| `tolerance_pct` | 0.5 | 박스 이탈 허용 오차 %. 가격이 박스 밖으로 이 % 이내 벗어나는 것은 이탈로 보지 않음(노이즈 처리). |

### RSI 진입 필터

> 박스 하단 근처여도 RSI가 너무 높으면 아직 매도 압력이 강한 상태 → 롱 진입 차단.

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `rsi_box_long_max` | 40.0 | 박스 롱 진입 시 RSI 상한. RSI가 이 값보다 높으면 진입 차단. (과매수 방향으로 진입 차단) |
| `rsi_box_short_min` | 60.0 | 박스 숏 진입 시 RSI 하한. RSI가 이 값보다 낮으면 진입 차단. (과매도 방향으로 진입 차단) |

### 박스 전용 SL 계산

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `box_sl_cushion_pct` | 0.5 | 박스 경계 기반 SL 배치 시 쿠션 %. 하단 지지선에서 이 % 아래에 SL 배치. 노이즈 돌파에 의한 오손절 방지. |
| `box_trailing_atr_mult` | 3.0 | 박스 전략의 트레일링 SL ATR 배수. 추세 전략(1.2~1.5)보다 느슨하게 설정. 박스 전략은 급격한 추세 추종이 목적이 아니므로 여유를 줌. |

**DB 테이블**: `gmoc_box_entry_exit_params`

---

## [H] ExecutionParams — 실행 조건 파라미터

> **역할**: 주문 실행의 방법·크기·쿨다운·킬스위치 등 "어떻게 매매할 것인가"를 결정한다.
> 시장 상황이 아닌 운영 정책에 해당한다.

### 포지션 사이즈

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `position_size_pct` | 70.0 | 가용 증거금 대비 포지션 사이즈 비율 %. 70%이면 잔고의 70%를 사용. 추세 전략 기본값. **박스 전략은 별도 레코드에서 50.0 사용.** |
| `min_order_jpy` | 1000 | 주문 최소 금액(JPY). 이 금액 미만이면 주문 발행 차단. |
| `pyramid_min_profit_pct` | 0.0 | 피라미딩(add_to_position) 허용 최소 수익률 %. 현재 포지션 수익률이 이 값 미만이면 피라미딩 차단. 기본값 0.0: 손실 포지션에는 절대 추가하지 않음. |

### 킬스위치 (자동 거래 정지)

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `kill_max_loss_jpy` | 5000 | 일일 누적 손실이 이 금액(JPY)을 초과하면 자동으로 거래 정지. |
| `kill_consecutive_losses` | 3 | 연속 손절 횟수가 이 값에 도달하면 자동 거래 정지. 연달아 잘못된 방향으로 진입하는 것을 방지. |

### 쿨다운 (재진입 억제)

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `entry_grace_period_sec` | 900 | 청산 후 다음 진입까지 최소 대기 시간(초). 기본 15분. 급하게 반대 방향 진입하는 것을 방지. |
| `candle_change_cooling_sec` | 300 | 새 캔들 확정 직후 진입 쿨다운(초). 캔들 전환 직후 지표가 아직 안정되지 않은 상태에서 진입 방지. |

### 주문 방식

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `entry_mode` | `market` | 주문 방식. `market`: 즉시 체결 시장가, `limit`: 지정가 주문, `ws_cross`: WebSocket 실시간 크로스 감지 후 진입. |
| `armed_expire_sec` | 14400 | `ws_cross` 모드에서 진입 대기(armed) 상태의 만료 시간(초). 기본 4시간. 이 시간이 지나면 armed 해제. |
| `limit_timeout_sec` | 300 | `limit` 주문 발행 후 이 시간(초) 안에 체결되지 않으면 주문 취소. 기본 5분. |
| `entry_limit_offset_atr` | 0.05 | `limit` 주문 시 현재가 대비 ATR × 이 비율만큼 유리한 방향으로 주문 가격 오프셋. |

**DB 테이블**: `gmoc_execution_params`

---

## [I] ExchangeConstraintParams — 거래소 제약 파라미터

> **역할**: GMO Coin 거래소의 사양과 리스크 관리 상한을 정의한다.
> 전략 튜닝이 아닌 "운영 환경 설정"에 가깝지만 DB에 보존해 추적 가능하게 한다.

### 거래소 사양 제약

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `max_leverage` | 1.5 | 최대 레버리지 배수. GMO Coin 레버리지 거래에서 허용된 최대값. 초과 진입 시 주문 차단. |
| `min_coin_size` | 0.001 | 최소 BTC 주문 수량. 이 수량 미만은 거래소가 거부하므로 사전 차단. |
| `max_slippage_pct` | 0.3 | 허용 최대 슬리피지 %. 시장가 주문 시 예상가 대비 이 % 이상 불리하면 주문 취소. |

### Keep Rate 모니터링 (마진 비율)

> Keep Rate = 현재 증거금 / 필요 증거금. 낮아질수록 강제청산 위험.

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `keep_rate_critical` | 1.3 | Keep Rate가 이 값 이하로 떨어지면 긴급 청산. 강제청산(= 거래소 강제 종료) 직전에 자체 청산. |
| `keep_rate_warn` | 1.5 | Keep Rate가 이 값 이하이면 경고 발생. Telegram 알림 + SL 타이트닝 검토. |

### 조기 이익청산 조건

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `min_profit_pct` | 0.15 | 조기 이익청산의 최소 수익률 %. SL 타이트닝 또는 청산 신호 발생 시 이 수익률 이상이어야 청산. 수수료 고려한 최소 수익 기준. |
| `commission_rate` | 0.001 | 수수료율(0.1%). snapshot 계산 시 실제 수익/손실 산출에 사용. |

**DB 테이블**: `gmoc_exchange_constraint_params`

---

## [J] RiskGuardParams — 외부 리스크 파라미터

> **역할**: 경제 이벤트(연준 금리결정, 고용지표 등), VIX 급등 등 외부 리스크 발생 시
> 진입을 차단하거나 스탑을 조이는 기준을 정의한다.
> `risk_guard_params_id`는 nullable이므로 사용하지 않을 경우 NULL로 설정하면 된다.

### 이벤트 블랙아웃 (진입 차단)

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `event_blackout_hours` | 4 | 고위험 이벤트(연준 FOMC, CPI 등) 전후 이 시간(시간) 동안 신규 진입 차단. |
| `event_blackout_medium_hours` | 2 | 중위험 이벤트(고용지표, 소매판매 등) 전후 이 시간(시간) 동안 진입 차단. |
| `event_post_blackout_minutes` | 30 | 이벤트 발표 후 시장 변동성이 가라앉길 기다리는 쿨다운(분). |

### 이벤트 시 SL 타이트닝

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `event_tighten_stop` | True | 이벤트 블랙아웃 기간 동안 기존 보유 포지션의 SL을 타이트닝할지 여부. |
| `event_tighten_factor` | 0.7 | SL 타이트닝 계수. 현재 SL 거리 × 0.7로 좁힘. 이벤트 충격에 빠르게 청산 가능하도록. |

### 인터마켓 편향 필터

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `intermarket_bias_enabled` | False | 인터마켓 편향 필터(VIX, DXY 등 연동 자산 분석) 활성화 여부. 기본 비활성. |
| `vix_stress_threshold` | 25.0 | VIX(공포지수)가 이 값을 초과하면 고위험 환경으로 판단 → 진입 억제 또는 사이즈 축소. |

**DB 테이블**: `gmoc_risk_guard_params`

---

## 파라미터 그룹별 적용 전략 매트릭스

| 그룹 | 추세 전략 | 박스 전략 | 공유 가능 |
|------|:---------:|:---------:|:---------:|
| [A] IndicatorParams | ✅ | ✅ | ✅ 동일 레코드 참조 가능 |
| [B] RegimeParams | ✅ | ✅ | ✅ 동일 레코드 참조 가능 |
| [C] TrendEntryParams | ✅ | ❌ nullable | ❌ 추세 전용 |
| [D] TrendExitParams | ✅ | ❌ nullable | ❌ 추세 전용 |
| [E] StopParams | ✅ | ✅ | ⚠️ 기본값 다름 → 별도 레코드 |
| [F] BoxDetectParams | ❌ nullable | ✅ | ❌ 박스 전용 |
| [G] BoxEntryExitParams | ❌ nullable | ✅ | ❌ 박스 전용 |
| [H] ExecutionParams | ✅ | ✅ | ⚠️ position_size_pct 다름 → 별도 레코드 |
| [I] ExchangeConstraintParams | ✅ | ✅ | ✅ 동일 레코드 참조 가능 |
| [J] RiskGuardParams | ✅ nullable | ✅ nullable | ✅ 동일 레코드 참조 가능 |

---

## 현재 운영 중인 전략 파라미터 (시드 데이터)

> `gmoc_param_strategies` 초기 시드 데이터. 기존 `gmoc_strategies.id=8`(추세), `id=7`(박스)에서 이식.

### 추세추종 전략 (`trading_style='trend_following'`, STRATEGY_ID=1)

| 그룹 | 레코드 이름 | 주요 설정값 |
|------|-----------|-----------|
| IndicatorParams | `default_indicators` | ema=20, atr=14, rsi=14, bb=20 |
| RegimeParams | `default_regime` | trending_min=4.0/6.0, ranging_max=3.0/5.0/2.5 |
| TrendEntryParams | `trend_entry_v1` | rsi=35~65, slope=0.08, divergence=on |
| TrendExitParams | `trend_exit_v1` | overbought=75, extreme=80, breakdown=40 |
| StopParams | `stop_trend` | atr_mult=2.0, trailing_initial=1.5, breakeven=1.0 |
| ExecutionParams | `exec_trend` | size=70%, kill=5000JPY/3연속, mode=market |
| ExchangeConstraintParams | `gmo_coin_constraints` | leverage=1.5, keep_critical=1.3 |

### 박스역추세 전략 (`trading_style='box_mean_reversion'`, STRATEGY_ID=2)

| 그룹 | 레코드 이름 | 주요 설정값 |
|------|-----------|-----------|
| IndicatorParams | `default_indicators` | (추세와 공유, id=1) |
| RegimeParams | `default_regime` | (추세와 공유) |
| StopParams | `stop_box` | **atr_mult=1.5** (추세와 다름), trailing_initial=1.5 |
| BoxDetectParams | `box_detect_v1` | lookback=60, tolerance=0.5%, min_touches=3 |
| BoxEntryExitParams | `box_entry_exit_v1` | near_bound=0.5%, rsi_long_max=40, cushion=0.5% |
| ExecutionParams | `exec_box` | **size=50%** (추세와 다름), kill=5000JPY/3연속 |
| ExchangeConstraintParams | `gmo_coin_constraints` | (추세와 공유) |

---

## 파라미터 이름 통일 이력

> 기존 코드·백테스트 간 이름 불일치를 이 설계에서 통일했다.

| 구 이름 | 새 이름 | 파일 |
|--------|--------|------|
| `jpy_floor` (DB) | `min_order_jpy` | ExecutionParams |
| `box_lookback_candles` (백테스트) | `box_lookback` | BoxDetectParams |
| `tolerance_pct` (실전) / `box_tolerance_pct` (백테스트) | `box_tolerance_pct` | BoxDetectParams |
| `min_touches` (실전) / `box_min_touches` (백테스트) | `box_min_touches` | BoxDetectParams |

> **하위 호환**: `merged_params()`에서 `box_tolerance_pct → tolerance_pct` alias를 자동 추가하므로
> 기존 코드의 `params.get("tolerance_pct")` 호출은 수정 없이 동작한다.

---

## 관련 코드 위치

| 항목 | 경로 |
|------|------|
| 파라미터 데이터클래스 (10개) | `signum-engine/core/shared/params/` |
| ParamStrategy 복합 객체 | `signum-engine/core/shared/params/strategy.py` |
| StrategyLoader | `signum-engine/core/shared/params/loader.py` |
| SQLAlchemy ORM 모델 | `signum-engine/core/shared/data/models/param_strategies.py` |
| Alembic 마이그레이션 | `signum-engine/alembic/versions/` |

---

*작성: 아키 (설계) · 큐니 (OOP 검증) | 2026-05-10*
