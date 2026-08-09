"""Decay analysis, implementing artifacts/prereg_decay.md exactly.

H1 monotonic decay | H2 structural break | H3 unusual live window | H4 none.
2024 excluded from all trend/break tests (partial year). Every test run twice,
with and without 2020, and disagreement => inconclusive.
"""
import json
import sys
from pathlib import Path

import numpy as np

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "regime_2010_2024.json")
LIVE_ALPHA = 0.58          # net, measured earlier on the 2025-05..2026-08 window
NAMED_EVENTS = {2018: "SEBI derivatives/physical settlement",
                2021: "retail surge + peak-margin final phase",
                2022: "T+1 settlement phased",
                2023: "T+1 settlement complete"}

data = json.loads(SRC.read_text())
years = [(y["year"], y["fade_avg_alpha_pct"], y.get("fade_trades"))
         for y in data["years"] if y.get("fade_avg_alpha_pct") is not None]
years.sort()
print("per-year net fade alpha")
for y, a, n in years:
    print(f"  {y}: {a:>6.2f}%   ({n} trades)" + ("   <-- PARTIAL, excluded from tests" if y == 2024 else ""))


def spearman(x, y):
    rx, ry = np.argsort(np.argsort(x)) + 1.0, np.argsort(np.argsort(y)) + 1.0
    r = float(np.corrcoef(rx, ry)[0, 1])
    n = len(x)
    if n < 4 or abs(r) >= 1.0:
        return r, float("nan")
    t = r * np.sqrt((n - 2) / (1 - r * r))
    # two-sided p via normal approximation on t with df=n-2
    z = abs(t)
    p = 2 * (1 - 0.5 * (1 + np.math.erf(z / np.sqrt(2)))) if hasattr(np, "math") else None
    from math import erf, sqrt
    p = 2 * (1 - 0.5 * (1 + erf(z / sqrt(2))))
    return r, float(p)


def analyse(label, pairs):
    yr = np.array([p[0] for p in pairs], dtype=float)
    al = np.array([p[1] for p in pairs], dtype=float)
    n = len(yr)
    print(f"\n{'='*66}\n{label}  (n={n}: {int(yr.min())}-{int(yr.max())})\n{'='*66}")

    # ---- H1: monotonic decay
    r, p = spearman(yr, al)
    slope = float(np.polyfit(yr, al, 1)[0])
    loo = [float(np.polyfit(np.delete(yr, i), np.delete(al, i), 1)[0]) for i in range(n)]
    loo_ok = all(s < 0 for s in loo)
    h1 = (r < 0 and p < 0.05 and loo_ok)
    print(f"H1 monotonic decay")
    print(f"   spearman r = {r:+.3f}, p = {p:.4f}")
    print(f"   OLS slope  = {slope:+.4f} %/year")
    print(f"   leave-one-out slopes all negative: {loo_ok}  "
          f"(range {min(loo):+.4f} .. {max(loo):+.4f})")
    print(f"   => H1 {'FIRES' if h1 else 'fails'}")

    # ---- H2: structural break (max mean-difference, permutation null)
    def max_split(a):
        best, bi = 0.0, None
        for k in range(2, len(a) - 1):
            d = abs(a[:k].mean() - a[k:].mean())
            if d > best:
                best, bi = d, k
        return best, bi
    obs, bi = max_split(al)
    rng = np.random.default_rng(0)
    null = np.array([max_split(rng.permutation(al))[0] for _ in range(10000)])
    p_break = float((null >= obs).mean())
    split_year = int(yr[bi]) if bi is not None else None
    near = [e for e in NAMED_EVENTS if split_year and abs(e - split_year) <= 1]
    h2 = (p_break < 0.05 and bool(near))
    print(f"\nH2 structural break")
    print(f"   best split before {split_year}: |Δmean| = {obs:.3f}pp")
    print(f"   permutation p = {p_break:.4f}  (10,000 shuffles)")
    print(f"   within ±1y of a named event: {near if near else 'NO'}")
    if p_break < 0.05 and not near:
        print(f"   => significant but UNEXPLAINED break (not retrofitted)")
    print(f"   => H2 {'FIRES' if h2 else 'fails'}")

    # ---- H3: live window is an outlier, no trend
    pct10 = float(np.percentile(al, 10))
    h3 = (not h1 and not h2 and LIVE_ALPHA < pct10)
    print(f"\nH3 unusual live window")
    print(f"   live alpha {LIVE_ALPHA:.2f}% vs 10th pct of {int(yr.min())}-{int(yr.max())} = {pct10:.2f}%")
    print(f"   => H3 {'FIRES' if h3 else 'fails'}")

    verdict = "H1 monotonic decay" if h1 else "H2 structural break" if h2 else \
              "H3 unusual live window" if h3 else "H4 none - decay unexplained"
    print(f"\n   VERDICT: {verdict}")
    return {"n": n, "spearman_r": round(r, 3), "spearman_p": round(p, 4),
            "ols_slope": round(slope, 4), "loo_all_negative": loo_ok,
            "break_split_year": split_year, "break_delta": round(obs, 3),
            "break_p": p_break, "break_near_event": near,
            "h1": h1, "h2": h2, "h3": h3, "verdict": verdict}


MAXYEAR = int(sys.argv[2]) if len(sys.argv) > 2 else 2023
tests = [(y, a) for y, a, _ in years if y <= MAXYEAR]
res_all = analyse("WITH 2020 (primary)", tests)
res_no20 = analyse("WITHOUT 2020 (COVID sensitivity)", [(y, a) for y, a in tests if y != 2020])

print(f"\n{'='*66}")
if res_all["verdict"] != res_no20["verdict"]:
    final = "INCONCLUSIVE - the two COVID treatments disagree (pre-registered rule)"
else:
    final = res_all["verdict"]
print(f"FINAL: {final}")

# sign test extension
alphas = [a for y, a in tests]
k, n = sum(1 for a in alphas if a > 0), len(alphas)
from math import comb
p_sign = sum(comb(n, i) for i in range(k, n + 1)) / 2 ** n
print(f"\nsign test: {k}/{n} years positive, one-sided p = {p_sign:.6f}")
print(f"           (was 11/11, p = 0.000488 over 2010-2020)")

Path("decay_result.json").write_text(json.dumps(
    {"per_year": [{"year": y, "alpha": a, "trades": t} for y, a, t in years],
     "with_2020": res_all, "without_2020": res_no20, "final_verdict": final,
     "sign_test": {"k": k, "n": n, "p": p_sign}}, indent=2))
print("\nwrote decay_result.json")
