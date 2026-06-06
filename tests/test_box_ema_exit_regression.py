"""
Regression test: GmoCoinBoxManager._is_ema_exit_triggered() must always
return False (BUG #183 regression guard).

Box entry at the lower/upper bound structurally places price on the wrong
side of EMA.  Applying the trend-following EMA-exit rule would immediately
close a freshly opened box position, creating an infinite open→close loop.
The box strategy delegates all exit decisions to TP/SL/box-invalidation logic
instead, so EMA exit must remain permanently disabled.

This module stubs out signum-engine (``core.*``) so the test can run in
isolation without a full dependency installation.
"""
from __future__ import annotations

import sys
import types
import unittest.mock


# ---------------------------------------------------------------------------
# Minimal signum-engine stubs
# ---------------------------------------------------------------------------
# We register lightweight fake modules so that importing the real manager
# files does not raise ModuleNotFoundError.  Only the symbols that the
# manager files actually reference at import time are needed.
# ---------------------------------------------------------------------------

def _install_core_stubs() -> None:
    """Populate ``sys.modules`` with stub packages for signum-engine."""

    def _make_module(name: str, **attrs) -> types.ModuleType:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod
        return mod

    # Minimal base class that managers inherit from
    class _GmoCoinBaseManager:
        _task_prefix: str = ""
        _log_prefix: str = ""
        _supports_short: bool = True

        def __init__(self, *args, **kwargs):  # noqa: ANN002
            pass

        def _is_ema_exit_triggered(
            self,
            pair: str,
            price: float,
            pos: object,
            ema: float,
            atr: object,
        ) -> bool:
            """Parent (trend-following) implementation: price-vs-EMA check.

            This stub returns ``True`` by default so that tests can confirm
            box manager's override (which must return ``False``) overrides
            this behaviour.
            """
            return True  # parent performs the real check; stub always True

    # Build the module hierarchy expected by the manager imports
    _make_module("core")
    _make_module("core.strategy")
    _make_module("core.strategy.managers",
                 GmoCoinBaseManager=_GmoCoinBaseManager)
    _make_module("core.strategy.managers.gmo_coin_base",
                 GmoCoinBaseManager=_GmoCoinBaseManager)
    _make_module("core.judge")
    _make_module("core.judge.analysis")
    _make_module("core.judge.analysis.box_detector",
                 detect_box=unittest.mock.MagicMock(),
                 BoxDetectResult=unittest.mock.MagicMock())
    _make_module("core.shared")
    _make_module("core.shared.box_signals",
                 classify_price_in_box=unittest.mock.MagicMock(),
                 check_box_invalidation=unittest.mock.MagicMock(),
                 compute_triangle_apex_candles=unittest.mock.MagicMock())
    _make_module("core.shared.exchange")
    _make_module("core.shared.exchange.types",
                 Position=unittest.mock.MagicMock())
    _make_module("core.shared.logging")
    _make_module("core.shared.logging.context",
                 get_judge_cycle_id=unittest.mock.MagicMock(return_value=None))


# Install stubs once, before any signum_strategy import
if "core" not in sys.modules:
    _install_core_stubs()

# ---------------------------------------------------------------------------
# Import the real managers (after stubs are in place)
# ---------------------------------------------------------------------------
# Add the src directory to the path so pytest can resolve signum_strategy
import pathlib
import importlib

_SRC = pathlib.Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from signum_strategy.gmo_coin.box.manager import GmoCoinBoxManager      # noqa: E402
from signum_strategy.gmo_coin.trend.manager import GmoCoinTrendManager  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _box_manager() -> GmoCoinBoxManager:
    """Return an uninitialized GmoCoinBoxManager instance (no __init__ call)."""
    return object.__new__(GmoCoinBoxManager)


