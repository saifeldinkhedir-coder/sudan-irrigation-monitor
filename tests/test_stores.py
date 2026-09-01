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


class TestTheDiseaseLoopClosesEndToEnd:
    """
    Rung 3 is the only rung that names a disease, so it has to actually reach
    the engine. The store is where the app writes and the engine reads; a break
    anywhere in that chain leaves the disease layer permanently stuck at an
    unnamed anomaly while appearing to work.
    """
    def _store(self, tmp_path):
        import nutrition_climate_ground as ncg
        return ncg.ObservationStore(str(tmp_path / "obs.db"))

    def _obs(self, **kw):
        import nutrition_climate_ground as ncg
        base = dict(obs_id="o1", field_id="Field A",
                    observed_at="2022-09-15T08:00:00+00:00",
                    lat=14.42, lon=33.10, photo_path="", observer="Ali",
                    crop="sorghum")
        base.update(kw)
        return ncg.GroundObservation(**base)

    def test_a_named_finding_survives_the_round_trip(self, tmp_path):
        store = self._store(tmp_path)
        try:
            store.add(self._obs(problem="sorghum_anthracnose"))
            rows = store.scouting_for("Field A")
            assert rows == [{"problem": "sorghum_anthracnose",
                             "observed_at": "2022-09-15T08:00:00+00:00",
                             "observer": "Ali"}]
        finally:
            store.close()

    def test_a_ticked_checkbox_without_a_name_is_not_a_diagnosis(self, tmp_path):
        """"I walked the field and ticked disease signs" is not "I found
        anthracnose". The rung that names a disease must not be lifted by a
        checkbox."""
        store = self._store(tmp_path)
        try:
            store.add(self._obs(obs_id="o2", disease_signs=True, problem=""))
            assert store.scouting_for("Field A") == []
        finally:
            store.close()

    def test_findings_come_back_oldest_first_so_the_latest_wins(self, tmp_path):
        store = self._store(tmp_path)
        try:
            store.add(self._obs(obs_id="a", problem="striga",
                                observed_at="2022-08-01"))
            store.add(self._obs(obs_id="b", problem="sorghum_anthracnose",
                                observed_at="2022-10-01"))
            rows = store.scouting_for("Field A")
            assert [r["problem"] for r in rows] == ["striga",
                                                    "sorghum_anthracnose"]
        finally:
            store.close()

    def test_the_store_feeds_the_ladder(self, tmp_path):
        """The join that matters: what the app writes is the shape the ladder
        reads."""
        import disease as dz
        store = self._store(tmp_path)
        try:
            store.add(self._obs(problem="sorghum_anthracnose"))
            out = dz.diagnose(scouting=store.scouting_for("Field A"))
            assert out["claim_level"] == "REPORTED"
            assert out["problem"] == "sorghum_anthracnose"
            assert out["observer"] == "Ali"
        finally:
            store.close()

    def test_another_field_s_findings_do_not_leak(self, tmp_path):
        store = self._store(tmp_path)
        try:
            store.add(self._obs(problem="striga"))
            assert store.scouting_for("Field B") == []
        finally:
            store.close()


class TestTheAccuracyFigureCanActuallyAccumulate:
    """
    The agreement rate is the only number in this platform that MEASURES its
    own accuracy rather than claiming it. Observations were saved with no
    satellite side, so `satellite_agreement` was NULL on every row ever
    written: the figure could never leave zero, and the screen said "no clear
    comparisons yet" for ever without anything indicating a defect.
    """
    def _report(self, vigours):
        import nutrition_climate_ground as ncg  # noqa: F401
        return {"fields": [
            {"name": f"F{i}", "crop_health": {"readings": {"vigour": {
                "status": "OK" if v is not None else "NO DATA", "value": v}}}}
            for i, v in enumerate(vigours)]}

    def _obs(self, field_id, canopy):
        import nutrition_climate_ground as ncg
        return ncg.GroundObservation(
            obs_id="o", field_id=field_id, observed_at="2022-09-15",
            lat=14.4, lon=33.1, photo_path="", canopy_condition=canopy)

    def test_a_poor_field_seen_poor_scores_as_agreement(self):
        import nutrition_climate_ground as ncg
        r = self._report([0.10, 0.40, 0.50, 0.60, 0.70])
        out = ncg.score_observation(self._obs("F0", "wilting"), r)
        assert out["verdict"] == "AGREE"

    def test_a_healthy_field_seen_poor_scores_as_ground_worse(self):
        import nutrition_climate_ground as ncg
        r = self._report([0.10, 0.40, 0.50, 0.60, 0.70])
        out = ncg.score_observation(self._obs("F4", "wilting"), r)
        assert out["verdict"] == "GROUND_WORSE"

    def test_a_field_the_satellite_could_not_see_is_unclear_with_a_reason(self):
        """UNCLEAR must be explainable, not merely counted."""
        import nutrition_climate_ground as ncg
        r = self._report([None, 0.40, 0.50, 0.60, 0.70])
        out = ncg.score_observation(self._obs("F0", "healthy"), r)
        assert out["verdict"] == "UNCLEAR"
        assert "no usable vigour reading" in out["reason"]

    def test_too_few_fields_for_a_quartile_is_unclear_not_a_guess(self):
        import nutrition_climate_ground as ncg
        r = self._report([0.2, 0.5])
        out = ncg.score_observation(self._obs("F0", "wilting"), r)
        assert out["verdict"] == "UNCLEAR"
        assert "lower quartile" in out["reason"]

    def test_the_quartile_needs_four_measured_fields(self):
        import nutrition_climate_ground as ncg
        assert ncg.reference_p25(self._report([0.1, 0.2, 0.3])) is None
        assert ncg.reference_p25(self._report([0.1, 0.2, 0.3, 0.4])) == 0.2

    def test_a_scored_observation_reaches_the_summary(self, tmp_path):
        """The join that matters: what the form writes is what the rate reads."""
        import nutrition_climate_ground as ncg
        store = ncg.ObservationStore(str(tmp_path / "o.db"))
        try:
            r = self._report([0.10, 0.40, 0.50, 0.60, 0.70])
            for i, (fid, canopy) in enumerate((("F0", "wilting"),
                                               ("F4", "healthy"),
                                               ("F3", "healthy"))):
                obs = self._obs(fid, canopy)
                obs.obs_id = f"o{i}"
                scored = ncg.score_observation(obs, r)
                obs.satellite_agreement = scored["verdict"]
                store.add(obs, satellite=scored["satellite"])
            summary = store.agreement_summary()
            assert summary["available"] is True
            assert summary["total"] == 3
            assert summary["agreement_rate"] == 1.0
        finally:
            store.close()

    def test_unclear_cases_stay_out_of_the_rate(self, tmp_path):
        """A forced verdict would corrupt the one number that describes the
        platform's own accuracy."""
        import nutrition_climate_ground as ncg
        store = ncg.ObservationStore(str(tmp_path / "o2.db"))
        try:
            r = self._report([0.10, 0.40, 0.50, 0.60, 0.70])
            good = self._obs("F0", "wilting")
            good.satellite_agreement = ncg.score_observation(good, r)["verdict"]
            store.add(good)
            bad = self._obs("F0", "something else entirely")
            bad.obs_id = "o9"
            bad.satellite_agreement = ncg.score_observation(bad, r)["verdict"]
            store.add(bad)
            summary = store.agreement_summary()
            assert summary["total"] == 1
            assert summary["unclear"] == 1
        finally:
            store.close()
