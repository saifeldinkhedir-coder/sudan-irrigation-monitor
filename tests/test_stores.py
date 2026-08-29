"""
Integration tests for the SQLite-backed stores, which run without Earth Engine.

These pin down the two places where the calibration and ground layers make a
report/withhold decision end to end:

  - a CalibrationStore must refuse to predict an absolute nitrogen figure until
    it has enough points AND an acceptable RMSE, and must produce one once both
    hold (rule 4);
  - an ObservationStore must compute its reliability figure only from clear
    satellite/ground cases and never from UNCLEAR ones (rule 8).
"""

import os
import sys
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import nutrition_climate_ground as ncg


def _store(tmp_path, name):
    return os.path.join(tmp_path, name)


class TestCalibrationStore:
    def test_predict_refused_until_enough_points(self, tmp_path):
        cal = ncg.CalibrationStore(_store(tmp_path, "c.db"))
        # 20 clean points: still below the 30-point floor.
        for i in range(20):
            cire = 1.0 + i * 0.1
            cal.add_point("sorghum", 14.0, 33.0, {"CIre": cire},
                          leaf_n_pct=1.5 + 0.4 * cire)
        fit = cal.fit("sorghum", predictor="cire")
        assert fit["fitted"] is False
        assert "30" in fit["reason"]
        # With no fitted model, predict returns None (nothing to quote).
        assert cal.predict("sorghum", {"CIre": 2.0}) is None
        cal.close()

    def test_predict_quotes_with_error_once_calibrated(self, tmp_path):
        cal = ncg.CalibrationStore(_store(tmp_path, "c.db"))
        rng = random.Random(0)
        # 40 points on a real linear relationship with small noise -> low RMSE.
        for i in range(40):
            cire = 1.0 + i * 0.05
            leaf_n = 1.2 + 0.5 * cire + rng.gauss(0, 0.05)
            cal.add_point("sorghum", 14.0, 33.0, {"CIre": cire}, leaf_n_pct=leaf_n)
        fit = cal.fit("sorghum", predictor="cire")
        assert fit["fitted"] is True
        assert fit["n_points"] == 40
        assert fit["usable"] is True
        pred = cal.predict("sorghum", {"CIre": 2.0})
        assert pred["available"] is True
        # The number must never arrive without its error.
        assert "rmse_pct" in pred["confidence"]
        assert pred["confidence"]["n_points"] == 40
        cal.close()

    def test_high_rmse_model_refuses_to_quote(self, tmp_path):
        cal = ncg.CalibrationStore(_store(tmp_path, "c.db"))
        rng = random.Random(1)
        # 40 points but the relationship is buried in large noise -> high RMSE.
        for i in range(40):
            cire = 1.0 + i * 0.05
            leaf_n = 2.0 + rng.gauss(0, 1.2)          # essentially no signal
            cal.add_point("cotton", 14.0, 33.0, {"CIre": cire}, leaf_n_pct=leaf_n)
        fit = cal.fit("cotton", predictor="cire")
        assert fit["fitted"] is True
        pred = cal.predict("cotton", {"CIre": 2.0})
        # RMSE exceeds the limit, so no absolute figure is quoted.
        assert pred["available"] is False
        assert "RMSE" in pred["reason"] or "limit" in pred["reason"]
        cal.close()

    def test_index_only_point_is_rejected(self, tmp_path):
        cal = ncg.CalibrationStore(_store(tmp_path, "c.db"))
        try:
            cal.add_point("wheat", 14.0, 33.0, {"CIre": 2.0})   # no lab, no SPAD
            assert False, "an index-only calibration point must be rejected"
        except ValueError:
            pass
        cal.close()


class TestObservationStore:
    def test_agreement_rate_ignores_unclear(self, tmp_path):
        st = ncg.ObservationStore(_store(tmp_path, "o.db"))

        def obs(i, agreement):
            return ncg.GroundObservation(
                obs_id=f"o{i}", field_id="f1", observed_at="2024-11-01",
                lat=14.0, lon=33.0, photo_path="/x.jpg",
                canopy_condition="wilting", crop="sorghum",
                satellite_agreement=agreement)

        st.add(obs(1, "AGREE"))
        st.add(obs(2, "AGREE"))
        st.add(obs(3, "SATELLITE_WORSE"))
        st.add(obs(4, None))          # never scored -> excluded from the figure
        summ = st.agreement_summary("sorghum")
        assert summ["available"] is True
        assert summ["total"] == 3      # the None one is not counted
        assert summ["agreement_rate"] == round(2 / 3, 3)
        st.close()

    def test_no_scored_observations_is_not_available(self, tmp_path):
        st = ncg.ObservationStore(_store(tmp_path, "o.db"))
        summ = st.agreement_summary()
        assert summ["available"] is False
        st.close()

    def test_unclear_reported_but_excluded_from_rate(self, tmp_path):
        st = ncg.ObservationStore(_store(tmp_path, "o.db"))

        def obs(i, agreement):
            return ncg.GroundObservation(
                obs_id=f"o{i}", field_id="f1", observed_at="2024-11-01",
                lat=14.0, lon=33.0, photo_path="/x.jpg",
                canopy_condition="wilting", crop="sorghum",
                satellite_agreement=agreement)

        st.add(obs(1, "AGREE"))
        st.add(obs(2, "GROUND_WORSE"))
        st.add(obs(3, "UNCLEAR"))
        st.add(obs(4, "UNCLEAR"))
        summ = st.agreement_summary("sorghum")
        assert summ["available"] is True
        assert summ["total"] == 2                 # only the 2 CLEAR cases
        assert summ["unclear"] == 2               # reported separately
        assert summ["agreement_rate"] == 0.5      # 1 AGREE / 2 scored
        st.close()
