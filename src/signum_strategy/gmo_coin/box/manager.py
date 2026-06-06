
"""
GmoCoinBoxManager — GMO Coin 레버리지 박스역추세 매니저.

GmoCoinBaseManager 상속. GmoCoinTrendManager와 형제 관계.
GMO Coin 어댑터/주문 시맨틱은 그대로 재사용.
시그널 계산만 box_signals + box_detector 기반으로 오버라이드.

상속 체인:
    BaseStrategyManager → MarginBaseManager → GmoCoinBaseManager → GmoCoinBoxManager

핵심 차이:
    - _compute_signal: detect_box() → classify_price_in_box() 기반 시그널
    - _get_strategy_type: "box_mean_reversion" (RegimeGate 연동)
    - _task_prefix, _log_prefix: 박스 전용

시그널 매핑:
    near_lower  → "box_near_lower"   — 박스 하단 근처 → 롱 검토
    near_upper  → "box_near_upper"   — 박스 상단 근처 → 숏 검토
    outside     → "box_outside"      — 박스 이탈 상태 (청산 여부는 action이 결정)
    middle      → "no_signal"        — 박스 중간 → 대기
    box_none    → "no_signal"        — 박스 미감지 → 대기

파라미터 (params dict):
    near_bound_pct   (float, default 0.5): 경계 밴드 %
    box_tolerance_pct (float, default 0.5): 박스 클러스터 허용 오차 %  ← 통일된 이름 (구: tolerance_pct)
    box_min_touches  (int,   default 3):   최소 터치 횟수               ← 통일된 이름 (구: min_touches)
    box_lookback     (int,   default 60):  박스 감지 캔들 수
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select

from core.judge.analysis.box_detector import detect_box
from core.shared.box_signals import classify_price_in_box, check_box_invalidation, compute_triangle_apex_candles
from core.strategy.managers.gmo_coin_base import GmoCoinBaseManager
from core.shared.exchange.types import Position
from core.shared.logging.context import get_judge_cycle_id

logger = logging.getLogger("core.judge.box_signal")

_LOG_PREFIX = "[BoxMgr]"

# ── 기본 파라미터 상수 ──────────────────────────────────────────
_DEFAULT_NEAR_BOUND_PCT = 0.5
_DEFAULT_TOLERANCE_PCT = 0.5
_DEFAULT_MIN_TOUCHES = 3
_DEFAULT_BOX_LOOKBACK = 60


class GmoCoinBoxManager(GmoCoinBaseManager):
    """GMO Coin 레버리지 박스역추세 매니저. 롱/숏 양방향."""

    _task_prefix = "gmoc_box"
    _log_prefix = "[BoxMgr]"
    # _supports_short = True — GmoCoinBaseManager에서 상속

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    def _get_strategy_type(self) -> str:
        return "box_mean_reversion"

    def _make_regime_classifier(self):
        """박스권 전략 정책."""
        from signum_strategy.gmo_coin.shared.regime.classifier import GateRegimeClassifier
        from signum_strategy.gmo_coin.box.policies import BoxLongStop, BoxShortStop
        from signum_strategy.gmo_coin.box.policies import BoxLongExit, BoxShortExit
        from signum_strategy.gmo_coin.box.policies import BoxLongProfit, BoxShortProfit
        return GateRegimeClassifier(
            regime_gate=None,  # set_regime_gate() 에서 주입됨
            manager_type="box_mean_reversion",
            box_stop_long=BoxLongStop(),
            box_stop_short=BoxShortStop(),
            box_exit_long=BoxLongExit(),
            box_exit_short=BoxShortExit(),
            box_profit_long=BoxLongProfit(),
            box_profit_short=BoxShortProfit(),
        )

    def _valid_entry_signals(self) -> frozenset[str]:
        return frozenset({"box_near_upper", "box_near_lower"})

    def _build_record_kwargs(self, **kwargs) -> dict:
        """박스권 포지션 DB 컬럼 매핑."""
        result = {
            "entry_order_id": kwargs.get("order_id"),
            "entry_price": kwargs["price"],
            "entry_amount": kwargs["size"],
            "entry_jpy": round(kwargs.get("collateral_jpy", 0), 2),
            "trend_strategy_id": self._trend_strategy_id,
            "box_strategy_id": self._box_strategy_id,
        }
        if kwargs.get("stop_loss_price") is not None:
            result["stop_loss_price"] = kwargs["stop_loss_price"]
        return result

    def _long_reentry_signals(self) -> frozenset[str]:
        """박스 전략 롱 재진입 신호.

        box_near_lower: 박스 하단 근처 — 롱 진입 신호이므로 포지션 유지.
        long_overbought: RSI 임시 과매수 — 정상화 후 재진입 가능하므로 예외.
        """
        return frozenset({"box_near_lower", "long_overbought"})

    def _short_reentry_signals(self) -> frozenset[str]:
        """박스 전략 숏 재진입 신호.

        box_near_upper: 박스 상단 근처 — 숏 진입 신호이므로 포지션 유지.
        short_oversold: RSI 임시 과매도 — 정상화 후 재진입 가능하므로 예외.
        """
        return frozenset({"box_near_upper", "short_oversold"})

    def _is_ema_exit_triggered(self, pair: str, price: float, pos, ema: float, atr) -> bool:
        """박스 역추세 전략: WS 실시간 EMA 이탈 청산 비적용.

        박스 하단 진입(box_near_lower) 시 price < EMA 는 구조적으로 항상 성립.
        추세 전략의 EMA 이탈 청산을 박스 전략에 그대로 적용하면
        진입 직후 즉시 청산 → 재진입 무한 루프가 발생한다 (BUG #183).
        박스 전략의 청산은 TP/SL/박스 이탈 신호가 전담한다.
        """
        return False

    def _check_exit_warning(self, pair, signal, realtime_price, ema, pos, atr=None):
        """박스 역추세 전략: EMA 기반 exit_warning 비적용.

        박스 숏 진입 시 price > EMA 는 구조적으로 항상 성립한다 (박스 상단에서 진입).
        추세추종 전략의 EMA 이탈 경고(price_above_ema_slope_down / price_below_ema)는
        박스 전략에 적용하지 않는다.
        청산은 BoxExitBase 정책(목표가 도달 / 박스 이탈) 또는 ATR SL 이 담당한다.
        """
        return signal

    async def _detect_existing_position(self, pair: str) -> Optional[Position]:
        """box 전략의 기존 포지션 감지 — DB 게이트 추가.

        어댑터 get_positions()는 거래소 레벨에서 전략 구분 불가
        (trend가 연 BTC_JPY 포지션도 동일하게 반환).
        DB(gmoc_box_positions)에 미청산 레코드가 있을 때만 어댑터 포지션을 인식.
        """
        try:
            async with self._session_factory() as db:
                Model = self._position_model
                pair_col = getattr(Model, self._position_pair_column)
                stmt = (
                    select(Model.id)
                    .where(pair_col == pair)
                    .where(Model.realized_pnl_jpy.is_(None))
                    .limit(1)
                )
                result = await db.execute(stmt)
                has_db_position = result.scalar_one_or_none() is not None
        except Exception as e:
            logger.warning(
                f"{_LOG_PREFIX} {pair}: DB 미청산 포지션 조회 실패 → None 반환: {e}"
            )
            return None

        if not has_db_position:
            logger.debug(
                f"{_LOG_PREFIX} {pair}: DB에 box 미청산 포지션 없음 → 어댑터 포지션 무시"
            )
            return None

        # DB에 box 포지션이 있을 때만 어댑터 조회 위임
        return await super()._detect_existing_position(pair)

    # ──────────────────────────────────────────
    # 시그널 계산 (박스역추세)
    # ──────────────────────────────────────────

    async def _compute_signal(
        self,
        pair: str,
        timeframe: str,
        entry_price: Optional[float] = None,
        params: Optional[dict] = None,
        side: Optional[str] = None,
    ) -> Optional[dict]:
        """
        박스 감지 + 가격 위치 분류 기반 시그널.

        1) 부모 _compute_signal() 호출 → ATR/EMA/RSI + 캔들 목록 취득
        2) detect_box() 실행 — 최근 box_lookback개 캔들의 고/저가로 박스 감지
        3) classify_price_in_box() 로 현재가 위치 분류
        4) 시그널 매핑 후 반환 (ema_slope_pct = None → 기울기 이력 불필요)
        """
        p = params or {}

        # ① 부모 계산 — ATR/EMA/RSI + 캔들 목록
        base = await super()._compute_signal(
            pair, timeframe,
            entry_price=entry_price,
            params=p,
            side=side,
        )
        if base is None:
            return None

        candle_close: float = base["reference_price"]   # 4H 캔들 종가 — ATR/EMA 등 기술 분석 기준가
        ws_price: Optional[float] = self._latest_price.get(pair)  # WS 실시간가
        # 박스 위치 판단은 실시간가 기준이어야 한다.
        # ws_price가 None인 경우: 시스템 최초 기동 직후 WS 미수신, 또는 WS 재연결 중
        # → candle_close로 폴백 (기존 동작 유지)
        classification_price: float = ws_price if (ws_price and ws_price > 0) else candle_close
        atr: Optional[float] = base.get("atr")
        candles = base.get("candles") or []

        # ② 박스 감지
        lookback = int(p.get("box_lookback", _DEFAULT_BOX_LOOKBACK))
        # 통일된 이름 우선, 구 이름 폴백 (DB 마이그레이션 전까지 하위 호환)
        tolerance_pct = float(p.get("box_tolerance_pct", p.get("tolerance_pct", _DEFAULT_TOLERANCE_PCT)))
        min_touches = int(p.get("box_min_touches", p.get("min_touches", _DEFAULT_MIN_TOUCHES)))
        cluster_percentile = float(p.get("box_cluster_percentile", 100.0))
        min_width_pct = float(p.get("box_min_width_pct", 0.0))

        box_candles = candles[-lookback:] if len(candles) > lookback else candles
        highs = [float(c.high) for c in box_candles]
        lows = [float(c.low) for c in box_candles]

        box_result = detect_box(
            highs=highs,
            lows=lows,
            tolerance_pct=tolerance_pct,
            min_touches=min_touches,
            cluster_percentile=cluster_percentile,
        )

        # 최소 폭 미달 시 미감지 처리 (백테스트 엔진과 동일 로직)
        if box_result.box_detected and min_width_pct > 0 and (box_result.width_pct or 0) < min_width_pct:
            logger.debug(
                f"[BoxMgr] {pair}: 박스 폭 {box_result.width_pct:.3f}% < min_width_pct {min_width_pct}% → 미감지 처리"
            )
            from core.judge.analysis.box_detector import BoxDetectResult
            box_result = BoxDetectResult(
                box_detected=False,
                reason=f"폭 {box_result.width_pct:.3f}% < min_width {min_width_pct}%",
            )

        # ③ 시그널 결정
        _cid = get_judge_cycle_id()
        _cid_prefix = f"[Judge-Layer][{_cid}]" if _cid else "[Judge-Layer]"
        location = "none"  # box 미감지 시 기본값
        near_bound_pct = float(p.get("near_bound_pct", _DEFAULT_NEAR_BOUND_PCT))
        apex_candles_left: Optional[float] = None  # 수렴 삼각형 정점까지 남은 캔들 수
        invalidation: Optional[str] = None  # 박스 무효화 이유
        if not box_result.box_detected:
            signal = "no_signal"
            box_upper: Optional[float] = None
            box_lower: Optional[float] = None
            logger.debug(
                f"{_cid_prefix}{_LOG_PREFIX} {pair}: 박스 미감지 "
                f"(reason={box_result.reason}) → no_signal"
            )
        else:
            box_upper = box_result.upper_bound
            box_lower = box_result.lower_bound

            # ③-a: 박스 무효화 체크 — 진입/청산 조건 대칭화 (BUG-045 수정)
            # BoxExitBase.evaluate()와 동일한 check_box_invalidation을 진입 전에도 호출.
            # 수렴 삼각형(converging_triangle) 등 박스 무효화 상태에서는
            # 진입 신호를 no_signal로 억제하여 진입→즉시청산 churning을 방지.
            triangle_lookback = int(p.get("triangle_lookback", 20))
            invalidation = check_box_invalidation(
                check_price=classification_price,
                candle_highs=highs,
                candle_lows=lows,
                upper=box_upper,
                lower=box_lower,
                tolerance_pct=tolerance_pct,
                triangle_lookback=triangle_lookback,
            )
            if invalidation is not None:
                signal = "no_signal"
                # 실제 위치는 계산 유지 (리포트에서 "위치 불명" 대신 실제 위치 + 무효화 이유 표시)
                location = classify_price_in_box(
                    check_price=classification_price,
                    upper=box_upper,
                    lower=box_lower,
                    near_bound_pct=near_bound_pct,
                )
                # 수렴 삼각형: apex(정점)까지 남은 캔들 수 계산 (리포트 표시용)
                if invalidation == "converging_triangle":
                    apex_candles_left = compute_triangle_apex_candles(
                        candle_highs=highs,
                        candle_lows=lows,
                        lookback=triangle_lookback,
                    )
                logger.info(
                    f"{_cid_prefix}{_LOG_PREFIX} {pair}: "
                    f"박스 무효화({invalidation}) → 진입 신호 억제 no_signal "
                    f"box=¥{box_lower:,.0f}~¥{box_upper:,.0f} location={location}"
                    + (f" apex={apex_candles_left:.1f}봉" if apex_candles_left is not None else "")
                )
            else:
                location = classify_price_in_box(
                    check_price=classification_price,
                    upper=box_upper,
                    lower=box_lower,
                    near_bound_pct=near_bound_pct,
                )
                signal = _LOCATION_TO_SIGNAL[location]

                logger.debug(
                    f"{_cid_prefix}{_LOG_PREFIX} {pair}: "
                    f"박스 ¥{box_lower:,.0f}~¥{box_upper:,.0f} "
                    f"(폭 {box_result.width_pct:.2f}%) "
                    f"실시간가 ¥{classification_price:,.0f}"
                    f"{'(WS)' if ws_price else '(4H 종가 폴백)'}"
                    f" → {location} → {signal}"
                )

        # ④ 박스 폭 → range_pct (RegimeGate 로그용)
        range_pct = box_result.width_pct if box_result.box_detected else 0.0

        # ⑤ 진입 조건 상세 (SSoT: 추세 entry_conditions를 박스 전용으로 교체)
        box_entry_conditions: dict = {
            "type": "box",
            "reference_price": candle_close,
            "box_detected": box_result.box_detected,
            "box_upper": box_upper,
            "box_lower": box_lower,
            "box_width_pct": box_result.width_pct if box_result.box_detected else 0.0,
            "price_location": location,
            "near_bound_pct": near_bound_pct,
            "invalidation_reason": invalidation,  # None = 무효화 없음, 문자열 = 무효화 이유
            "apex_candles_left": apex_candles_left if invalidation == "converging_triangle" else None,
            "rsi": base.get("rsi"),
            "rsi_long_threshold": float(p.get("rsi_box_long_max", 40.0)),
            "rsi_short_threshold": float(p.get("rsi_box_short_min", 60.0)),
            # ranging 판정 2경로 표시용 (② 체제 게이트 섹션에서 현황 표시)
            "candle_bb_width_pct": float(base.get("bb_width_pct") or 0.0),
            "candle_range_pct": float(base.get("range_pct") or 0.0),
            "bb_trending_min": float(p.get("bb_width_trending_min", 4.0)),
            "bb_ranging_max": float(p.get("bb_width_ranging_max", 3.0)),
            "range_ranging_max": float(p.get("range_pct_ranging_max", 5.0)),
            "range_tight_ranging_max": float(p.get("range_tight_ranging_max", 4.5)),
            "box_range_coverage_mult": float(p.get("box_range_coverage_mult", 1.5)),
        }

        # 박스 폭 기반 regime 재판정 — RegimeGate 누적값으로 사용
        # 박스 감지 시: range_pct(고저 이동폭)와 박스 폭을 함께 넘겨 ranging 판정
        # 박스 미감지 시: base["regime"] (원래 계산값) 유지
        _base_bb = float(base.get("bb_width_pct") or 0.0)
        _base_range = float(base.get("range_pct") or 0.0)
        if box_result.box_detected and box_result.width_pct > 0:
            from core.shared.signals import classify_regime as _cr
            _regime_for_gate, _, _ = _cr(
                _base_bb,
                _base_range,
                p,
                box_width_pct=box_result.width_pct,
            )
        else:
            _regime_for_gate = base.get("regime", "unclear")

        return {
            **base,
            # 박스 전략 고유 시그널로 덮어쓰기
            "signal": signal,
            # 트렌드용 exit_signal 무효화 — base["exit_signal"]은 EMA slope 기반이며
            # 박스 전략에는 적용 불가 (slope=None이어도 rsi_breakdown 등이 잔류할 수 있음)
            # 박스 청산 판단은 rule_based.py가 signal(box_near_upper/outside 등)을 보고 결정
            "exit_signal": {},
            # 박스 메타 (리포트용)
            "box_upper": box_upper,
            "box_lower": box_lower,
            "box_detected": box_result.box_detected,
            # RegimeGate에 박스 폭 전달 (range_pct 덮어쓰기)
            "range_pct": range_pct,
            # 박스 폭 기반 regime 재판정값 → RegimeGate.update_regime()에 사용 (loop.py)
            # 넓은 박스에서도 ranging으로 판정되어 RegimeGate가 박스 전략을 활성화할 수 있음
            "regime": _regime_for_gate,
            # 박스 매니저는 EMA 기울기가 의미 없으므로 None 고정
            # → 부모 slope 이력 누적 없음 (None 넣으면 리스트에 쌓이지만 조건 판단 무시됨)
            "ema_slope_pct": None,
            # 진입 조건 상세 (추세 기반 entry_conditions를 박스 전용으로 교체)
            "entry_conditions": box_entry_conditions,
        }

    # ──────────────────────────────────────────
    # 지정가 진입 (박스역추세 전용)
    # ──────────────────────────────────────────

    async def _open_position_limit(
        self,
        pair: str,
        price: float,
        atr: Optional[float],
        params: dict,
        *,
        signal_data: dict | None = None,
    ):
        """박스 전략 지정가 진입.

        near_upper → 숏 지정가: box_upper * (1 - near_bound_pct/100)
        near_lower → 롱 지정가: box_lower * (1 + near_bound_pct/100)

        near_upper/lower 구간 경계에 지정가를 거므로:
        - 체결 시 항상 near_upper/lower 구간 내부에서 진입 보장
        - midpoint와의 완충 거리 확보 → breakeven SL 조기 발동 방지 (#196)
        """
        import time as _time
        from core.shared.exchange.types import OrderType, PendingLimitOrder

        sd = signal_data or {}
        signal = sd.get("signal", "")

        if signal == "box_near_upper":
            side = "sell"
        elif signal == "box_near_lower":
            side = "buy"
        else:
            logger.debug(f"{_LOG_PREFIX} {pair}: _open_position_limit — 박스 신호 아님 ({signal}), 스킵")
            return None

        box_upper = sd.get("box_upper")
        box_lower = sd.get("box_lower")
        if box_upper is None or box_lower is None:
            logger.warning(f"{_LOG_PREFIX} {pair}: box_upper/box_lower 없음 → box limit 진입 스킵")
            return None

        near_bound_pct = float(params.get("near_bound_pct", _DEFAULT_NEAR_BOUND_PCT))
        if side == "sell":
            # near_upper 구간 하한 = box_upper * (1 - near_bound_pct/100)
            # 이 가격 이상이 되어야 체결 → near_upper 구간 진입 보장
            limit_price = round(box_upper * (1.0 - near_bound_pct / 100.0), 0)
        else:
            # near_lower 구간 상한 = box_lower * (1 + near_bound_pct/100)
            # 이 가격 이하가 되어야 체결 → near_lower 구간 진입 보장
            limit_price = round(box_lower * (1.0 + near_bound_pct / 100.0), 0)

        if limit_price <= 0:
            logger.warning(f"{_LOG_PREFIX} {pair}: box limit_price={limit_price} 유효하지 않음")
            return None

        # ── 3-B: 실시간가 near zone 검증 ─────────────────────────────────────
        # 4H 캔들 종가가 near zone에 있어도 실시간가가 이미 zone을 이탈했으면
        # 지정가를 내도 체결 불가 → 5분 루프만 반복됨.
        # price(WS 실시간가)가 near_upper/lower 존 안에 있을 때만 주문 발행.
        if price and price > 0:
            if side == "sell" and price < limit_price:
                logger.info(
                    f"{_LOG_PREFIX} {pair}: [존 검증] WS ¥{price:.0f} < limit ¥{limit_price:.0f} "
                    f"— near_upper 존(¥{limit_price:.0f}~¥{box_upper:.0f}) 이탈, 지정가 진입 스킵"
                )
                return None
            if side == "buy" and price > limit_price:
                logger.info(
                    f"{_LOG_PREFIX} {pair}: [존 검증] WS ¥{price:.0f} > limit ¥{limit_price:.0f} "
                    f"— near_lower 존(¥{box_lower:.0f}~¥{limit_price:.0f}) 이탈, 지정가 진입 스킵"
                )
                return None

        # ── 안전장치 1: 방향 정합성 ───────────────────────────────────────────
        # 숏 지정가가 midpoint 이하이면 체결 즉시 breakeven SL 발동 위험 (#196)
        # near_bound_pct가 작거나 박스가 너무 좁을 때 발생할 수 있음
        midpoint = (box_upper + box_lower) / 2.0
        if side == "sell" and limit_price <= midpoint:
            logger.warning(
                f"{_LOG_PREFIX} {pair}: [안전장치] 숏 limit_price=¥{limit_price:.0f} ≤ midpoint=¥{midpoint:.0f} "
                f"→ breakeven SL 즉시 발동 위험, 박스 지정가 진입 취소 "
                f"(near_bound_pct={near_bound_pct}%, box_width=¥{box_upper - box_lower:.0f})"
            )
            return None
        if side == "buy" and limit_price >= midpoint:
            logger.warning(
                f"{_LOG_PREFIX} {pair}: [안전장치] 롱 limit_price=¥{limit_price:.0f} ≥ midpoint=¥{midpoint:.0f} "
                f"→ breakeven SL 즉시 발동 위험, 박스 지정가 진입 취소 "
                f"(near_bound_pct={near_bound_pct}%, box_width=¥{box_upper - box_lower:.0f})"
            )
            return None

        # ── 안전장치 2: midpoint 최소 거리 ─────────────────────────────────────
        # 지정가와 midpoint 사이가 박스폭의 min_limit_midpoint_cushion_pct 이상이어야 함
        # 기본 8%: 지정가 체결 후 WS 가격이 midpoint를 즉시 이탈해도 SL이 안전하게 유지
        box_width = box_upper - box_lower
        cushion_to_midpoint_pct = abs(limit_price - midpoint) / box_width * 100
        min_cushion_pct = float(params.get("min_limit_midpoint_cushion_pct", 8.0))
        if cushion_to_midpoint_pct < min_cushion_pct:
            logger.warning(
                f"{_LOG_PREFIX} {pair}: [안전장치] limit_price=¥{limit_price:.0f} — "
                f"midpoint 거리 {cushion_to_midpoint_pct:.1f}% < {min_cushion_pct:.0f}% "
                f"(box_width=¥{box_width:.0f}) → 지정가 진입 취소"
            )
            return None

        # ── 안전장치 3: WS 현재가 대비 지정가 괴리율 ───────────────────────────
        # 박스 경계가 오래된 캔들 기반으로 계산됐거나 급등락이 발생하면
        # 지정가가 실제 시장가와 크게 달라 비정상 체결이 발생할 수 있음
        # 기본 5%: near_upper 신호 시 limit_price ≈ 현재가이므로 5% 이상 차이면 이상
        _ws_price = self._latest_price.get(pair)
        if _ws_price and _ws_price > 0:
            _deviation_pct = abs(limit_price - _ws_price) / _ws_price * 100
            _max_dev_pct = float(params.get("max_limit_price_deviation_pct", 5.0))
            if _deviation_pct > _max_dev_pct:
                logger.warning(
                    f"{_LOG_PREFIX} {pair}: [안전장치] WS 현재가 ¥{_ws_price:.0f} ↔ 지정가 ¥{limit_price:.0f} "
                    f"괴리 {_deviation_pct:.1f}% > {_max_dev_pct:.0f}% "
                    f"— 박스 데이터 오래됨 또는 급등락, 지정가 진입 취소"
                )
                return None

        if not hasattr(self._adapter, "get_collateral"):
            logger.error(f"{_LOG_PREFIX} {pair}: 어댑터에 get_collateral 없음")
            return None

        collateral = await self._adapter.get_collateral()
        available = collateral.collateral - collateral.require_collateral
        if available <= 0:
            logger.debug(f"{_LOG_PREFIX} {pair}: box limit — 여유 증거금 없음, 스킵")
            return None

        position_size_pct = float(params.get("position_size_pct", 10.0))
        invest_jpy = available * position_size_pct / 100
        min_jpy = float(params.get("min_order_jpy", 500))
        if invest_jpy < min_jpy:
            logger.info(f"{_LOG_PREFIX} {pair}: box limit 투입 JPY({invest_jpy:.0f}) < {min_jpy:.0f}, 스킵")
            return None

        coin_size = round(invest_jpy / limit_price, 8)
        min_coin = float(params.get("min_coin_size", 0.001))
        if coin_size < min_coin:
            logger.debug(f"{_LOG_PREFIX} {pair}: box limit 수량 부족 ({coin_size} < {min_coin})")
            return None

        try:
            order_type = OrderType.SELL if side == "sell" else OrderType.BUY
            order = await self._adapter.place_order(
                order_type=order_type,
                pair=pair,
                amount=coin_size,
                price=limit_price,
            )
            _ws_at_order = self._latest_price.get(pair) or 0
            _limit_vs_ws = abs(limit_price - _ws_at_order) / _ws_at_order * 100 if _ws_at_order else 0.0
            _cushion_str = f"{cushion_to_midpoint_pct:.1f}%"
            logger.info(
                f"{_LOG_PREFIX} {pair}: box limit {side} 주문 — "
                f"지정가=¥{limit_price:,.0f} size={coin_size} order_id={order.order_id} "
                f"박스=¥{box_lower:,.0f}~¥{box_upper:,.0f}(mid=¥{midpoint:,.0f} cushion={_cushion_str}) "
                f"WS현재가=¥{_ws_at_order:,.0f}(괴리={_limit_vs_ws:.2f}%) "
                f"collateral=¥{invest_jpy:,.0f}"
            )
            return PendingLimitOrder(
                order_id=order.order_id,
                pair=pair,
                limit_price=float(limit_price),
                amount=coin_size,
                invest_jpy=invest_jpy,
                placed_at=_time.time(),
                signal_at_placement=signal,
                params=dict(params),
                atr=atr,
                signal_data=sd,
                box_key=(float(box_upper), float(box_lower)),
            )
        except Exception as e:
            logger.warning(f"{_LOG_PREFIX} {pair}: box limit 주문 실패 — {e}")
            return None

    async def _finalize_limit_entry(self, pair: str, order, pending) -> None:
        """박스 지정가 체결 후 포지션 등록.

        SL = 박스 경계 외부 (box_sl_cushion_pct):
          숏: box_upper * (1 + box_sl_cushion_pct/100)
          롱: box_lower * (1 - box_sl_cushion_pct/100)
        """
        from datetime import datetime, timezone
        from core.shared.exchange.types import Position

        try:
            exec_price = float(order.price or pending.limit_price)
            exec_amount = float(order.amount)
            if exec_amount == 0 and exec_price > 0:
                exec_amount = round(pending.invest_jpy / exec_price, 8)

            signal = pending.signal_at_placement
            side = "sell" if signal == "box_near_upper" else "buy"
            params = pending.params

            # ── 안전장치 4: 체결 후 가격 위치 재확인 ──────────────────────────
            # 지정가 대기 중 박스 변동으로 체결 시점에 midpoint가 이동했을 경우 방어
            if pending.box_key is not None:
                _bu, _bl = pending.box_key
                _midpoint_at_fill = (_bu + _bl) / 2.0
                if side == "sell" and exec_price <= _midpoint_at_fill:
                    logger.error(
                        f"{_LOG_PREFIX} {pair}: [안전장치] 숏 체결가 ¥{exec_price:.0f} ≤ midpoint ¥{_midpoint_at_fill:.0f} "
                        f"— 유효하지 않은 체결, 포지션 등록 취소 후 즉시 청산"
                    )
                    try:
                        await self._close_position_impl(pair, "invalid_fill_below_midpoint")
                    except Exception as _ce:
                        logger.error(f"{_LOG_PREFIX} {pair}: 잘못된 체결 후 청산 실패 — {_ce}")
                    return
                if side == "buy" and exec_price >= _midpoint_at_fill:
                    logger.error(
                        f"{_LOG_PREFIX} {pair}: [안전장치] 롱 체결가 ¥{exec_price:.0f} ≥ midpoint ¥{_midpoint_at_fill:.0f} "
                        f"— 유효하지 않은 체결, 포지션 등록 취소 후 즉시 청산"
                    )
                    try:
                        await self._close_position_impl(pair, "invalid_fill_above_midpoint")
                    except Exception as _ce:
                        logger.error(f"{_LOG_PREFIX} {pair}: 잘못된 체결 후 청산 실패 — {_ce}")
                    return

            # 박스 경계 기반 SL (box_key 있을 때), ATR fallback
            sl_cushion_pct = float(params.get("box_sl_cushion_pct", 0.5))
            if pending.box_key is not None:
                box_upper, box_lower = pending.box_key
                if side == "sell":
                    initial_sl = round(box_upper * (1.0 + sl_cushion_pct / 100.0), 0)
                else:
                    initial_sl = round(box_lower * (1.0 - sl_cushion_pct / 100.0), 0)
            else:
                atr = pending.atr
                atr_mult = float(params.get("atr_multiplier_stop", 2.0))
                if atr:
                    initial_sl = round(exec_price + atr * atr_mult if side == "sell" else exec_price - atr * atr_mult, 0)
                else:
                    initial_sl = None

            pos = Position(
                pair=pair,
                entry_price=exec_price,
                entry_amount=exec_amount,
                side=side,
                stop_loss_price=initial_sl,
                extra={
                    "opened_at": datetime.now(timezone.utc),
                    "pyramid_count": 0,
                    "pyramid_entries": [],
                    "total_size_pct": float(params.get("position_size_pct", 10.0)) / 100.0,
                },
            )
            self._position[pair] = pos

            pos.db_record_id = await self._record_open(
                product_code=pair,
                side=side,
                order_id=order.order_id,
                price=exec_price,
                size=exec_amount,
                collateral_jpy=pending.invest_jpy,
                stop_loss_price=initial_sl,
                strategy_id=params.get("strategy_id"),
            )

            if initial_sl is not None:
                try:
                    await self._sync_losscut_price(pair, initial_sl)
                except Exception as e:
                    logger.warning(f"{_LOG_PREFIX} {pair}: box limit 진입 시 ロスカット 설정 실패 — {e}")

            _sl_dist_pct = abs(exec_price - initial_sl) / exec_price * 100 if initial_sl else 0.0
            _box_ctx = ""
            if pending.box_key is not None:
                _bu2, _bl2 = pending.box_key
                _mid2 = (_bu2 + _bl2) / 2
                _pos_in_box = (exec_price - _bl2) / (_bu2 - _bl2) * 100 if _bu2 > _bl2 else 0
                _box_ctx = (
                    f" 박스=¥{_bl2:,.0f}~¥{_bu2:,.0f}(mid=¥{_mid2:,.0f}) "
                    f"진입위치={_pos_in_box:.1f}%"
                )
            logger.info(
                f"{_LOG_PREFIX} {pair}: box limit {side} 진입 확정 "
                f"order_id={order.order_id} price=¥{exec_price:,.0f} size={exec_amount} "
                f"SL=¥{initial_sl}(거리={_sl_dist_pct:.2f}%){_box_ctx}"
            )
        except Exception as e:
            logger.error(f"{_LOG_PREFIX} {pair}: box limit 진입 확정 오류 — {e}", exc_info=True)


# ── 시그널 매핑 테이블 ─────────────────────────────────────────
_LOCATION_TO_SIGNAL: dict[str, str] = {
    "near_lower": "box_near_lower",   # 박스 하단 근처 → 롱 검토
    "near_upper": "box_near_upper",   # 박스 상단 근처 → 숏 검토
    "outside":    "box_outside",      # 박스 이탈 상태 (청산 여부는 action이 결정)
    "middle":     "no_signal",        # 박스 중간 → 대기
}
