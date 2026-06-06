
"""MarginPositionSizer — 증거금 기반 포지션 사이즈 산정.

OOP_STRATEGY_REFACTOR.md §2.5 참조.

현재 3곳(GmoCoinBaseManager._open_position, _open_position_limit)에 흩어진
사이즈 계산 로직을 한 곳으로 통합.

계산 순서:
  1. invest_jpy = available_collateral × position_size_pct / 100
  2. min_order_jpy 체크
  3. 레버리지 상한(max_leverage) 체크
  4. min_coin_size 체크
  5. SizingDecision 반환
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.strategy.contracts.sizing import SizingDecision

if TYPE_CHECKING:
    from core.strategy.contracts.sizing import OrderType
    from core.strategy.contracts.types import Collateral

logger = logging.getLogger(__name__)


class MarginPositionSizer:
    """GMO Coin 증거금 기반 포지션 사이즈 산정 단일 구현."""

    def compute(
        self,
        *,
        collateral: "Collateral",
        price: float,
        params: dict,
        order_type: "OrderType",
    ) -> SizingDecision:
        """사이즈 산정 + 차단 사유 반환.

        Args:
            collateral: 현재 잔고 DTO.
            price: 주문 기준 가격.
            params: 전략 파라미터.
            order_type: ``"market"`` | ``"limit"``.

        Returns:
            ``SizingDecision(coin_size, invest_jpy, blocked, block_reason)``.
        """
        available = float(collateral.available_jpy)
        if available <= 0:
            return SizingDecision(
                coin_size=0.0,
                invest_jpy=0.0,
                blocked=True,
                block_reason="no_collateral",
            )

        position_size_pct = float(params.get("position_size_pct", 10.0))
        invest_jpy = available * position_size_pct / 100.0
        min_jpy = float(params.get("min_order_jpy", 500))

        if invest_jpy < min_jpy:
            return SizingDecision(
                coin_size=0.0,
                invest_jpy=invest_jpy,
                blocked=True,
                block_reason="below_min_jpy",
            )

        if price <= 0:
            return SizingDecision(
                coin_size=0.0,
                invest_jpy=invest_jpy,
                blocked=True,
                block_reason="invalid_price",
            )

        coin_size = round(invest_jpy / price, 8)

        # 레버리지 상한 체크
        leverage = int(collateral.leverage)
        total_collateral = float(collateral.available_jpy + collateral.used_margin_jpy)
        max_leverage = float(params.get("max_leverage", 1.5))
        if total_collateral > 0:
            effective_leverage = (coin_size * price) / total_collateral
            if effective_leverage > max_leverage:
                coin_size = round(total_collateral * max_leverage / price, 8)
                logger.debug(
                    f"MarginPositionSizer: 레버리지 제한 ({effective_leverage:.2f}×) → "
                    f"coin_size={coin_size:.8f}"
                )

        min_coin = float(params.get("min_coin_size", 0.001))
        if coin_size < min_coin:
            return SizingDecision(
                coin_size=coin_size,
                invest_jpy=invest_jpy,
                blocked=True,
                block_reason="below_min_coin",
            )

        return SizingDecision(
            coin_size=coin_size,
            invest_jpy=invest_jpy,
            blocked=False,
            block_reason=None,
        )
