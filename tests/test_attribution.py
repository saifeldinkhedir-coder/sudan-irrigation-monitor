"""
Tests for the attribution & validation harness (Stage 3).

The claims being pinned down are the scientifically load-bearing ones:

  - when a head-to-tail gap is REAL (independent of soil/crop/planting date), the
    adjusted gap survives the controls and the placebo test rejects chance;
  - when the gap is ENTIRELY an artefact of soil, the adjusted gap collapses
    toward zero even though the raw gap is large - the whole point of the module;
  - green-up extraction distinguishes a time-shifted (late-planted) curve from a
    depressed (water-short) one;
  - a negative control whose gap excludes zero is flagged as pipeline bias.

These are generated from synthetic data with a KNOWN truth, so a regression that
silently breaks the confounder control turns a test red.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import attribution as at


def _fields_real_gap(n=60, seed=0):
    """A genuine water gradient: response falls with position, and soil/crop are
    assigned INDEPENDENTLY of position, so controlling for them must NOT remove
    the gap."""
    rng = np.random.default_rng(seed)
    recs = []
    for i in range(n):
        pos = rng.uniform(0, 1)
        soil = rng.choice(["clay", "loam", "sand"])
        crop = rng.choice(["sorghum", "wheat"])
        # response depends on position only (+ small noise, tiny soil effect)
        resp = 0.75 - 0.35 * pos + rng.normal(0, 0.02)
        recs.append({"position": pos, "response": resp,
                     "soil_class": soil, "crop": crop})
    return recs


def _fields_soil_artefact(n=60, seed=1):
    """A FAKE gap: response is driven entirely by soil, and soil happens to be
    arranged head->tail (sand at the tail). Raw position slope will look strong;
    after controlling for soil it must collapse."""
    rng = np.random.default_rng(seed)
    recs = []
    for i in range(n):
        pos = rng.uniform(0, 1)
        # soil is a function of position: tail is sandy (poorer)
        soil = "clay" if pos < 0.33 else ("loam" if pos < 0.66 else "sand")
        soil_effect = {"clay": 0.75, "loam": 0.6, "sand": 0.4}[soil]
        resp = soil_effect + rng.normal(0, 0.02)   # NO direct position term
        recs.append({"position": pos, "response": resp,
                     "soil_class": soil, "crop": "sorghum"})
    return recs


class TestAttribution:
    def test_real_gap_survives_controls(self):
        res = at.fit_attribution(_fields_real_gap(), n_permutations=500)
        assert res.status == "OK"
        assert res.raw_gap > 0.2
        # Gap should remain large after controlling for soil and crop.
        assert res.adjusted_gap > 0.2
        # CI excludes zero -> a real, confident gradient.
        assert res.adjusted_gap_ci95[0] > 0
        # Placebo rejects chance.
        assert res.placebo_p_value < 0.05
        # a permutation p-value can never be exactly 0 (floor is 1/(M+1))
        assert res.placebo_p_value >= 1.0 / (500 + 1)
        assert "SURVIVES" in res.interpretation

    def test_soil_artefact_collapses_after_control(self):
        res = at.fit_attribution(_fields_soil_artefact(), n_permutations=500)
        assert res.status == "OK"
        # The RAW gap looks real...
        assert res.raw_gap > 0.2
        # ...but collapses once soil is controlled for.
        assert abs(res.adjusted_gap) < 0.5 * abs(res.raw_gap)
        assert "soil_class" in res.controls_used
        assert "COLLAPSES" in res.interpretation

    def test_too_few_fields_is_insufficient(self):
        recs = [{"position": i / 5, "response": 0.5} for i in range(5)]
        res = at.fit_attribution(recs)
        assert res.status == "INSUFFICIENT DATA"

    def test_constant_control_is_not_claimed_as_used(self):
        # Every field is the same soil: soil cannot be a control (no variation),
        # and the result must not pretend it controlled for it.
        recs = _fields_real_gap()
        for r in recs:
            r["soil_class"] = "clay"
        res = at.fit_attribution(recs, controls=("soil_class",), n_permutations=300)
        assert "soil_class" not in res.controls_used


class TestGreenUp:
    def test_late_planting_gives_later_greenup_same_peak(self):
        # Two curves, same amplitude, one shifted 20 days later.
        days = list(range(0, 120, 10))
        early = [0.15, 0.2, 0.45, 0.7, 0.8, 0.82, 0.8, 0.7, 0.5, 0.3, 0.2, 0.15]
        late = [0.15, 0.15, 0.18, 0.25, 0.5, 0.72, 0.82, 0.8, 0.7, 0.5, 0.3, 0.2]
        gu_early = at.green_up_day(days, early)
        gu_late = at.green_up_day(days, late)
        assert gu_early is not None and gu_late is not None
        assert gu_late > gu_early            # the discriminator works

    def test_flat_series_has_no_greenup(self):
        days = list(range(0, 60, 10))
        flat = [0.3, 0.3, 0.3, 0.3, 0.3, 0.3]
        assert at.green_up_day(days, flat) is None

    def test_too_short_series_returns_none(self):
        assert at.green_up_day([0, 10], [0.2, 0.5]) is None


class TestPersistenceAndControls:
    def test_persistence_flags_structural_recurrence(self):
        seasons = [
            {"season": 2021, "adjusted_gap": 0.30, "ci95": [0.15, 0.45]},
            {"season": 2022, "adjusted_gap": 0.28, "ci95": [0.12, 0.44]},
            {"season": 2023, "adjusted_gap": 0.33, "ci95": [0.18, 0.48]},
        ]
        out = at.persistence(seasons)
        assert out["status"] == "OK"
        assert out["consistent_sign"] is True
        assert out["structural_evidence"] is True

    def test_persistence_one_off_is_not_structural(self):
        seasons = [
            {"season": 2021, "adjusted_gap": 0.30, "ci95": [0.15, 0.45]},
            {"season": 2022, "adjusted_gap": 0.02, "ci95": [-0.15, 0.19]},
            {"season": 2023, "adjusted_gap": -0.05, "ci95": [-0.22, 0.12]},
        ]
        out = at.persistence(seasons)
        assert out["structural_evidence"] is False

    def test_negative_control_flags_bias(self):
        # A control whose CI excludes zero means the pipeline manufactures gaps.
        biased = at.AttributionResult(status="OK", adjusted_gap=0.25,
                                      adjusted_gap_ci95=[0.10, 0.40])
        out = at.negative_control_ok(biased)
        assert out["pipeline_bias_suspected"] is True

    def test_negative_control_clean(self):
        clean = at.AttributionResult(status="OK", adjusted_gap=0.03,
                                     adjusted_gap_ci95=[-0.12, 0.18])
        out = at.negative_control_ok(clean)
        assert out["pipeline_bias_suspected"] is False
