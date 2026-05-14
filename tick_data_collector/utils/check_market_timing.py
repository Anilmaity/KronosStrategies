from datetime import datetime, timezone, time as dtime

# ── Market timing (UTC) ───────────────────────────────────────────────────────
def is_market_closed_utc():
    now = datetime.now(timezone.utc)
    weekday = now.weekday()  # Monday=0
    current_time = now.time()

    # Friday after 21:00 UTC
    if weekday == 4 and current_time >= dtime(21, 0):
        return True

    # Saturday full
    if weekday == 5:
        return True

    # Sunday before 21:00 UTC
    if weekday == 6 and current_time < dtime(21, 0):
        return True

    return False
