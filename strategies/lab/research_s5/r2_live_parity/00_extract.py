"""00_extract.py — one read-only snapshot of the live record into results/*.parquet.

Run with the Kronos_Backend venv (psycopg2) after `source ../../Kronos_Backend/scripts/prod-db-env.sh`:
    cd strategies && ../../Kronos_Backend/.venv/bin/python lab/research_s5/r2_live_parity/00_extract.py

Everything downstream reads the parquet files, never the DB. PROTOCOL.md D1–D4.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
import psycopg2

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
OUT.mkdir(exist_ok=True)

WIN_LO = "2026-07-01 00:00:00+00"
WIN_HI = "2026-09-18 12:00:00+00"

# strategy name in apis_strategy -> lab module / short key
ROSTER = {
    "S93 FVG Scalp": "s93_fvg_scalp",
    "S94 Sweep Reversal": "s94_sweep_reversal",
    "S99 MSS FVG Reversal": "s99_mss_fvg",
    "S100 M3 Combo Scalper": "s100_m3_combo",
    "S95 Session Breakout": "s95_session_breakout",
    "Session Breakout M5 ORB": "orb",
    "Concept C03_FVG_FILL": "c03_fvg_fill",
    "Research OB_MIT_BIAS": "s14_ob_mit_bias",
    "Neymar Telegram Copy": "copy_free",
    "Neymar VIP": "copy_vip",
}


def connect():
    conn = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ["DB_PORT"],
                            dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"],
                            password=os.environ["DB_PASSWORD"], sslmode="require")
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()
    cur.execute("set time zone 'UTC'")
    return conn


def q(conn, sql, params=None) -> pd.DataFrame:
    return pd.read_sql(sql, conn, params=params)


def main() -> None:
    conn = connect()

    # ---- signals (the selection record) ----
    sig = q(conn, """
        select ss.id::text as signal_id, s.name as strategy_name, ss.symbol, ss.side,
               ss.entry_price::float as entry_price, ss.stop_loss::float as stop_loss,
               ss.take_profit::float as take_profit, ss.reason, ss.status, ss.rejection_reason,
               ss.signal_at, ss.position_id::text as position_id
        from apis_strategysignal ss join apis_strategy s on s.id = ss.strategy_id
        where ss.signal_at >= %s and ss.signal_at < %s
        order by ss.signal_at""", (WIN_LO, WIN_HI))
    sig["key"] = sig["strategy_name"].map(ROSTER)
    sig.to_parquet(OUT / "live_signals.parquet", index=False)
    print(f"signals: {len(sig)}  ({sig.signal_at.min()} .. {sig.signal_at.max()})")
    print(sig.groupby(["key", "status"]).size().to_string())

    # ---- positions + entry order + exit order + triggers ----
    pos = q(conn, """
        select p.id::text as position_id, s.name as strategy_name, p.symbol,
               p.quantity::float as quantity, p.realized_profit_loss::float as realized_units,
               p.avg_buy_price::float as avg_buy_price, p.avg_sell_price::float as avg_sell_price,
               p.created_at as pos_created_at,
               oe.id::text as entry_order_id, oe.side as entry_side, oe.price::float as entry_order_price,
               oe.quantity::float as lots, oe.broker_order_id, oe.reason as entry_reason,
               oe.created_at as entry_created_at,
               ox.condition as exit_condition, ox.reason as exit_reason, ox.price::float as exit_order_price,
               ox.created_at as exit_created_at, ox.broker_order_id as exit_broker_order_id
        from apis_position p
        join apis_userstrategy us on us.id = p.user_strategy_id
        join apis_strategy s on s.id = us.strategy_id
        left join apis_order oe on oe.position_id = p.id and oe.condition = 'ENTRY'
        left join lateral (
            select o.* from apis_order o
            where o.position_id = p.id and o.condition <> 'ENTRY'
            order by o.created_at limit 1) ox on true
        where p.created_at >= %s and p.created_at < %s
        order by p.created_at""", (WIN_LO, WIN_HI))
    pos["key"] = pos["strategy_name"].map(ROSTER)
    trg = q(conn, """
        select t.position_id::text as position_id, t.trigger_type, t.order_type, t.status,
               t.trigger_price::float as trigger_price, t.side, t.greater_than
        from apis_trigger t join apis_position p on p.id = t.position_id
        where p.created_at >= %s and p.created_at < %s""", (WIN_LO, WIN_HI))
    # nominal stop / target levels from the triggers (TIME_EXIT stores an epoch — skip it)
    sl = (trg[(trg.trigger_type == "STOPLOSS")].groupby("position_id").trigger_price.first()
          .rename("trig_sl"))
    tp = (trg[(trg.trigger_type == "TARGET")].groupby("position_id").trigger_price.first()
          .rename("trig_tp"))
    pos = pos.merge(sl, on="position_id", how="left").merge(tp, on="position_id", how="left")
    pos.to_parquet(OUT / "live_positions.parquet", index=False)
    trg.to_parquet(OUT / "live_triggers.parquet", index=False)
    print(f"positions: {len(pos)}")
    print(pos.groupby(["key"]).agg(n=("position_id", "size"), closed=("quantity", lambda x: int((x == 0).sum())),
                                   broker=("broker_order_id", lambda x: int((x.fillna("") != "").sum()))).to_string())

    # ---- broker deals (all, both accounts; downstream filters) ----
    deals = q(conn, """
        select account_id, deal_id, position_id, order_id, symbol, deal_type, entry_type,
               volume, price, profit, commission, swap, deal_time, raw
        from broker_deals order by deal_time""")
    deals["raw_reason"] = deals["raw"].map(lambda r: (r or {}).get("reason") if isinstance(r, dict) else None)
    deals["raw_comment"] = deals["raw"].map(lambda r: (r or {}).get("comment") if isinstance(r, dict) else None)
    deals["raw_sl"] = deals["raw"].map(lambda r: (r or {}).get("stopLoss") if isinstance(r, dict) else None)
    deals["raw_tp"] = deals["raw"].map(lambda r: (r or {}).get("takeProfit") if isinstance(r, dict) else None)
    deals["raw"] = deals["raw"].map(json.dumps)
    deals.to_parquet(OUT / "deals.parquet", index=False)
    print(f"deals: {len(deals)}  ({deals.deal_time.min()} .. {deals.deal_time.max()})")
    print(deals.groupby(["entry_type", "raw_reason"]).size().to_string())

    # ---- manager actions + config ----
    act = q(conn, """select created_at, action, reason from apis_manageraction
                     where action in ('KILL_SWITCH','INFO') order by created_at""")
    act.to_csv(OUT / "actions.csv", index=False)
    cfg = q(conn, "select master_mode, kill_switch_loss_usd, max_concurrent_positions, state, modified_at from apis_managerconfig")
    cfg["state"] = cfg["state"].map(json.dumps)
    cfg.to_csv(OUT / "managerconfig.csv", index=False)
    print(act.to_string())
    print(cfg.to_string())
    conn.close()


if __name__ == "__main__":
    main()
