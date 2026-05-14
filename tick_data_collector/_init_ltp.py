import os, psycopg2

conn = psycopg2.connect(os.environ["TIGERDATA_URL"])
conn.autocommit = True
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS ltp (time TIMESTAMPTZ NOT NULL, ltp DOUBLE PRECISION NOT NULL, symbol TEXT NOT NULL);")
cur.execute("SELECT create_hypertable('ltp', 'time', if_not_exists => TRUE);")
cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ltp_time_symbol_idx ON ltp (time, symbol);")
print("OK: ltp hypertable ready")
