"""
Data-entry page: scouting, calibration, harvest, costs.

WHY THIS PAGE EXISTS AT ALL
---------------------------
Two of this platform's most useful outputs are deliberately locked: an absolute
leaf-nitrogen percentage, and a yield in tonnes per hectare. Neither is quoted
without local measurements, because the satellite sees canopy and the canopy-to-
nitrogen and canopy-to-yield relationships are crop-, variety-, management- and
season-specific.

Those gates are correct and they are also, on their own, a bad experience. A
farmer who collects twelve leaf samples and is still told "not available" cannot
tell whether they are nearly there or nowhere near, and a refusal that cannot
say what would lift it is indistinguishable from one that never lifts. The
second teaches people to stop trying.

So every form here sits under a progress line that says exactly how many
measurements remain, and what the obstacle is once the count is met. The gate
does not move. It just becomes legible.

WHAT THIS PAGE WILL NOT DO
--------------------------
It will not accept a remembered harvest, a sack count, or an estimate as a
calibration point. An unquantifiable error in the training data becomes an
unquantifiable error in every prediction made from it, and unlike a satellite
error it leaves no trace anyone can find later.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st

import view as D


OBS_DIR = "observations"
CANOPY = ["", "healthy", "patchy", "yellowing", "wilting"]
SOIL_SURFACE = ["", "dry", "moist", "waterlogged", "cracked", "crusted"]
OUTLET = ["", "open", "blocked", "damaged", "silted"]
CROPS = ["sorghum", "wheat", "cotton", "groundnut", "default"]


def _progress_line(prog: dict) -> None:
    """Render a calibration progress bar and the specific next step."""
    st.progress(prog.get("fraction", 0.0))
    if prog.get("unlocked"):
        st.success(prog["next_step"])
    else:
        st.info(prog["next_step"])


def render(report: dict, db_dir: str = ".") -> None:
    st.subheader("Record data")
    st.caption(
        "Everything entered here is REPORTED, not measured. A satellite value "
        "can be wrong in ways the data reveals; a typed value can be wrong in "
        "ways it cannot. The two are stored and labelled apart.")

    names = [f.get("name") for f in report.get("fields", [])] or ["(no fields)"]
    tabs = st.tabs(["Scouting", "Nitrogen calibration", "Harvest", "Costs"])

    with tabs[0]:
        _scouting(names, db_dir)
    with tabs[1]:
        _nitrogen(report, names, db_dir)
    with tabs[2]:
        _harvest(report, names, db_dir)
    with tabs[3]:
        _costs(names, db_dir)


# ------------------------------------------------------------------ scouting

def _scouting(names, db_dir) -> None:
    import nutrition_climate_ground as ncg

    st.markdown("**A photograph and what you saw**")
    st.caption(
        "This is the only layer that measures the platform's own reliability. "
        "Where the satellite and a person disagree, the disagreement is stored "
        "rather than resolved, and only clear cases are scored - a forced "
        "verdict would corrupt the one figure that describes how well the "
        "indicators actually work.")

    store = ncg.ObservationStore(os.path.join(db_dir, "observations.db"))
    try:
        summary = store.agreement_summary()
        if summary.get("available"):
            st.metric("Satellite agreed with the observer",
                      f"{round(100 * summary['agreement_rate'])}%",
                      help=f"{summary['total']} clear comparisons, "
                           f"{summary['unclear']} unclear and excluded")
        else:
            st.info(summary.get("reason", "no comparisons yet"))

        with st.form("scouting"):
            c1, c2 = st.columns(2)
            field_id = c1.selectbox("Field", names)
            crop = c2.selectbox("Crop", CROPS)
            lat = c1.number_input("Latitude", value=14.42, format="%.5f")
            lon = c2.number_input("Longitude", value=33.10, format="%.5f")
            photo = st.file_uploader("Photograph", type=["jpg", "jpeg", "png"])

            c3, c4 = st.columns(2)
            canopy = c3.selectbox("Canopy condition", CANOPY)
            stage = c4.text_input("Growth stage")
            soil = c3.selectbox("Soil surface", SOIL_SURFACE)
            outlet = c4.selectbox("Outlet condition", OUTLET)

            # WHICH problem, not merely whether there was one.
            #
            # A "disease signs" tick cannot lift the disease ladder past an
            # unnamed anomaly, because naming is precisely the thing the
            # satellite cannot do and a person can. The options come from the
            # selected crop's own registry: offering sorghum's anthracnose for
            # a wheat field invites somebody to record a disease that crop
            # does not get, and a wrong name in the ground-truth store is
            # worse than no name at all - it is the data everything else is
            # checked against.
            options = D.crop_problem_options(crop)
            if options:
                problem = st.selectbox(
                    "Problem found (this is what names a disease)",
                    [""] + [k for k, _l in options],
                    format_func=lambda k: dict(options).get(k, "— nothing —"),
                    help="Leave empty unless you SAW it. The satellite cannot "
                         "name a disease; this field is the only thing that "
                         "can.")
            else:
                problem = ""
                st.caption(f"No problems are registered for {crop}. Another "
                           "crop's are deliberately not offered.")

            c5, c6, c7 = st.columns(3)
            weeds = c5.checkbox("Weeds present")
            pests = c6.checkbox("Pest damage")
            disease = c7.checkbox("Disease signs")
            salinity = c5.checkbox("Salinity crust")
            water_reached = c6.checkbox("Water reached the field")
            weed_pct = c7.number_input("Weed cover %", 0.0, 100.0, 0.0)
            days_since = st.number_input("Days since irrigation", 0, 365, 0)
            observer = st.text_input("Observer")
            notes = st.text_area("Notes")

            if st.form_submit_button("Save observation"):
                path = ""
                if photo is not None:
                    os.makedirs(os.path.join(db_dir, OBS_DIR), exist_ok=True)
                    path = os.path.join(
                        db_dir, OBS_DIR,
                        f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{photo.name}")
                    with open(path, "wb") as fh:
                        fh.write(photo.getbuffer())

                import uuid
                obs = ncg.GroundObservation(
                    obs_id=uuid.uuid4().hex[:12], field_id=field_id,
                    observed_at=datetime.now(timezone.utc).isoformat(),
                    lat=lat, lon=lon, photo_path=path, observer=observer,
                    crop=crop, growth_stage=stage, canopy_condition=canopy,
                    weeds_present=weeds, weed_cover_pct=weed_pct,
                    pest_damage=pests, disease_signs=disease, problem=problem,
                    soil_surface=soil, salinity_signs=salinity,
                    water_reached_field=water_reached,
                    days_since_irrigation=int(days_since),
                    outlet_condition=outlet, notes=notes)
                store.add(obs)
                if problem:
                    st.info("This names a problem, so the next engine run will "
                            "report this field as REPORTED rather than as an "
                            "unnamed anomaly.")
                st.success(f"Saved observation {obs.obs_id}"
                           + ("" if path else
                              " — with no photograph. A record without an image "
                              "cannot be re-checked later by anyone else."))
    finally:
        store.close()


# ----------------------------------------------------------------- nitrogen

def _nitrogen(report, names, db_dir) -> None:
    import nutrition_climate_ground as ncg
    import decision_logic as dl

    st.markdown("**Leaf nitrogen or SPAD, paired with the satellite indices**")
    crop = st.selectbox("Crop", CROPS, key="n_crop")

    store = ncg.CalibrationStore(os.path.join(db_dir, "calibration.db"))
    try:
        rows = store.conn.execute(
            "SELECT COUNT(*) FROM calibration WHERE crop = ?", (crop,)).fetchone()
        model = store.conn.execute(
            "SELECT rmse, n_points FROM model WHERE crop = ?", (crop,)).fetchone()
        prog = dl.calibration_progress(
            n_points=rows[0] if rows else 0,
            min_points=ncg.MIN_CALIBRATION_POINTS,
            rmse=model[0] if model else None,
            max_rmse=ncg.MAX_ACCEPTABLE_RMSE_PCT,
            quantity="an absolute leaf-nitrogen percentage")
        _progress_line(prog)

        by_name = {f.get("name"): f for f in report.get("fields", [])}
        with st.form("nitrogen"):
            field_id = st.selectbox("Field the sample came from", names)
            c1, c2 = st.columns(2)
            lat = c1.number_input("Latitude", value=14.42, format="%.5f",
                                  key="n_lat")
            lon = c2.number_input("Longitude", value=33.10, format="%.5f",
                                  key="n_lon")
            method = st.radio("Measurement", ["Laboratory leaf N %", "SPAD"],
                              horizontal=True)
            value = st.number_input(
                "Value", 0.0, 100.0, 2.5, step=0.1,
                help="Leaf nitrogen as a percentage, or the SPAD reading")
            stage = st.text_input("Growth stage", key="n_stage")
            operator = st.text_input("Taken by")

            rec = by_name.get(field_id) or {}
            indices = ((rec.get("nutrition") or {}).get("chlorophyll_indices")
                       or {})
            if indices:
                st.caption(f"Satellite indices for this field this season: "
                           f"{', '.join(f'{k} {round(v, 3)}' for k, v in indices.items() if v is not None)}")
            else:
                st.warning(
                    "No satellite chlorophyll indices are available for this "
                    "field in the loaded report. A calibration point without "
                    "them pairs a laboratory value with nothing and calibrates "
                    "nothing.")

            if st.form_submit_button("Save calibration point"):
                if not indices:
                    st.error("Refused: no satellite indices to pair this "
                             "measurement with.")
                else:
                    try:
                        store.add_point(
                            crop=crop, lat=lat, lon=lon, indices=indices,
                            leaf_n_pct=value if method.startswith("Lab") else None,
                            spad=value if method == "SPAD" else None,
                            growth_stage=stage, operator=operator)
                        st.success("Saved. Re-open this tab to see the count move.")
                    except ValueError as e:
                        st.error(str(e))

        if prog.get("blocked_by") in ("UNFITTED", "ERROR") and st.button(
                "Fit the model now", key="fit_n"):
            st.write(store.fit(crop))
    finally:
        store.close()


# ------------------------------------------------------------------ harvest

def _harvest(report, names, db_dir) -> None:
    import agronomy as agro

    st.markdown("**A weighed harvest from a known area**")
    st.caption(
        "Not an estimate, not a sack count, not a recollection. An "
        "unquantifiable error in the training data becomes an unquantifiable "
        "error in every yield figure predicted from it, and unlike a satellite "
        "error it leaves no trace anyone can find later.")

    crop = st.selectbox("Crop", CROPS, key="y_crop")
    store = agro.YieldCalibrationStore(os.path.join(db_dir,
                                                    "yield_calibration.db"))
    try:
        _progress_line(store.progress(crop))

        by_name = {f.get("name"): f for f in report.get("fields", [])}
        with st.form("harvest"):
            field_id = st.selectbox("Field", names, key="y_field")
            c1, c2 = st.columns(2)
            kg = c1.number_input("Harvested weight (kg)", 0.0, 1e7, 0.0, step=50.0)
            area = c2.number_input("Area harvested (ha)", 0.0, 1e4, 0.0,
                                   step=0.1)
            season = st.number_input("Season start year", 2015, 2100, 2022)
            operator = st.text_input("Weighed by")
            notes = st.text_area("Notes", key="y_notes")

            rec = by_name.get(field_id) or {}
            vig = ((rec.get("crop_health") or {}).get("readings", {})
                   .get("vigour", {}))
            ndvi = vig.get("value") if vig.get("status") == "OK" else None
            if ndvi is not None:
                st.caption(f"Season canopy for this field: NDVI {ndvi}")
                if area:
                    st.caption(f"This point would be "
                               f"{(kg / 1000.0) / area:.2f} t/ha")
            else:
                st.warning("No usable canopy value for this field in the loaded "
                           "report, so this harvest cannot be paired with an "
                           "observation and would train nothing.")

            if st.form_submit_button("Save harvest point"):
                try:
                    store.add_point(crop=crop, harvested_kg=kg, area_ha=area,
                                    ndvi=ndvi, field_id=field_id,
                                    season=int(season), operator=operator,
                                    notes=notes)
                    st.success("Saved.")
                except ValueError as e:
                    st.error(f"Refused: {e}")

        if len(store.points(crop)) >= agro.MIN_YIELD_CALIBRATION_POINTS and \
                st.button("Fit the model now", key="fit_y"):
            st.write(store.fit(crop))
    finally:
        store.close()


# -------------------------------------------------------------------- costs

def _costs(names, db_dir) -> None:
    import farm_records as fr

    st.markdown("**Operations and sales**")
    st.caption("Bookkeeping, kept apart from the satellite figures. A margin "
               "and an NDVI are not the same kind of fact and are never "
               "combined without being labelled MIXED.")

    store = fr.RecordStore(os.path.join(db_dir, "farm_records.db"))
    try:
        field_id = st.selectbox("Field", names, key="c_field")
        c1, c2 = st.columns(2)

        with c1.form("operation"):
            st.markdown("Add an operation")
            op = st.selectbox("Operation", fr.OPERATION_TYPES)
            date = st.date_input("Date")
            cost = st.number_input("Cost", 0.0, 1e9, 0.0, step=100.0)
            currency = st.text_input("Currency", "SDG")
            qty = st.number_input("Quantity", 0.0, 1e6, 0.0)
            unit = st.text_input("Unit")
            if st.form_submit_button("Save operation"):
                store.add_operation(fr.Operation(
                    field_id=field_id, date=str(date), operation=op, cost=cost,
                    currency=currency, quantity=qty or None, unit=unit or None))
                st.success("Saved.")

        with c2.form("sale"):
            st.markdown("Add a sale")
            s_date = st.date_input("Date", key="s_date")
            s_qty = st.number_input("Quantity sold", 0.0, 1e7, 0.0)
            s_unit = st.text_input("Unit", "tonne")
            revenue = st.number_input("Revenue", 0.0, 1e9, 0.0, step=100.0)
            s_cur = st.text_input("Currency", "SDG", key="s_cur")
            if st.form_submit_button("Save sale"):
                store.add_sale(fr.Sale(
                    field_id=field_id, date=str(s_date), quantity=s_qty,
                    unit=s_unit, revenue=revenue, currency=s_cur))
                st.success("Saved.")

        st.markdown("**This field so far**")
        breakdown = store.cost_breakdown(field_id)
        if breakdown["status"] == "OK":
            st.write(f"Total cost: {breakdown['total_cost']} "
                     f"{breakdown['currency']} over "
                     f"{breakdown['n_operations']} operations")
            st.bar_chart(breakdown["by_operation"])
        else:
            st.info(breakdown["reason"])

        margin = store.gross_margin(field_id)
        if margin["status"] == "OK":
            st.metric("Gross margin",
                      f"{margin['gross_margin']} {margin['currency']}")
            st.caption(margin["caveat"])
        else:
            st.info(margin["reason"])
    finally:
        store.close()
