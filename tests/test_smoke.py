"""Smoke tests for signum-strategy package structure."""


def test_import_trend_manager():
    """GmoCoinTrendManager can be imported from signum_strategy."""
    from signum_strategy.gmo_coin.trend.manager import GmoCoinTrendManager  # noqa: F401


def test_import_box_manager():
    """GmoCoinBoxManager can be imported from signum_strategy."""
    from signum_strategy.gmo_coin.box.manager import GmoCoinBoxManager  # noqa: F401


def test_version():
    """Package version is accessible."""
    import signum_strategy
    assert isinstance(signum_strategy.__version__, str)
