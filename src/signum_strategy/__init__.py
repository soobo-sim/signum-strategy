"""signum-strategy — Concrete trading strategy implementations for signum-engine."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("signum-strategy")
except PackageNotFoundError:
    __version__ = "0.0.0+dev"
