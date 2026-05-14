from datetime import datetime, timedelta, timezone
from typing import Any


def split_candles(candle: dict[str, Any]) -> list[dict[str, str]]:
    """
    Reconstruct a 4-point synthetic tick path from one OHLC candle.

    Bullish (close > open): open → low  → high → close
    Bearish (close < open): open → high → low  → close
    Doji   (close == open): open → open → open → close

    Returns a list of 4 dicts: {"time": "<ISO8601+tz>", "price": "<str>"}
    """
    required = ("time", "open", "high", "low", "close")
    missing  = [k for k in required if k not in candle]
    if missing:
        raise ValueError(f"split_candles: candle missing keys {missing}  got: {list(candle)}")

    t = candle["time"]
    try:
        o, h, l, c = (
            str(candle["open"]),
            str(candle["high"]),
            str(candle["low"]),
            str(candle["close"]),
        )
        fo, fc = float(o), float(c)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"split_candles: cannot parse OHLC values: {exc}") from exc

    if fc > fo:
        sequence = [o, l, h, c]
    elif fc < fo:
        sequence = [o, h, l, c]
    else:
        sequence = [o, o, o, c]

    open_time = _parse_time(t)

    return [
        {"time": str(open_time + timedelta(seconds=i)), "price": price}
        for i, price in enumerate(sequence)
    ]


def _parse_time(t: Any) -> datetime:
    if isinstance(t, datetime):
        dt = t
    else:
        try:
            dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"split_candles: cannot parse time {t!r}: {exc}") from exc

    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
