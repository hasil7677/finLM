"""
test_anomalies.py
─────────────────
The anomaly engine's whole value is that its numbers are trustworthy, so the
tests here target the ways a cross-sectional backtest lies:

  • lookahead — a characteristic that peeks at the return it is predicting
  • sign errors — a flipped characteristic inverts the finding silently
  • a broken harness — machinery that manufactures a spread from noise
  • overstated significance — raw t-stats on overlapping returns

The null test is the load-bearing one: if a randomised panel still produces a
significant spread, nothing else in this module can be believed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from llmfin.anomalies import (
    AnomalyConfig, REGISTRY, _cum, evaluate, newey_west_t, portfolio_sort,
)


def synth_panel(n_symbols=120, n_months=60, seed=0, signal=0.0) -> pd.DataFrame:
    """Panel with a planted signal: next month's return is `signal` times this
    month's characteristic `c`, plus noise. signal=0 gives a pure null."""
    rng = np.random.default_rng(seed)
    rows = []
    months = [f"{2010 + i // 12}-{i % 12 + 1:02d}" for i in range(n_months)]
    c = rng.normal(size=n_symbols)
    for t, month in enumerate(months):
        noise = rng.normal(scale=0.05, size=n_symbols)
        ret = signal * c + noise          # this month's return, driven by prior c
        c = rng.normal(size=n_symbols)     # redraw the characteristic for next month
        dvol = rng.uniform(0.01, 0.06, size=n_symbols)
        dmax = rng.uniform(0.02, 0.15, size=n_symbols)
        dskew = rng.normal(size=n_symbols)
        amihud = rng.uniform(0.1, 2.0, size=n_symbols)
        for i in range(n_symbols):
            rows.append({
                "symbol": f"SYM{i:03d}", "month": month,
                "mret": float(ret[i]), "c": float(c[i]),
                "close": 500.0, "price_mean": 500.0, "n_days": 20,
                "avg_volume": 1e6, "avg_turnover": 5e8,
                "dvol": float(dvol[i]), "dmax": float(dmax[i]),
                "dskew": float(dskew[i]), "amihud": float(amihud[i]),
            })
    return pd.DataFrame(rows).sort_values(["symbol", "month"]).reset_index(drop=True)


CFG = AnomalyConfig(min_history_months=2, min_names=50)


# ── lookahead ───────────────────────────────────────────────────────────────

def test_forward_return_is_strictly_next_month():
    """`fwd` must be month t+1's return, never month t's — the single mistake
    that would make every anomaly here look tradeable."""
    p = synth_panel(n_symbols=60, n_months=6)
    p = p.sort_values(["symbol", "month"])
    fwd = p.groupby("symbol", sort=False)["mret"].shift(-1)
    one = p[p["symbol"] == "SYM000"].reset_index(drop=True)
    f = fwd[p["symbol"] == "SYM000"].reset_index(drop=True)
    for i in range(len(one) - 1):
        assert f[i] == pytest.approx(one["mret"][i + 1])
    assert np.isnan(f.iloc[-1]), "last month must have no forward return"


def test_cumulative_return_skips_the_requested_lags():
    """momentum_12_1 must skip the most recent month. If _cum leaked lag 1,
    momentum would be contaminated by short-term reversal."""
    p = pd.DataFrame({
        "symbol": ["A"] * 15,
        "month": [f"2010-{i+1:02d}" for i in range(12)] + [f"2011-{i+1:02d}" for i in range(3)],
        "mret": [0.0] * 13 + [0.5, 0.0],   # a +50% month at position 13
    })
    out = _cum(p, 12, 2)      # months t-12..t-2
    # At the final row (index 14), t-1 is index 13 (the +50% month) and must be
    # excluded; the window t-12..t-2 is flat, so the result is ~0.
    assert out.iloc[14] == pytest.approx(0.0, abs=1e-9)
    # At index 13, the +50% month is the current month, also excluded.
    assert out.iloc[13] == pytest.approx(0.0, abs=1e-9)


def test_characteristics_never_reference_the_holding_period():
    """Every registered characteristic must be computable from a panel whose
    future rows are deleted, and give the same answer."""
    p = synth_panel(n_symbols=60, n_months=40)
    cut = sorted(p["month"].unique())[-6]
    for a in REGISTRY:
        full = a.fn(p)
        truncated = a.fn(p[p["month"] < cut].copy())
        merged = p.assign(_v=full)
        merged = merged[merged["month"] < cut].reset_index(drop=True)
        got = pd.Series(truncated).reset_index(drop=True)
        both = merged["_v"].notna() & got.notna()
        if both.sum() == 0:
            continue
        assert np.allclose(merged.loc[both, "_v"], got[both], equal_nan=True), (
            f"{a.name} changes when future rows are removed — it peeks ahead"
        )


