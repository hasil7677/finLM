"""
anomalies.py
────────────
Cross-sectional anomaly replication on Indian equities.

The question: of the equity anomalies documented in the (overwhelmingly US)
literature, which survive on NSE data with point-in-time discipline and
realistic costs? Hou/Xue/Zhang's "Replicating Anomalies" found most US results
do not replicate once methodology is held constant. Nobody has run the Indian
version.

WHY THIS IS A SEPARATE ENGINE FROM backtest.py
──────────────────────────────────────────────
backtest.py is event-driven: screen for an event, take a signal, exit on an ATR
stop/target. That is a trading rule. The anomaly literature uses cross-sectional
portfolio sorts: rank the universe on a characteristic, form quantile
portfolios, hold, measure the top-minus-bottom spread. Testing momentum through
an ATR-exit engine measures the exit rule, not the anomaly, and the number would
not be comparable to any published result. So this module implements the
standard methodology and reuses backtest.py only for the data layer
(corporate-action-adjusted panel) and the liquidity universe definition.

METHODOLOGY
───────────
  • Monthly formation. Characteristics use data through month t only; returns
    are earned over t+1. No characteristic may reference the holding period.
  • Point-in-time liquid universe, re-derived every month from that month's own
    price/volume/turnover — never from a current symbol list.
  • Equal-weight quantile portfolios (the DB has no shares outstanding, so
    value-weighting and a true size factor are not available — stated as a
    limitation rather than proxied badly).
  • Long-short = top quantile minus bottom quantile, where every characteristic
    is SIGNED so that high = expected high return. The sign convention is
    recorded per anomaly; getting one backwards inverts the result.
  • Costs charged on realised portfolio turnover at each rebalance, both legs.
  • t-statistics use Newey-West with lag = holding_months, because overlapping
    holding periods make raw t-stats badly overstated.

SHORTING: the short leg is not implementable in an Indian cash account (see
CLAUDE.md §7). Long-short spreads are reported because that is what the
literature reports and what makes these numbers comparable; the long-only leg is
reported alongside for anything an actual cash account could hold.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from llmfin.corporate_actions import adjust_corporate_actions
from llmfin.data_store import DB_PATH
from llmfin.provenance import write_artifact


@dataclass
class AnomalyConfig:
    n_portfolios: int = 5           # quintiles; the liquid NSE universe is too
                                    # thin for stable deciles in early years
    holding_months: int = 1
    min_names: int = 50             # skip a formation month with a thin universe
    cost_pct_per_side: float = 0.20  # 0.40% round trip, matching backtest.py
    # Point-in-time liquidity screen, same thresholds as ScanConfig
    min_price: float = 100.0
    min_avg_volume: float = 500_000
    min_turnover: float = 10.0 * 1e7
    min_history_months: int = 13    # need 12m of history for momentum
    symbol_batch: int = 400         # symbols per SQL batch (memory bound)


# ── data layer ──────────────────────────────────────────────────────────────

def _monthly_panel(db_path: Path, cfg: AnomalyConfig, adjust: bool = True) -> pd.DataFrame:
    """Collapse the daily panel to one row per (symbol, month).

    Processed in symbol batches: corporate-action adjustment is per-symbol, so
    batching is safe, and it keeps the 4.15M-row historical DB inside this
    machine's memory (CLAUDE.md §4 records the same constraint for
    regime_analysis.py).
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    symbols = [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM daily_prices ORDER BY symbol")]

    out: list[pd.DataFrame] = []
    for i in range(0, len(symbols), cfg.symbol_batch):
        batch = symbols[i:i + cfg.symbol_batch]
        q = ("SELECT symbol, date, open, high, low, close, prev_close, volume, turnover "
             f"FROM daily_prices WHERE symbol IN ({','.join('?' * len(batch))}) "
             "ORDER BY symbol, date")
        df = pd.read_sql_query(q, conn, params=batch)
        if df.empty:
            continue
        if adjust:
            df, _ = adjust_corporate_actions(df)

        df = df[df["prev_close"] > 0].copy()
        df["ret"] = df["close"] / df["prev_close"] - 1
        # Guard against residual unadjusted corporate actions: a single +900%
        # day would dominate every moment-based characteristic in that month.
        df = df[df["ret"].between(-0.75, 3.0)]
        df["month"] = df["date"].str.slice(0, 7)

        g = df.groupby(["symbol", "month"], sort=False)
        m = g.agg(
            close=("close", "last"),
            price_mean=("close", "mean"),
            n_days=("ret", "size"),
            mret=("ret", lambda s: float((1.0 + s).prod() - 1.0)),
            dvol=("ret", "std"),
            dmax=("ret", "max"),
            dskew=("ret", "skew"),
            avg_volume=("volume", "mean"),
            avg_turnover=("turnover", "mean"),
            amihud=("ret", "size"),   # placeholder, overwritten below
        ).reset_index()

        # Amihud illiquidity: average |return| per rupee of turnover.
        df["_illiq"] = df["ret"].abs() / df["turnover"].where(df["turnover"] > 0)
        illiq = df.groupby(["symbol", "month"], sort=False)["_illiq"].mean().reset_index(name="amihud_v")
        m = m.merge(illiq, on=["symbol", "month"], how="left")
        m["amihud"] = m["amihud_v"] * 1e9   # scale to a readable magnitude
        m = m.drop(columns=["amihud_v"])
        out.append(m)

    conn.close()
    panel = pd.concat(out, ignore_index=True)
    panel = panel[panel["n_days"] >= 5]          # a month needs real trading
    return panel.sort_values(["symbol", "month"]).reset_index(drop=True)


# ── characteristics ─────────────────────────────────────────────────────────
# Every function returns a Series SIGNED so that HIGH = expected HIGH return.
# The `expect` note records what the literature predicts, so a sign flip is
# visible in review rather than silently inverting a result.

@dataclass
class Anomaly:
    name: str
    fn: Callable[[pd.DataFrame], pd.Series]
    expect: str
    reference: str


def _g(p: pd.DataFrame, col: str):
    return p.groupby("symbol", sort=False)[col]


def _cum(p: pd.DataFrame, start: int, end: int) -> pd.Series:
    """Cumulative return over months t-start .. t-end inclusive (both lags)."""
    lr = np.log1p(p["mret"].clip(lower=-0.99))
    g = p.assign(_lr=lr).groupby("symbol", sort=False)["_lr"]
    roll = g.transform(lambda s: s.shift(end).rolling(start - end + 1).sum())
    return np.expm1(roll)


def _rolling_beta(p: pd.DataFrame, window: int = 24) -> pd.Series:
    """Beta of monthly returns on the equal-weight market, trailing `window`."""
    mkt = p.groupby("month")["mret"].mean().rename("mkt")
    q = p.merge(mkt, on="month", how="left")
    def _b(sub: pd.DataFrame) -> pd.Series:
        cov = sub["mret"].rolling(window).cov(sub["mkt"])
        var = sub["mkt"].rolling(window).var()
        return (cov / var).shift(1)   # strictly prior information
    return q.groupby("symbol", sort=False, group_keys=False).apply(_b, include_groups=False)


REGISTRY: list[Anomaly] = [
    Anomaly("short_term_reversal", lambda p: -_g(p, "mret").shift(0),
            "buy last month's losers", "Jegadeesh 1990; Lehmann 1990"),
    Anomaly("momentum_12_1", lambda p: _cum(p, 12, 2),
            "buy 12-month winners, skipping the last month", "Jegadeesh & Titman 1993"),
    Anomaly("long_term_reversal", lambda p: -_cum(p, 60, 13),
            "buy 5-year losers", "De Bondt & Thaler 1985"),
    Anomaly("high_52w", lambda p: p["close"] / _g(p, "close").transform(
                lambda s: s.rolling(12, min_periods=6).max()),
            "buy stocks near their 52-week high", "George & Hwang 2004"),
    Anomaly("low_volatility", lambda p: -_g(p, "dvol").shift(0),
            "buy low-volatility stocks", "Ang et al. 2006; Baker et al. 2011"),
    Anomaly("max_effect", lambda p: -_g(p, "dmax").shift(0),
            "avoid lottery-like stocks with a big single-day gain", "Bali et al. 2011"),
    Anomaly("illiquidity", lambda p: _g(p, "amihud").shift(0),
            "illiquid stocks earn a premium", "Amihud 2002"),
    Anomaly("low_turnover", lambda p: -(p["avg_turnover"] / p["price_mean"]),
            "buy low-turnover stocks", "Datar et al. 1998"),
    Anomaly("low_beta", lambda p: -_rolling_beta(p),
            "betting against beta", "Frazzini & Pedersen 2014"),
    Anomaly("neg_skew", lambda p: -_g(p, "dskew").shift(0),
            "avoid positively-skewed lottery stocks", "Boyer et al. 2010"),
    Anomaly("volume_shock", lambda p: -(p["avg_volume"] / _g(p, "avg_volume").transform(
                lambda s: s.shift(1).rolling(12, min_periods=6).mean())),
            "avoid stocks with abnormal volume (attention/overreaction)",
            "Gervais et al. 2001"),
]


# ── portfolio sort ──────────────────────────────────────────────────────────

def _liquid_mask(p: pd.DataFrame, cfg: AnomalyConfig) -> pd.Series:
    return ((p["price_mean"] >= cfg.min_price)
            & (p["avg_volume"] >= cfg.min_avg_volume)
            & (p["avg_turnover"] >= cfg.min_turnover))


def portfolio_sort(panel: pd.DataFrame, char: str, cfg: AnomalyConfig) -> pd.DataFrame:
    """Form quantile portfolios on `char` at each month end, hold for
    cfg.holding_months, and return the per-month long/short/spread returns."""
    p = panel.copy()
    p["fwd"] = p.groupby("symbol", sort=False)["mret"].shift(-1)
    p["hist"] = p.groupby("symbol", sort=False).cumcount()
    p = p[_liquid_mask(p, cfg)
          & p[char].notna() & p["fwd"].notna()
          & (p["hist"] >= cfg.min_history_months)]

    rows, prev_long, prev_short = [], set(), set()
    for month, g in p.groupby("month", sort=True):
        if len(g) < cfg.min_names:
            continue
        try:
            q = pd.qcut(g[char].rank(method="first"), cfg.n_portfolios, labels=False)
        except ValueError:
            continue
        g = g.assign(_q=q)
        lo, hi = g[g["_q"] == 0], g[g["_q"] == cfg.n_portfolios - 1]
        if len(lo) < 3 or len(hi) < 3:
            continue

        long_names, short_names = set(hi["symbol"]), set(lo["symbol"])
        # Realised turnover: fraction of each leg replaced since last rebalance.
        t_long = 1.0 if not prev_long else 1 - len(long_names & prev_long) / len(long_names)
        t_short = 1.0 if not prev_short else 1 - len(short_names & prev_short) / len(short_names)
        cost = (t_long + t_short) * cfg.cost_pct_per_side / 100.0

        r_long, r_short = float(hi["fwd"].mean()), float(lo["fwd"].mean())
        rows.append({
            "month": month, "n": len(g),
            "long": r_long, "short": r_short,
            "spread_gross": r_long - r_short,
            "spread_net": r_long - r_short - cost,
            "long_net": r_long - t_long * cfg.cost_pct_per_side / 100.0,
            "turnover": (t_long + t_short) / 2,
        })
        prev_long, prev_short = long_names, short_names
    return pd.DataFrame(rows)


def conditional_sort(panel: pd.DataFrame, char: str, cfg: AnomalyConfig,
                     control: str = "avg_turnover", n_control: int = 3) -> pd.DataFrame:
    """Sort on `char` *within* buckets of `control`, then average the spreads.

    This is how you answer "is this effect just liquidity wearing a costume?".
    The unconditional universe sweep varies liquidity and the characteristic at
    the same time, so a gradient across tiers is ambiguous. Here liquidity is
    held fixed inside each bucket, so a surviving spread is attributable to the
    characteristic itself.
    """
    p = panel.copy()
    p["fwd"] = p.groupby("symbol", sort=False)["mret"].shift(-1)
    p["hist"] = p.groupby("symbol", sort=False).cumcount()
    p = p[_liquid_mask(p, cfg)
          & p[char].notna() & p["fwd"].notna() & p[control].notna()
          & (p["hist"] >= cfg.min_history_months)]

    rows = []
    for month, g in p.groupby("month", sort=True):
        if len(g) < cfg.min_names:
            continue
        try:
            g = g.assign(_c=pd.qcut(g[control].rank(method="first"), n_control, labels=False))
        except ValueError:
            continue
        spreads, longs = [], []
        for _, sub in g.groupby("_c"):
            if len(sub) < cfg.n_portfolios * 3:
                continue
            try:
                q = pd.qcut(sub[char].rank(method="first"), cfg.n_portfolios, labels=False)
            except ValueError:
                continue
            sub = sub.assign(_q=q)
            lo, hi = sub[sub["_q"] == 0], sub[sub["_q"] == cfg.n_portfolios - 1]
            if len(lo) < 3 or len(hi) < 3:
                continue
            spreads.append(float(hi["fwd"].mean() - lo["fwd"].mean()))
            longs.append(float(hi["fwd"].mean()))
        if not spreads:
            continue
        # Costs: assume full turnover each rebalance — conservative, and the
        # bucket-level name overlap is not tracked here.
        cost = 2 * cfg.cost_pct_per_side / 100.0
        s = float(np.mean(spreads))
        rows.append({"month": month, "n": len(g), "long": float(np.mean(longs)),
                     "short": float(np.mean(longs)) - s,
                     "spread_gross": s, "spread_net": s - cost,
                     "long_net": float(np.mean(longs)) - cfg.cost_pct_per_side / 100.0,
                     "turnover": 1.0})
    return pd.DataFrame(rows)


# ── statistics ──────────────────────────────────────────────────────────────

def newey_west_t(x: np.ndarray, lags: int) -> float:
    """t-stat on the mean with a Newey-West HAC variance.

    Overlapping holding periods induce serial correlation; a raw t-stat on
    overlapping returns is one of the most common ways a backtest overstates
    significance."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    T = len(x)
    if T < 3:
        return float("nan")
    d = x - x.mean()
    var = float((d @ d) / T)
    for l in range(1, min(lags, T - 1) + 1):
        cov = float((d[l:] @ d[:-l]) / T)
        var += 2.0 * (1.0 - l / (lags + 1.0)) * cov
    if var <= 0:
        return float("nan")
    return float(x.mean() / np.sqrt(var / T))


def evaluate(res: pd.DataFrame, cfg: AnomalyConfig) -> dict:
    if res.empty:
        return {"months": 0}
    g, n, lo = res["spread_gross"].to_numpy(), res["spread_net"].to_numpy(), res["long_net"].to_numpy()
    ann = 12 / cfg.holding_months
    def sharpe(v):
        sd = float(np.nanstd(v, ddof=1))
        return round(float(np.nanmean(v) / sd * np.sqrt(ann)), 2) if sd > 0 else None
    return {
        "months": len(res),
        "mean_gross_pct_pm": round(float(np.nanmean(g)) * 100, 3),
        "mean_net_pct_pm": round(float(np.nanmean(n)) * 100, 3),
        "ann_net_pct": round((float(np.nanmean(n)) * 12) * 100, 2),
        # Gross t-stat answers "is there a signal at all"; net answers "does it
        # survive costs". A zero-alpha strategy has a significantly NEGATIVE net
        # t-stat purely from turnover, so the two are not interchangeable.
        "t_stat_gross_nw": round(newey_west_t(g, cfg.holding_months), 2),
        "t_stat_net_nw": round(newey_west_t(n, cfg.holding_months), 2),
        "sharpe_net": sharpe(n),
        "pct_months_positive": round(float((n > 0).mean()) * 100, 1),
        "avg_turnover": round(float(res["turnover"].mean()), 3),
        "long_only_ann_net_pct": round(float(np.nanmean(lo)) * 12 * 100, 2),
        "avg_names": int(res["n"].mean()),
    }


def _two_sided_p(t: float, df: int) -> float:
    """Two-sided p-value from a t-statistic, normal approximation.

    df is large here (>100 months) so the normal tail is within ~1% of the
    t-distribution; avoids a scipy dependency for a number used only for
    ranking in Benjamini-Hochberg."""
    if t is None or t != t:
        return 1.0
    z = abs(float(t))
    # Abramowitz & Stegun 7.1.26 error-function approximation
    x = z / np.sqrt(2.0)
    a1, a2, a3, a4, a5, p = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429, 0.3275911
    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    tt = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * tt + a4) * tt) + a3) * tt + a2) * tt + a1) * tt * np.exp(-x * x)
    erf = sign * y
    return float(max(min(1.0 - erf, 1.0), 0.0))


def benjamini_hochberg(pvals: dict[str, float], q: float = 0.10) -> dict:
    """BH step-up procedure. Returns which hypotheses survive at FDR level q.

    Controls the expected proportion of false discoveries among rejections,
    which is the right target when screening a family of candidate anomalies —
    Bonferroni controls the probability of *any* false positive and is far too
    conservative for this purpose."""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    k_max = 0
    for i, (_, p) in enumerate(items, start=1):
        if p <= i / m * q:
            k_max = i
    survivors = [name for name, _ in items[:k_max]]
    return {
        "q": q, "n_tests": m, "n_survivors": k_max,
        "survivors": survivors,
        "ranked": [{"name": n, "p": round(p, 4),
                    "bh_threshold": round((i + 1) / m * q, 4),
                    "survives": n in survivors}
                   for i, (n, p) in enumerate(items)],
    }


def bootstrap_max_t(panel: pd.DataFrame, chars: list[str], cfg: AnomalyConfig,
                    n_iter: int = 1000, seed: int = 0) -> dict:
    """Distribution of the LARGEST |t| across the family under the null.

    Forward returns are permuted *within each month*, destroying any
    cross-sectional relationship to the characteristics while leaving each
    month's return distribution and the across-month structure intact. The
    observed max |t| is then compared to this distribution.

    This answers the question per-test correction cannot: given that we ran a
    whole family, is the biggest thing we found bigger than what a family of
    this size produces from noise?
    """
    rng = np.random.default_rng(seed)
    p = panel.copy()
    p["fwd"] = p.groupby("symbol", sort=False)["mret"].shift(-1)
    p["hist"] = p.groupby("symbol", sort=False).cumcount()
    p = p[_liquid_mask(p, cfg) & p["fwd"].notna() & (p["hist"] >= cfg.min_history_months)]
    p = p[["month", "symbol", "fwd"] + chars].dropna(subset=["fwd"])

    # Permuting `fwd` within a month cannot change which names fall in which
    # quantile — membership depends only on the characteristic. So compute
    # memberships ONCE and make each bootstrap iteration pure numpy over fixed
    # index sets. Same estimator, ~2 orders of magnitude faster.
    months: list[np.ndarray] = []          # fwd values per month
    members: dict[str, list[tuple]] = {c: [] for c in chars}
    for mi, (_, g) in enumerate(p.groupby("month", sort=True)):
        fwd = g["fwd"].to_numpy(dtype=float)
        months.append(fwd)
        for c in chars:
            vals = g[c].to_numpy(dtype=float)
            ok = np.where(~np.isnan(vals))[0]
            if len(ok) < cfg.min_names:
                continue
            try:
                q = pd.qcut(pd.Series(vals[ok]).rank(method="first"),
                            cfg.n_portfolios, labels=False).to_numpy()
            except ValueError:
                continue
            lo_idx, hi_idx = ok[q == 0], ok[q == cfg.n_portfolios - 1]
            if len(lo_idx) < 3 or len(hi_idx) < 3:
                continue
            members[c].append((mi, lo_idx, hi_idx))

    def family_max_t(month_vals: list[np.ndarray]) -> float:
        best = 0.0
        for c in chars:
            mem = members[c]
            if len(mem) <= 10:
                continue
            spreads = np.array([month_vals[mi][hi].mean() - month_vals[mi][lo].mean()
                                for mi, lo, hi in mem])
            best = max(best, abs(newey_west_t(spreads, cfg.holding_months)))
        return best

    observed = family_max_t(months)
    null_max = np.array([
        family_max_t([rng.permutation(v) for v in months]) for _ in range(n_iter)
    ])
    return {
        "observed_max_abs_t": round(float(observed), 3),
        "null_max_mean": round(float(null_max.mean()), 3),
        "null_max_p95": round(float(np.percentile(null_max, 95)), 3),
        "p_value": round(float((null_max >= observed).mean()), 4),
        "n_iter": n_iter,
        "n_chars": len(chars),
    }


def run_all(db_path: Path, cfg: Optional[AnomalyConfig] = None,
            only: Optional[list[str]] = None, adjust: bool = True) -> dict:
    cfg = cfg or AnomalyConfig()
    panel = _monthly_panel(db_path, cfg, adjust=adjust)
    chosen = [a for a in REGISTRY if not only or a.name in only]
    for a in chosen:
        panel[a.name] = a.fn(panel)

    results, per_year = {}, {}
    for a in chosen:
        res = portfolio_sort(panel, a.name, cfg)
        results[a.name] = {**evaluate(res, cfg), "expect": a.expect, "reference": a.reference}
        if not res.empty:
            res = res.assign(year=res["month"].str.slice(0, 4))
            per_year[a.name] = {y: round(float(sub["spread_net"].sum()) * 100, 2)
                                for y, sub in res.groupby("year")}
    return {
        "config": asdict(cfg),
        "panel": {"symbol_months": len(panel),
                  "months": int(panel["month"].nunique()),
                  "symbols": int(panel["symbol"].nunique()),
                  "range": [panel["month"].min(), panel["month"].max()]},
        "results": results,
        "net_spread_by_year_pct": per_year,
    }


def format_table(out: dict) -> str:
    rows = sorted(out["results"].items(), key=lambda kv: -(kv[1].get("t_stat_net_nw") or -99))
    w = f"{'anomaly':<22}{'ann net %':>10}{'t (NW)':>8}{'Sharpe':>8}{'%+mo':>7}{'turn':>7}{'LO ann%':>9}  survives"
    lines = [w, "-" * len(w)]
    for name, r in rows:
        if not r.get("months"):
            lines.append(f"{name:<22}{'no data':>10}")
            continue
        t = r.get("t_stat_net_nw")
        ok = "YES" if (t is not None and t == t and abs(t) >= 2.0 and r["ann_net_pct"] > 0) else "no"
        lines.append(f"{name:<22}{r['ann_net_pct']:>10.2f}{t:>8.2f}"
                     f"{(r['sharpe_net'] or 0):>8.2f}{r['pct_months_positive']:>7.1f}"
                     f"{r['avg_turnover']:>7.2f}{r['long_only_ann_net_pct']:>9.2f}  {ok}")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Cross-sectional anomaly replication on NSE data")
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--portfolios", type=int, default=AnomalyConfig.n_portfolios)
    p.add_argument("--holding-months", type=int, default=AnomalyConfig.holding_months)
    p.add_argument("--cost-pct-per-side", type=float, default=AnomalyConfig.cost_pct_per_side)
    p.add_argument("--only", nargs="*", help="subset of anomaly names")
    p.add_argument("--no-adjust-splits", action="store_true")
    a = p.parse_args()
    cfg = AnomalyConfig(n_portfolios=a.portfolios, holding_months=a.holding_months,
                        cost_pct_per_side=a.cost_pct_per_side)
    out = run_all(Path(a.db), cfg, only=a.only, adjust=not a.no_adjust_splits)
    print(format_table(out))
    print(json.dumps(out["panel"], indent=2))
    art = write_artifact(kind="anomalies", config=out["config"],
                         result={k: v for k, v in out.items() if k != "config"},
                         db_path=Path(a.db))
    print(f"\nartifact: {art}")


if __name__ == "__main__":
    main()
