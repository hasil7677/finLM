# Pre-registration — multiple-testing correction for the anomaly family

**Written 2026-08-09, before running any correction.** Committed so the stopping
rule cannot be revised after seeing the answer. Same discipline as
`prereg_breadth.py`, applied to *scope* rather than to a hypothesis — the failure
mode here is not a wrong result, it is an infinite regress of robustness checks
that never terminates.

## The problem

CLAUDE.md §6b reports 11 anomalies × 3 universe tiers = **33 tests**, all
uncorrected. At α = 0.05 roughly 1.7 significant results are expected from pure
noise. Several cells sit near |t| = 2. The reported findings may be nothing.

## What will be run

1. **Benjamini-Hochberg FDR** at q = 0.10 across the family, on the *net*
   two-sided p-values derived from the Newey-West t-statistics.
   - Primary family: the 11 anomalies in the **wide** universe (the tier where
     effects appeared). Family size 11.
   - Secondary, reported separately: all 33 cells.
2. **Bootstrap of the maximum |t|** under the null. The characteristic values
   are held fixed and the forward-return vector is permuted *within each month*,
   which destroys any cross-sectional signal while preserving each month's
   return distribution and the cross-month correlation structure. 1,000
   iterations.
   The observed max |t| across the family is compared to the bootstrap
   distribution. This answers "does ANY anomaly survive as a set", which is the
   question that matters and is not answered by per-test correction.

## Stopping rule — binding

- If **0 anomalies** survive BH at q = 0.10 in the primary family: **report the
  null and stop.** §6b is rewritten as a null result. No block bootstrap, no
  alternative estimator, no re-specification of the characteristic, no
  additional universe tiers. The finding becomes "these effects do not survive
  multiple-testing correction on this sample", which is a real and publishable
  finding.
- If **1–2 survive**: report them as surviving, with the bootstrap max-|t|
  p-value alongside. Do not add further robustness checks to strengthen them.
- If **3 or more survive**: proceed to the factor attribution (task #3), which
  is already planned and is the correct next control — not to more resampling.

In every branch, the universe gradient and the conditional double-sort results
are reported as-is, since they are descriptive rather than significance claims.

## What will NOT be done

Explicitly ruled out in advance, because each is individually defensible and
collectively an infinite regress:

- block bootstrap to "account for serial correlation better"
- alternative HAC lag selections chosen after seeing results
- re-defining any characteristic (e.g. a different skew estimator) post hoc
- adding universe tiers or changing quantile counts to find a specification
  where something survives
- dropping the 2020 COVID window unless decided on the pre-registered decay
  analysis, independently of what it does to significance

## Prediction (recorded before running)

I expect **0 to 1** anomalies to survive BH at q = 0.10 in the wide universe.
`neg_skew` (|t| = 2.63) and `high_52w` (|t| = 2.57) are the only plausible
survivors; both are near the threshold and the family is large enough that BH
will likely reject both. I expect the bootstrap max-|t| test to be
**non-significant**, i.e. the largest observed statistic is consistent with what
33 draws from a null produce.

If that prediction is right, the honest headline for §6b becomes: *"No documented
anomaly tested survives multiple-testing correction on Indian equities 2010–2020.
The apparent effects in the illiquid tier are consistent with sampling noise."*