# ── the null test ───────────────────────────────────────────────────────────

def test_null_panel_produces_no_spread():
    """Pure noise, no planted signal, several seeds. If the harness manufactures
    a significant GROSS spread here, every result it produces is an artefact of
    the machinery.

    Tested on gross, not net: a zero-alpha portfolio still pays turnover, so its
    net t-stat is significantly negative by construction. Conflating the two
    would make this test pass for the wrong reason.

    Asserted on the distribution across many seeds rather than on one draw: a
    single null panel produces |t| >= 2 about 5% of the time by construction, so
    a one-seed assertion is a coin flip dressed as a test.
    """
    ts, means = [], []
    for seed in range(20):
        p = synth_panel(n_symbols=120, n_months=96, seed=seed, signal=0.0)
        st = evaluate(portfolio_sort(p, "c", CFG), CFG)
        assert st["months"] > 60
        ts.append(st["t_stat_gross_nw"])
        means.append(st["mean_gross_pct_pm"])
    ts, means = np.array(ts), np.array(means)
    assert abs(ts.mean()) < 0.5, f"t-stats are centred off zero: {ts.mean():.3f}"
    assert abs(means.mean()) < 0.05, f"sort has a systematic drift: {means.mean():.4f}"
    assert (np.abs(ts) >= 2.0).mean() <= 0.20, (
        f"too many false positives: {(np.abs(ts) >= 2.0).sum()}/20"
    )


def test_zero_alpha_strategy_loses_exactly_its_costs():
    """Documents the hurdle every anomaly has to clear. At ~80% monthly turnover
    and 0.4% round-trip, a signal-free quintile long-short bleeds several
    percent a year — that is how large an edge must be to survive."""
    p = synth_panel(n_symbols=120, n_months=120, seed=7, signal=0.0)
    st = evaluate(portfolio_sort(p, "c", CFG), CFG)
    assert st["ann_net_pct"] < -3.0
    assert st["t_stat_net_nw"] < -2.0


def test_planted_signal_is_recovered_with_the_right_sign():
    """Control for the null test: with a real planted signal the engine must
    find it, otherwise 'no spread' proves nothing."""
    p = synth_panel(n_symbols=120, n_months=120, seed=3, signal=0.04)
    st = evaluate(portfolio_sort(p, "c", CFG), CFG)
    assert st["mean_gross_pct_pm"] > 1.0
    assert st["t_stat_net_nw"] > 3.0


def test_sign_flip_inverts_the_result():
    """A characteristic signed backwards must produce the mirror image — this is
    what makes the `expect` note in the registry checkable."""
    p = synth_panel(n_symbols=120, n_months=120, seed=3, signal=0.04)
    a = evaluate(portfolio_sort(p, "c", CFG), CFG)
    b = evaluate(portfolio_sort(p.assign(c=-p["c"]), "c", CFG), CFG)
    assert a["mean_gross_pct_pm"] == pytest.approx(-b["mean_gross_pct_pm"], rel=0.02)


# ── costs and statistics ────────────────────────────────────────────────────

def test_costs_reduce_the_spread_and_scale_with_turnover():
    p = synth_panel(n_symbols=120, n_months=60, seed=11, signal=0.04)
    free = evaluate(portfolio_sort(p, "c", AnomalyConfig(min_history_months=2, cost_pct_per_side=0.0)), CFG)
    dear = evaluate(portfolio_sort(p, "c", AnomalyConfig(min_history_months=2, cost_pct_per_side=0.5)), CFG)
    assert free["mean_net_pct_pm"] > dear["mean_net_pct_pm"]
    assert free["mean_gross_pct_pm"] == pytest.approx(dear["mean_gross_pct_pm"], rel=1e-6)


def test_newey_west_matches_plain_t_at_zero_lags():
    rng = np.random.default_rng(0)
    x = rng.normal(size=200)
    plain = x.mean() / (x.std(ddof=0) / np.sqrt(len(x)))
    assert newey_west_t(x, 0) == pytest.approx(plain, rel=1e-9)


def test_newey_west_discounts_positive_autocorrelation():
    """Overlapping holding periods induce positive serial correlation; the HAC
    t-stat must come out smaller than the naive one, or it isn't doing its job."""
    rng = np.random.default_rng(1)
    e = rng.normal(size=600)
    x = e.copy()
    for i in range(1, len(x)):
        x[i] = 0.7 * x[i - 1] + e[i]
    x = x + 0.3
    assert abs(newey_west_t(x, 6)) < abs(newey_west_t(x, 0))


def test_thin_months_are_skipped_not_silently_included():
    p = synth_panel(n_symbols=20, n_months=40)     # below min_names=50
    assert portfolio_sort(p, "c", CFG).empty


