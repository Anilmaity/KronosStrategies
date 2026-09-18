"""00b_extract_actions.py — all ManagerAction rows with the managed strategy's name, so live
'active' windows per strategy can be reconstructed (PROTOCOL deviation 1, 2026-09-18)."""
import os, sys
from pathlib import Path
import pandas as pd, psycopg2
OUT = Path(__file__).resolve().parent / "results"
conn = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ["DB_PORT"], dbname=os.environ["DB_NAME"],
                        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"], sslmode="require")
conn.set_session(readonly=True, autocommit=True)
a = pd.read_sql("""select ma.created_at, ma.action, ma.reason, s.name as strategy_name
                   from apis_manageraction ma
                   left join apis_managedstrategy ms on ms.id = ma.managed_strategy_id
                   left join apis_userstrategy us on us.id = ms.user_strategy_id
                   left join apis_strategy s on s.id = us.strategy_id
                   order by ma.created_at""", conn)
a.to_csv(OUT / "manager_actions_all.csv", index=False)
print(len(a)); print(a.groupby(["strategy_name", "action"]).size().to_string())
# also: the detail strings of metaapi rejections and entry_drift (for 04_gates)
r = pd.read_sql("""select s.name as strategy_name, ss.rejection_reason, ss.signal_at
                   from apis_strategysignal ss join apis_strategy s on s.id=ss.strategy_id
                   where ss.status='REJECTED' and ss.signal_at >= '2026-07-01' and ss.rejection_reason like 'metaapi%%'""", conn)
print(r.rejection_reason.str[:90].value_counts().head(15).to_string())
conn.close()
