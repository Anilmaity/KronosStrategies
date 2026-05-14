import logging

from .fetcher import OandaTickFetcher
from ._ticks import split_candles
from ._exceptions import (
    OandaTickError,
    OandaAuthError,
    OandaAPIError,
    CacheReadError,
)
from ._validator import (
    validate_range,
    DayReport,
    OANDA_HOLIDAYS,
    TRADING_DAY_CANDLES,
)

__all__ = [
    # core
    "OandaTickFetcher",
    "split_candles",
    # completeness
    "validate_range",
    "DayReport",
    "OANDA_HOLIDAYS",
    "TRADING_DAY_CANDLES",
    # exceptions
    "OandaTickError",
    "OandaAuthError",
    "OandaAPIError",
    "CacheReadError",
]

# Libraries must not configure logging — attach a NullHandler so callers
# that haven't configured logging don't see "No handlers could be found" warnings.
logging.getLogger(__name__).addHandler(logging.NullHandler())
