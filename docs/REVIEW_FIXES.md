# Post-build review fixes

An independent adversarial code review was run over the engine after the Stage
2-4 build. It found three real correctness bugs and one documentation mismatch.
All are fixed, each with a test. Recorded here so the changes are traceable.

## 1. HIGH — field stress could never fire (silent wrong result)

`engine.field_condition` derived the stress threshold (`median − 2·robust_sigma`)
over the **same field** whose **mean** it then compared. Since a field's mean is
never below its own low tail, `stressed` was essentially always False and the
whole integrity-rule-3 reading (`STRESS WITH LITTLE RAIN` vs `STRESS DESPITE
RAIN`, i.e. drought-vs-network separation) was dead code.

**Fix:** the stress threshold is now derived from a **reference** geometry (the
command area / neighbourhood) passed as `reference_geom`; the field mean is
compared against that. With no reference supplied, the vigour/moisture VALUES are
still reported but no threshold and no stress verdict are (an honest
`NOT AVAILABLE`, not a threshold that can never fire). The decision itself moved
into the pure, tested `decision_logic.stress_reading`.

*Note:* `field_condition` was defined but not yet called by `analyse`, so no
already-produced number was wrong — but it is a delivered part of the field layer
and would have been wrong the moment it was used.

*Tests:* `test_decision_logic.TestStressReading`,
`test_engine_assembly.test_field_condition_withholds_stress_without_a_reference`,
`…offers_verdict_with_a_reference`.

## 2. LOW–MODERATE — placebo p-value could be exactly 0

`attribution.fit_attribution` computed the permutation p-value as
`mean(perm ≥ obs)`, which can return `0.0` — an impossible p-value.

**Fix:** the standard `(1 + #{perm ≥ obs}) / (1 + M)`, so the floor is `1/(M+1)`
and it is never exactly zero. *Test:* `test_attribution` asserts
`placebo_p_value >= 1/(M+1)`.

## 3. LOW — calibration RMSE used /n, understating the error at the quote-gate

`CalibrationStore.fit` computed `rmse = sqrt(ss_res / n)`. For a 2-parameter OLS
the unbiased residual standard error uses `n − 2`; dividing by `n` understates
RMSE by ~√(n/(n−2)) and could let a borderline model slip past the
`MAX_ACCEPTABLE_RMSE_PCT` gate it was meant to fail.

**Fix:** `rmse = sqrt(ss_res / (n − 2))` (n is always ≥ 30 here, so safe). The
existing calibration gate tests still pass.

## 4. Documentation — Otsu docstring/return mismatch

`decision_logic.otsu_threshold`'s Returns block described `is_bimodal` as
`separability ≥ 0.5`, while the code (correctly) sets it from the valley-depth
`bimodality`. Behaviour was always right; the docstring is now corrected.

## Also noted, not a bug

- The adjusted-gap CI in `fit_attribution` treats the fitted head value as fixed;
  the true interval is marginally wider. Documented in-code as an approximation,
  adequate for the survive/collapse decision.
- The mock Earth Engine backend always carries the full default band list, so a
  future `select()`/`rename()` band-name mismatch would still resolve in the mock
  and pass the assembly test while failing on real EE. No such mismatch exists
  today; flagged so it is not mistaken for full coverage.