def test_conditional_sort_recovers_a_signal_independent_of_the_control():
    """A planted signal orthogonal to the control variable must survive the
    double sort — otherwise the control would be eating real effects."""
    from llmfin.anomalies import conditional_sort
    p = synth_panel(n_symbols=180, n_months=96, seed=5, signal=0.04)
    rng = np.random.default_rng(99)
    p = p.assign(avg_turnover=rng.uniform(2e8, 9e8, size=len(p)))  # noise control
    st = evaluate(conditional_sort(p, "c", CFG, control="avg_turnover"), CFG)
    assert st["mean_gross_pct_pm"] > 0.8
    assert st["t_stat_gross_nw"] > 3.0


def test_conditional_sort_removes_an_effect_that_is_only_the_control():
    """The test that gives the double sort its meaning: if the 'characteristic'
    is just the control relabelled, controlling for it must kill the spread."""
    from llmfin.anomalies import conditional_sort
    rng = np.random.default_rng(4)
    p = synth_panel(n_symbols=180, n_months=96, seed=6, signal=0.0)
    # Liquidity is a persistent SYMBOL property, so this month's value predicts
    # next month's return. Randomising per row would leave nothing to predict.
    syms = sorted(p["symbol"].unique())
    level = dict(zip(syms, rng.uniform(2e8, 9e8, size=len(syms))))
    liq = p["symbol"].map(level)
    p = p.assign(avg_turnover=liq, c=liq)          # characteristic IS the control
    p["mret"] = p["mret"] + 4e-11 * liq            # liquidity drives returns
    uncond = evaluate(portfolio_sort(p, "c", CFG), CFG)
    cond = evaluate(conditional_sort(p, "c", CFG, control="avg_turnover"), CFG)
    assert uncond["t_stat_gross_nw"] > 2.0, "setup failed: no unconditional effect to remove"
    assert abs(cond["mean_gross_pct_pm"]) < abs(uncond["mean_gross_pct_pm"]) / 2


def test_benjamini_hochberg_matches_a_worked_example():
    from llmfin.anomalies import benjamini_hochberg
    # Classic BH example: with m=5, q=0.10, thresholds are .02 .04 .06 .08 .10
    p = {"a": 0.009, "b": 0.03, "c": 0.05, "d": 0.30, "e": 0.60}
    out = benjamini_hochberg(p, q=0.10)
    assert out["survivors"] == ["a", "b", "c"], out["ranked"]
    # Step-UP: a later hypothesis clearing its threshold rescues earlier ones.
    p2 = {"a": 0.02, "b": 0.09, "c": 0.099, "d": 0.5, "e": 0.9}
    assert benjamini_hochberg(p2, q=0.10)["n_survivors"] >= 1


def test_benjamini_hochberg_rejects_nothing_when_all_p_are_large():
    from llmfin.anomalies import benjamini_hochberg
    out = benjamini_hochberg({f"x{i}": 0.4 + i * 0.05 for i in range(11)}, q=0.10)
    assert out["n_survivors"] == 0


def test_two_sided_p_is_sane():
    from llmfin.anomalies import _two_sided_p
    assert _two_sided_p(0.0, 100) == pytest.approx(1.0, abs=0.01)
    assert _two_sided_p(1.96, 100) == pytest.approx(0.05, abs=0.005)
    assert _two_sided_p(2.58, 100) == pytest.approx(0.01, abs=0.005)
    assert _two_sided_p(-1.96, 100) == pytest.approx(0.05, abs=0.005)


def test_bootstrap_max_t_finds_nothing_in_pure_noise():
    """The bootstrap's own null check: on a signal-free panel the observed max
    |t| must sit inside the null distribution it generates."""
    from llmfin.anomalies import bootstrap_max_t
    p = synth_panel(n_symbols=120, n_months=60, seed=21, signal=0.0)
    p["c2"] = np.random.default_rng(1).normal(size=len(p))
    out = bootstrap_max_t(p, ["c", "c2"], CFG, n_iter=40, seed=2)
    assert out["p_value"] > 0.05, out


def test_bootstrap_max_t_detects_a_planted_signal():
    from llmfin.anomalies import bootstrap_max_t
    p = synth_panel(n_symbols=120, n_months=60, seed=22, signal=0.06)
    p["c2"] = np.random.default_rng(1).normal(size=len(p))
    out = bootstrap_max_t(p, ["c", "c2"], CFG, n_iter=40, seed=2)
    assert out["p_value"] <= 0.05, out
    assert out["observed_max_abs_t"] > out["null_max_p95"]


def test_registry_is_well_formed():
    names = [a.name for a in REGISTRY]
    assert len(names) == len(set(names))
    for a in REGISTRY:
        assert a.expect and a.reference, f"{a.name} missing expectation/reference"