def _trend_manager() -> GmoCoinTrendManager:
    """Return an uninitialized GmoCoinTrendManager instance (no __init__ call)."""
    return object.__new__(GmoCoinTrendManager)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBoxEmaExitDisabled:
    """GmoCoinBoxManager must never trigger EMA-based exit (BUG #183)."""

    def test_ema_exit_always_false_long_scenario(self):
        """
        After a box_near_lower long entry, price is structurally below EMA.
        _is_ema_exit_triggered() must return False so the position is not
        closed immediately.
        """
        mgr = _box_manager()
        # Simulate: price = 4 900 000, EMA = 5 000 000 (price below EMA → trend
        # logic would exit, box logic must not)
        result = mgr._is_ema_exit_triggered(
            pair="BTC_JPY",
            price=4_900_000.0,
            pos=unittest.mock.MagicMock(),
            ema=5_000_000.0,
            atr=50_000.0,
        )
        assert result is False, (
            "Box manager must keep EMA exit disabled (price below EMA after "
            "box_near_lower entry) — BUG #183 regression"
        )

    def test_ema_exit_always_false_short_scenario(self):
        """
        After a box_near_upper short entry, price is structurally above EMA.
        _is_ema_exit_triggered() must return False so the position is not
        closed immediately.
        """
        mgr = _box_manager()
        # Simulate: price = 5 100 000, EMA = 5 000 000 (price above EMA → trend
        # short logic would exit, box logic must not)
        result = mgr._is_ema_exit_triggered(
            pair="BTC_JPY",
            price=5_100_000.0,
            pos=unittest.mock.MagicMock(),
            ema=5_000_000.0,
            atr=50_000.0,
        )
        assert result is False, (
            "Box manager must keep EMA exit disabled (price above EMA after "
            "box_near_upper short entry) — BUG #183 regression"
        )

    def test_ema_exit_always_false_regardless_of_inputs(self):
        """
        The return value must be False for any combination of price / EMA / ATR.
        Exhaustive spot-check across varied inputs.
        """
        mgr = _box_manager()
        cases = [
            # (price, ema, atr)
            (1.0, 1.0, 0.0),
            (0.0, 0.0, 0.0),
            (1e9, 1.0, 1e6),
            (1.0, 1e9, 1e6),
            (float("inf"), 1.0, 1.0),
        ]
        for price, ema, atr in cases:
            result = mgr._is_ema_exit_triggered(
                pair="BTC_JPY",
                price=price,
                pos=None,
                ema=ema,
                atr=atr,
            )
            assert result is False, (
                f"Expected False for price={price}, ema={ema}, atr={atr}; "
                f"got {result!r}"
            )


class TestTrendEmaExitNotDisabled:
    """GmoCoinTrendManager must NOT permanently disable EMA exit.

    The trend manager relies on the parent class (GmoCoinBaseManager) for
    EMA exit logic — it must NOT override _is_ema_exit_triggered() to always
    return False.  This test acts as a symmetry guard: if someone accidentally
    copies the box override into the trend manager the test will catch it.
    """

    def test_trend_manager_does_not_override_ema_exit_to_false(self):
        """
        GmoCoinTrendManager does not define its own _is_ema_exit_triggered(),
        so it inherits the parent implementation.  Verify the method is *not*
        defined in GmoCoinTrendManager.__dict__ (i.e., not overridden).
        """
        assert "_is_ema_exit_triggered" not in GmoCoinTrendManager.__dict__, (
            "GmoCoinTrendManager must NOT override _is_ema_exit_triggered() "
            "— trend following needs the real EMA exit check from the parent"
        )

    def test_trend_manager_ema_exit_uses_parent_logic(self):
        """
        The trend manager's _is_ema_exit_triggered() is inherited from the
        stub base class which returns True (represents the real parent check).
        This ensures the box override and trend inheritance diverge as intended.
        """
        trend_mgr = _trend_manager()
        box_mgr = _box_manager()

        # Using the same inputs, both managers should produce different results:
        # box always False; trend uses parent logic (stub returns True)
        kwargs = dict(
            pair="BTC_JPY",
            price=4_900_000.0,
            pos=None,
            ema=5_000_000.0,
            atr=50_000.0,
        )
        box_result = box_mgr._is_ema_exit_triggered(**kwargs)
        trend_result = trend_mgr._is_ema_exit_triggered(**kwargs)

        assert box_result is False, "Box manager must always return False"
        assert trend_result is True, (
            "Trend manager must use parent EMA exit logic (not hardcoded False)"
        )
