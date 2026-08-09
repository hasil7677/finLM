"""PRE-REGISTRATION - written and run BEFORE live-window breadth is computed.

Fits fade alpha ~ breadth on the 11 historical years and emits:
  1. the prediction function,
  2. the 95% PREDICTION interval for a NEW observation (not the CI on the mean),
  3. a power analysis: for which breadth values would the observed live alpha of
     +0.58% fall OUTSIDE the interval, i.e. what result could have falsified this.

Nothing here reads the 2025-26 DB.
"""
import json
from pathlib import Path

import numpy as np

from llmfin.provenance import write_artifact

OBSERVED_LIVE_ALPHA = 0.58   # net @0.4% cost, measured earlier, already fixed
SRC = Path(__file__).with_name("regime_full.json")

years = json.loads(SRC.read_text())["years"]
x = np.array([y["breadth_pct"] for y in years], dtype=float)
y = np.array([y["fade_avg_alpha_pct"] for y in years], dtype=float)
n = len(x)

b, a = np.polyfit(x, y, 1)          # slope, intercept
fit = a + b * x
sse = float(((y - fit) ** 2).sum())
s = (sse / (n - 2)) ** 0.5          # residual standard error
xbar = float(x.mean())
sxx = float(((x - xbar) ** 2).sum())
r = float(np.corrcoef(x, y)[0, 1])
t_crit = 2.2622                     # t(0.975, df=9)


def predict(xs: float) -> tuple[float, float, float, float]:
    """Point prediction and 95% prediction interval for a NEW observation."""
    yhat = a + b * xs
    half = t_crit * s * (1 + 1 / n + (xs - xbar) ** 2 / sxx) ** 0.5
    return yhat, yhat - half, yhat + half, 2 * half


print(f"n={n}  r={r:.3f}  R^2={r**2:.3f}")
print(f"fit: alpha = {a:.3f} + ({b:.4f} x breadth)   residual SE s={s:.3f}")
print(f"historical breadth range: {x.min():.2f} .. {x.max():.2f}  (mean {xbar:.2f})")
print(f"historical alpha range:   {y.min():.2f} .. {y.max():.2f}")
print()
print("95% PREDICTION interval for a new year, across candidate breadth values:")
print(f"{'breadth':>8}{'predict':>9}{'lo':>8}{'hi':>8}{'width':>8}   0.58 inside?")
grid = np.arange(44, 62.1, 2.0)
falsify_lo, falsify_hi = [], []
for xs in grid:
    yhat, lo, hi, w = predict(float(xs))
    inside = lo <= OBSERVED_LIVE_ALPHA <= hi
    (falsify_lo if not inside else falsify_hi).append(float(xs))
    print(f"{xs:>8.1f}{yhat:>9.2f}{lo:>8.2f}{hi:>8.2f}{w:>8.2f}   {'YES' if inside else 'NO  <-- would falsify'}")

# Solve for the breadth values where 0.58 sits exactly on the interval edge.
fine = np.arange(30, 80, 0.05)
inside_mask = np.array([predict(float(v))[1] <= OBSERVED_LIVE_ALPHA <= predict(float(v))[2] for v in fine])
consistent = fine[inside_mask]
band = (float(consistent.min()), float(consistent.max())) if consistent.size else None

print()
print("POWER: breadth values for which alpha=+0.58 is CONSISTENT with the model:")
print(f"   {band[0]:.2f}%  ..  {band[1]:.2f}%" if band else "   none")
print(f"   historical observed breadth spanned {x.min():.2f}..{x.max():.2f},")
print(f"   so within the historical range the test {'CAN' if band[0] > x.min() or band[1] < x.max() else 'CANNOT'} fail.")

prereg = {
    "committed_before_observing_live_breadth": True,
    "model": {"intercept": a, "slope": b, "residual_se": s, "r": r, "r_squared": r**2,
              "n": n, "df": n - 2, "t_crit_0975": t_crit, "xbar": xbar, "sxx": sxx},
    "historical_breadth_range": [float(x.min()), float(x.max())],
    "historical_alpha_range": [float(y.min()), float(y.max())],
    "observed_live_alpha_pct": OBSERVED_LIVE_ALPHA,
    "decision_rule": (
        "Compute 2025-26 breadth with regime_analysis._year_regime_stats' definition. "
        "CONFIRMS the breadth model if +0.58 falls inside the 95% prediction interval "
        "at that breadth; CONTRADICTS it if outside. If live breadth falls outside "
        "[{:.2f}, {:.2f}] the result is an extrapolation and is reported as such."
    ).format(float(x.min()), float(x.max())),
    "alpha_058_consistent_for_breadth_between": band,
    "grid": [{"breadth": float(v), **dict(zip(("predict", "lo", "hi", "width"),
             map(float, predict(float(v)))))} for v in grid],
}
p = write_artifact(kind="prereg_breadth", config={"source": str(SRC)}, result=prereg,
                   out_dir=Path(__file__).parent / "artifacts")
print(f"\npre-registration artifact: {p}")
