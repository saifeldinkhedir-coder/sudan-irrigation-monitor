"""
GeoLibre <-> analysis-engine bridge.

Two directions of flow, which is the whole design:

  IN   A field-collection form submitted in GeoLibre becomes a GroundObservation,
       is scored against the satellite record for the same field and date
       (AGREE / SATELLITE_WORSE / GROUND_WORSE / UNCLEAR - only clear cases
       scored), and is written to the ObservationStore. Over a season this
       accumulates the platform's own reliability figure.

  OUT  A farmer card (one sentence, per reach) is served from the engine's
       results for display or messaging.

The satellite lookup is INJECTED (a callable), so this bridge is fully testable
without Earth Engine: tests pass a stub provider. In production the provider is
an EE-backed function that returns the field's NDVI/CIre for the observation
date.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Callable, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import nutrition_climate_ground as ncg
import farmer_channel as fc


REQUIRED_FIELDS = ("field_id", "canopy_condition", "water_reached_field",
                   "lat", "lon", "observed_at", "photo_path")


def validate_submission(form_data: dict) -> dict:
    """Check the required fields are present before anything touches the store.
    Returns {"ok": bool, "missing": [...]}."""
    missing = [f for f in REQUIRED_FIELDS
               if form_data.get(f) in (None, "")]
    return {"ok": not missing, "missing": missing}


def submit_observation(form_data: dict, store: "ncg.ObservationStore",
                       satellite_provider: Optional[Callable] = None,
                       scheme_p25: Optional[float] = None,
                       obs_id: Optional[str] = None) -> dict:
    """
    Turn a submitted form into a scored, stored GroundObservation.

    satellite_provider(lat, lon, observed_at) -> dict|None with at least "NDVI"
    (and optionally "CIre"). When it returns None, or scheme_p25 is unknown, the
    agreement is UNCLEAR - never a guess (integrity rule 8).
    """
    v = validate_submission(form_data)
    if not v["ok"]:
        return {"ok": False, "error": "missing required fields",
                "missing": v["missing"]}

    oid = obs_id or f"{form_data['field_id']}:{form_data['observed_at']}"
    obs = ncg.GroundObservation(
        obs_id=oid,
        field_id=form_data["field_id"],
        observed_at=form_data["observed_at"],
        lat=float(form_data["lat"]), lon=float(form_data["lon"]),
        photo_path=form_data["photo_path"],
        source=form_data.get("source", "phone"),
        observer=form_data.get("observer", ""),
        crop=form_data.get("crop", ""),
        growth_stage=form_data.get("growth_stage", ""),
        canopy_condition=form_data.get("canopy_condition", ""),
        weeds_present=form_data.get("weeds_present"),
        weed_cover_pct=form_data.get("weed_cover_pct"),
        pest_damage=form_data.get("pest_damage"),
        disease_signs=form_data.get("disease_signs"),
        soil_surface=form_data.get("soil_surface", ""),
        salinity_signs=form_data.get("salinity_signs"),
        water_reached_field=form_data.get("water_reached_field"),
        days_since_irrigation=form_data.get("days_since_irrigation"),
        outlet_condition=form_data.get("outlet_condition", ""),
        notes=form_data.get("notes", ""))

    satellite = None
    if satellite_provider is not None:
        try:
            satellite = satellite_provider(obs.lat, obs.lon, obs.observed_at)
        except Exception:
            satellite = None

    # Score agreement only when both sides say something clear.
    obs.satellite_agreement = ncg.compare_with_satellite(
        obs, satellite or {}, scheme_p25 if scheme_p25 is not None else 0.0) \
        if (satellite and scheme_p25 is not None) else "UNCLEAR"

    store.add(obs, satellite)
    return {"ok": True, "obs_id": oid, "agreement": obs.satellite_agreement}


def farmer_card_for(results_path: str, canal_name: str,
                    reach_position: Optional[float] = None,
                    lang: str = "ar") -> dict:
    """Serve a farmer card for a canal (and optional reach) from a results JSON.
    This is the OUT direction - the same engine number phrased for the farmer."""
    with open(results_path, encoding="utf-8") as fh:
        results = json.load(fh)
    for c in results.get("canals", []):
        if c.get("name") == canal_name:
            return fc.farmer_card(c, reach_position=reach_position, lang=lang)
    return {"text": "", "clauses": [], "attributes_cause": False,
            "error": f"canal {canal_name} not found in results"}
