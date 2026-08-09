"""Universe sensitivity: does the null survive a broader, less liquid universe?
Anomalies are classically strongest in small/illiquid names, which the default
10cr turnover filter screens out entirely."""
from pathlib import Path
import numpy as np
from llmfin.anomalies import AnomalyConfig, REGISTRY, _monthly_panel, portfolio_sort, evaluate

DB = Path(r"~/.llmfin\market_historical.db")
base = AnomalyConfig()
panel = _monthly_panel(DB, base)
for a in REGISTRY:
    panel[a.name] = a.fn(panel)
print(f"panel: {len(panel)} symbol-months, {panel['symbol'].nunique()} symbols\n")

TIERS = [("wide   (1cr, >=20rs)", 1e7, 20.0, 100_000),
         ("mid    (5cr, >=50rs)", 5e7, 50.0, 250_000),
         ("liquid (10cr,>=100rs)", 10e7, 100.0, 500_000)]
names = [a.name for a in REGISTRY]
print(f"{'anomaly':<22}" + "".join(f"{t[0][:12]:>14}" for t in TIERS))
print(f"{'':<22}" + "".join(f"{'gross t / ann%':>14}" for _ in TIERS))
print("-" * (22 + 14 * len(TIERS)))
counts = {}
for nm in names:
    row = f"{nm:<22}"
    for label, turn, price, vol in TIERS:
        cfg = AnomalyConfig(min_turnover=turn, min_price=price, min_avg_volume=vol)
        res = portfolio_sort(panel, nm, cfg)
        st = evaluate(res, cfg)
        counts[label] = st.get("avg_names", 0)
        if not st.get("months"):
            row += f"{'-':>14}"
        else:
            row += f"{st['t_stat_gross_nw']:>6.2f}/{st['ann_net_pct']:>6.1f}"
    print(row)
print("\navg names per month by tier:", counts)
