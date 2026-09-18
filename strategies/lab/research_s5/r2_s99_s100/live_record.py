"""live_record.py -- the live selection + P&L record of S99 and S100 since 2026-07-01 (prod DB,
READ-ONLY: sslmode=require, default_transaction_read_only=on, readonly session).

    cd Kronos_Backend && source scripts/prod-db-env.sh && \
      PGSSLMODE=require .venv/bin/python ../KronosStrategies/strategies/lab/research_s5/r2_s99_s100/live_record.py

Writes results/live_*.csv and prints the tables. Units: Position.realized_profit_loss is points x
lots; USD = x100 (70 Code Map/Units and Conventions).
On this DB apis_position.created_at is timestamptz and equals the signal's signal_at to the second
(checked 2026-09-18), so hours are read from it directly. Exit time = the closing Order's created_at.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import psycopg2

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(exist_ok=True)
SINCE = "2026-07-01"
NAMES = ("S99 MSS FVG Reversal", "S100 M3 Combo Scalper")


def conn():
    c = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ["DB_PORT"], dbname=os.environ["DB_NAME"],
                         user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"], sslmode="require",
                         options="-c default_transaction_read_only=on")
    c.set_session(readonly=True, autocommit=True)
    return c


def pf(v):
    gw, gl = v[v > 0].sum(), -v[v <= 0].sum()
    return float(gw / gl) if gl > 0 else float("inf")


def main() -> None:
    c = conn()
    sig = pd.read_sql("""
        select st.name as strategy, s.signal_at, s.created_at, s.side, s.entry_price, s.stop_loss, s.take_profit,
               s.reason, s.status, s.rejection_reason, s.position_id
        from apis_strategysignal s join apis_strategy st on st.id = s.strategy_id
        where st.name = any(%(names)s) and s.created_at >= %(since)s
        order by s.created_at""", c, params=dict(names=list(NAMES), since=SINCE))
    pos = pd.read_sql("""
        select st.name as strategy, p.id as position_id, p.created_at, p.symbol, p.quantity, p.total_buy_quantity,
               p.avg_buy_price, p.avg_sell_price, p.realized_profit_loss, us.user_broker_id,
               (select min(o.created_at) from apis_order o where o.position_id = p.id and o.condition <> 'ENTRY') as exit_at,
               (select string_agg(distinct o.condition, ',') from apis_order o where o.position_id = p.id and o.condition <> 'ENTRY') as exit_kind,
               (select max(o.quantity) from apis_order o where o.position_id = p.id and o.condition = 'ENTRY') as entry_lots,
               (select max(o.side) from apis_order o where o.position_id = p.id and o.condition = 'ENTRY') as entry_side
        from apis_position p join apis_userstrategy us on us.id = p.user_strategy_id
        join apis_strategy st on st.id = us.strategy_id
        where st.name = any(%(names)s) and p.created_at >= %(since)s
        order by p.created_at""", c, params=dict(names=list(NAMES), since=SINCE))
    sig.to_csv(OUT / "live_signals.csv", index=False)
    pos.to_csv(OUT / "live_positions.csv", index=False)

    lines = []
    def p(*a):
        s = " ".join(str(x) for x in a); print(s); lines.append(s)

    p(f"# live record since {SINCE} (pulled {pd.Timestamp.now('UTC'):%Y-%m-%d %H:%M} UTC)")
    for name in NAMES:
        s = sig[sig.strategy == name]
        p(f"\n## {name} -- signals: {len(s)}  PLACED {int((s.status=='PLACED').sum())}  REJECTED {int((s.status=='REJECTED').sum())}"
          f"  other {int((~s.status.isin(['PLACED','REJECTED'])).sum())}")
        rej = s[s.status == "REJECTED"].rejection_reason.fillna("").str.split(":").str[0].str.strip()
        p(rej.value_counts().to_string())
        # sl_too_tight detail: the stop distances that were rejected
        tight = s[(s.status == "REJECTED") & s.rejection_reason.fillna("").str.startswith("sl_too_tight")]
        if len(tight):
            d = (tight.entry_price - tight.stop_loss).abs()
            p(f"sl_too_tight stop distances: n {len(d)} median {d.median():.2f} p90 {d.quantile(.9):.2f}")
        placed = s[s.status == "PLACED"]
        d_all = (s.entry_price - s.stop_loss).abs()
        p(f"generated stop distance: median {d_all.median():.2f}  share < 1.5: {100*(d_all<1.5).mean():.1f}%  share < 3.0: {100*(d_all<3.0).mean():.1f}%")
        p("signals by month: " + s.groupby(pd.to_datetime(s.created_at).dt.strftime("%Y-%m")).status.value_counts().unstack(fill_value=0).to_string().replace("\n", " | "))

        q = pos[(pos.strategy == name) & (pos.symbol == "XAU_USD")].copy()
        q = q[q.exit_at.notna()]
        q["usd"] = q.realized_profit_loss.astype(float) * 100.0
        q["pts"] = q.realized_profit_loss.astype(float) / q.entry_lots.replace(0, pd.NA).astype(float)
        q["month"] = pd.to_datetime(q.created_at).dt.strftime("%Y-%m")
        p(f"\nclosed positions: n {len(q)}  USD {q.usd.sum():+.0f}  PF(usd) {pf(q.usd):.3f}  WR {100*(q.usd>0).mean():.1f}%"
          f"  mean lots {q.entry_lots.astype(float).mean():.3f}  pts-per-0.01lot-equivalent PF {pf(q.pts.dropna()):.3f}  pts sum {q.pts.sum():+.1f}")
        p("by month (USD, n, PF):")
        g = q.groupby("month").agg(n=("usd", "size"), usd=("usd", "sum"), pfv=("usd", pf), wr=("usd", lambda v: 100*(v>0).mean()))
        p(g.round(2).to_string())
        p("by exit kind: " + q.groupby("exit_kind").usd.agg(["size", "sum"]).round(0).to_string().replace("\n", " | "))
        # hour of entry (UTC)
        q["hour_utc"] = pd.to_datetime(q.created_at, utc=True).dt.hour   # column is timestamptz; matches signal_at (checked)
        h = q.groupby("hour_utc").usd.agg(["size", "sum"]).round(0)
        p("by entry hour UTC (n, USD): " + h.to_string().replace("\n", " | "))
        # stop-distance buckets on the placed signals that link to a position
        pl = placed.merge(q[["position_id", "usd"]], on="position_id", how="inner")
        if len(pl):
            pl["stop"] = (pl.entry_price - pl.stop_loss).abs()
            pl["bucket"] = pd.cut(pl.stop, [0, 1.5, 2, 3, 4, 6, 100], labels=["<1.5", "1.5-2", "2-3", "3-4", "4-6", "6+"])
            b = pl.groupby("bucket", observed=True).usd.agg(["size", "sum", pf]).round(2)
            p("placed & closed by stop bucket (n, USD, PF): " + b.to_string().replace("\n", " | "))
        q.to_csv(OUT / f"live_closed_{name.split()[0].lower()}.csv", index=False)
    (OUT / "live_record.txt").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
