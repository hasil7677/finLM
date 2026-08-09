"""Build one continuous analysis DB (2010 -> present) from the two ingest paths,
then validate the July-2024 format boundary.

Two ingest paths meeting mid-series is the classic place for a discontinuity
that LOOKS like a structural break but is a schema artifact. Since the decay
finding rests on a trend across exactly that boundary, this has to be checked
before the merged series is used for anything.
"""
import shutil
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

HIST = Path(r"~/.llmfin\market_historical.db")   # old format, 2010 -> 2024-07-05
LIVE = Path(r"~/.llmfin\market.db")              # UDiFF, 2024-07-08 -> present
FULL = Path(r"~/.llmfin\market_full.db")
BOUNDARY = "2024-07-05"

for p, label in ((HIST, "historical"), (LIVE, "live")):
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    print(f"{label:11} {c.execute('SELECT MIN(date),MAX(date),COUNT(*) FROM daily_prices').fetchone()}")
    c.close()

print(f"\ncopying {HIST.name} -> {FULL.name} ...")
shutil.copyfile(HIST, FULL)
conn = sqlite3.connect(FULL)
conn.execute(f"ATTACH DATABASE '{LIVE}' AS live")
before = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
conn.execute("""
    INSERT OR REPLACE INTO daily_prices
    (symbol, date, series, open, high, low, close, prev_close, volume, turnover)
    SELECT symbol, date, series, open, high, low, close, prev_close, volume, turnover
    FROM live.daily_prices
""")
conn.commit()
after = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
conn.execute("DETACH DATABASE live")
rng = conn.execute("SELECT MIN(date), MAX(date), COUNT(DISTINCT date) FROM daily_prices").fetchone()
print(f"merged: {before} + {after-before} new = {after} rows")
print(f"range : {rng[0]} .. {rng[1]}  ({rng[2]} trading days)")

# ---- gap check
dates = pd.read_sql_query("SELECT DISTINCT date FROM daily_prices ORDER BY date", conn)["date"]
d = pd.to_datetime(dates)
gaps = d.diff().dt.days
big = [(str(d.iloc[i-1].date()), str(d.iloc[i].date()), int(gaps.iloc[i]))
       for i in range(1, len(d)) if gaps.iloc[i] > 10]
print(f"\ngaps > 10 calendar days: {len(big)}")
for a, b, n in big[:10]:
    print(f"   {a} -> {b}  ({n} days)")

# ---- boundary validation
print(f"\n{'='*64}\nFORMAT BOUNDARY VALIDATION around {BOUNDARY}\n{'='*64}")
q = """SELECT date, symbol, close, prev_close, volume, turnover, series
       FROM daily_prices WHERE date BETWEEN ? AND ?"""
pre = pd.read_sql_query(q, conn, params=("2024-01-01", BOUNDARY))
post = pd.read_sql_query(q, conn, params=("2024-07-06", "2024-12-31"))
print(f"pre  ({pre['date'].min()}..{pre['date'].max()}): {len(pre)} rows, "
      f"{pre['symbol'].nunique()} symbols, {pre['date'].nunique()} days")
print(f"post ({post['date'].min()}..{post['date'].max()}): {len(post)} rows, "
      f"{post['symbol'].nunique()} symbols, {post['date'].nunique()} days")

print(f"\n{'metric':<22}{'pre':>14}{'post':>14}{'ratio':>9}")
print("-" * 59)
for name, col in (("rows/day", None), ("median close", "close"),
                  ("median volume", "volume"), ("median turnover", "turnover")):
    if col is None:
        a, b = len(pre) / pre["date"].nunique(), len(post) / post["date"].nunique()
    else:
        a, b = pre[col].median(), post[col].median()
    print(f"{name:<22}{a:>14,.1f}{b:>14,.1f}{b/a:>9.2f}")

print(f"\nseries mix pre : {pre['series'].value_counts().to_dict()}")
print(f"series mix post: {post['series'].value_counts().to_dict()}")

# symbol continuity across the seam
last_pre = set(pre[pre["date"] == pre["date"].max()]["symbol"])
first_post = set(post[post["date"] == post["date"].min()]["symbol"])
print(f"\nsymbols on last pre-boundary day : {len(last_pre)}")
print(f"symbols on first post-boundary day: {len(first_post)}")
print(f"overlap: {len(last_pre & first_post)} "
      f"({len(last_pre & first_post)/max(len(last_pre),1)*100:.1f}% of pre)")
print(f"dropped at seam: {len(last_pre - first_post)}, new at seam: {len(first_post - last_pre)}")

# daily returns must not spike at the seam (would signal a price-scale change)
both = pd.concat([pre, post])
both = both[both["prev_close"] > 0]
both["ret"] = both["close"] / both["prev_close"] - 1
daily = both.groupby("date")["ret"].mean()
seam = daily.loc["2024-06-20":"2024-07-20"]
print(f"\nequal-weight daily return around the seam (should look ordinary):")
for dt, v in seam.items():
    mark = "   <-- first post-boundary day" if dt >= "2024-07-06" and dt <= "2024-07-10" else ""
    print(f"   {dt}: {v*100:+.2f}%{mark}")
print(f"\nstd of daily mean return  pre: {daily.loc[:BOUNDARY].std()*100:.3f}%  "
      f"post: {daily.loc['2024-07-06':].std()*100:.3f}%")
conn.close()
print(f"\nmerged DB written: {FULL}")
