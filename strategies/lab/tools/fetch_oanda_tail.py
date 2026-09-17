"""Read-only OANDA candle fetch for the missing tail. Runs inside the strategies container
(uses OANDA_API_KEY / OANDA_PRACTICE from its env; never prints them). Writes CSV per
granularity to /tmp/xau_tail/. Mid OHLC (what the cache holds) + bid/ask close for spread."""
import csv, os, sys, time
import requests
FROM, TO = sys.argv[1], sys.argv[2]
key = os.environ["OANDA_API_KEY"]
practice = os.getenv("OANDA_PRACTICE", "true").strip().lower() not in ("false", "0", "no")
base = "https://api-fxpractice.oanda.com/v3" if practice else "https://api-fxtrade.oanda.com/v3"
s = requests.Session(); s.headers.update({"Authorization": f"Bearer {key}", "Accept-Datetime-Format": "RFC3339"})
OUT=os.environ.get("TAIL_OUT","/tmp/xau_tail"); os.makedirs(OUT, exist_ok=True)
for gran in os.environ.get("GRANS", "M1,M5,M15,H1,H4,D").split(","):
    rows, frm, pages = [], FROM, 0
    while True:
        r = s.get(f"{base}/instruments/XAU_USD/candles",
                  params=({"granularity": gran, "price": "MBA", "from": frm, "count": 4500, "dailyAlignment": 0, "alignmentTimezone": "UTC"} | ({"to": TO} if TO != "now" else {})), timeout=30)
        if r.status_code != 200:
            print(gran, "HTTP", r.status_code, r.text[:120]); break
        c = r.json().get("candles", []); pages += 1
        for k in c:
            if not k.get("complete", False):
                continue
            rows.append((k["time"], k["mid"]["o"], k["mid"]["h"], k["mid"]["l"], k["mid"]["c"], k["volume"],
                         k["bid"]["c"], k["ask"]["c"]))
        if len(c) < 2:
            break
        last = c[-1]["time"]
        if last <= frm: break
        frm = last
        if len(c) < 4500: break
        time.sleep(0.2)
    # dedupe on time keeping last
    dd = {}
    for row in rows: dd[row[0]] = row
    rows = [dd[k] for k in sorted(dd)]
    with open(f"{OUT}/{gran}.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["time", "open", "high", "low", "close", "volume", "bid_c", "ask_c"]); w.writerows(rows)
    print(f"{gran}: {len(rows)} complete candles in {pages} pages, {rows[0][0] if rows else '-'} -> {rows[-1][0] if rows else '-'}")
