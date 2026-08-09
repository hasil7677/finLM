# Pre-registration - the fade-alpha decay analysis

**Written 2026-08-09, after the 2021-2024 backfill completed but BEFORE running
any per-year analysis on it.** The 2010-2020 results are already known; the
2021-2024 results are not. This file fixes the decision rules while that is still
true.

Prior art: `prereg_breadth.py` (falsified) and `prereg_multiple_testing.md`.

## The question

Fade alpha measured +2.32%/trade net over 2010-2020 and +0.58%/trade net over the
2025-05 → 2026-08 live window. Same config, same cost model, same code path.
Survivorship is excluded (README.md). Breadth was pre-registered as the
explanation and **falsified**. The cause is open.

## Data

`market_historical.db`, now 2010-01-04 → 2024-07-05, 5,826,709 rows. Years
2010-2023 are complete (243-251 trading days each). **2024 is partial (125 days,
ends 2024-07-05)** because the old bhavcopy format was retired mid-year.

A residual gap remains: **2024-07-06 → 2025-05-25** is in neither database. The
series is therefore continuous 2010→2024.5, then a ~11-month hole, then the live
window. This is stated wherever the series is shown; it is not "continuous to
present".

Config: the §6 pullback-fade - `entry_style=pullback`, `stop_mult=2.0`,
`target_mult=2.5`, `horizon=10`, `cost_pct=0.4` (net), unchanged from the
existing regime analysis so the new years are comparable to the old ones.

## Handling decisions, fixed now

- **2024 is excluded from all trend and break tests** (partial year). Reported
  separately as a data point, never used to fit a slope.
- **2020 is included by default** but every test is also run with 2020 excluded,
  and both are reported. COVID is a genuine confound: a fade strategy in
  Mar-Apr 2020 will produce extreme values in either direction, and a
  decay-from-a-2020-spike is a different finding from secular decay. If the two
  versions disagree on the verdict, **the verdict is "inconclusive"**, not
  whichever one is more interesting.
- The primary series is **per-year net fade alpha**, n = 14 (2010-2023).

## The three hypotheses and how they are distinguished

**H1 - monotonic decay.** The edge erodes gradually as the market matures.
- *Criterion:* Spearman rank correlation between year and alpha is negative with
  p < 0.05, **and** the fitted linear slope stays negative when any single year
  is dropped (leave-one-out robustness).

**H2 - structural break.** The edge is stable, then drops at a specific date.
- *Criterion:* the maximum two-sample difference in mean alpha over all splits
  is significant (permutation test, 10,000 shuffles of the year labels,
  p < 0.05), **and** the best split falls within ±1 year of a named market-
  structure event fixed below.
- Candidate events, named in advance so the date cannot be chosen post hoc:
  - **2021** - the retail participation surge (demat account growth)
  - **2021-09** - the peak-margin regime, final phase
  - **2022-02 → 2023-01** - T+1 settlement, phased then complete
  - **2018** - SEBI's phased physical-settlement / derivatives changes
- If the best split is significant but lands on no named event, that is reported
  as **an unexplained break**, not retrofitted to whichever event is nearest.

**H3 - the live window is unusual, no trend.** 2010-2023 shows neither decay nor
break, and the low live number is an outlier.
- *Criterion:* H1 and H2 both fail, and live-window alpha falls outside the 10th
  percentile of the 2010-2023 per-year distribution.

**H4 - none of the above.** If H1, H2 and H3 all fail, the verdict is
**"decay unexplained; the per-year series does not distinguish these"** and the
analysis stops.

## Stopping rule - binding

Whichever branch fires, **the analysis ends there.** Explicitly ruled out in
advance:

- adding regime covariates beyond the breadth/vol already tested, hunting for one
  that correlates
- re-running with a different fade config to find a version that decays more
  cleanly
- sub-setting to a subsample (sector, price band, liquidity tier) that shows a
  stronger pattern
- moving to monthly resolution to get more points *for the purpose of* reaching
  significance

The one permitted follow-up, and only if H2 fires with a dated break: check
whether the anomaly study's sub-period split moves at the same date. That is a
pre-specified corroboration, not a search.

## Prediction (recorded before running)

I expect **H1 (monotonic decay)** to be the closest fit, with a negative slope
that is **not** significant at n = 14 - i.e. the direction is right and the power
is insufficient. I expect the leave-one-out check to be unstable around 2020.

I expect the 2021-2023 years to land between the 2010-2020 mean (+2.32%) and the
live-window value (+0.58%), roughly in the +1.0% to +2.0% range. If instead they
come in at or above +2.32%, that argues for H3 and makes the live window the
anomaly rather than the endpoint of a trend.

Second prediction: the sign test will extend but weaken. 11/11 positive years
becomes 14/14 only if 2021, 2022 and 2023 are all positive. I expect at least one
non-positive year in that stretch, which would take the sign test from
p = 1/2048 to roughly p = 0.006 - still strong, no longer spectacular.
