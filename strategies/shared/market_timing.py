from datetime import datetime, timezone, time as dtime


def is_market_closed_utc() -> bool:
    """Return True when XAUUSD spot market is closed.

    Closures:
      - Daily OANDA maintenance: 21:00–22:00 UTC (02:30–03:30 IST)
      - Weekend: Friday 21:00 UTC through Sunday 22:00 UTC
        (covers all of Saturday and Sunday in IST)
    """
    now = datetime.now(timezone.utc)
    weekday = now.weekday()
    t = now.time()

    if dtime(21, 0) <= t < dtime(22, 0):
        return True
    if weekday == 4 and t >= dtime(21, 0):
        return True
    if weekday == 5:
        return True
    if weekday == 6 and t < dtime(22, 0):
        return True
    return False
