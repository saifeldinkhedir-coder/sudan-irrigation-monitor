# GeoLibre plugin — Sudan Irrigation & Agriculture Monitor

This is the interface layer. The original work is the analysis engine in `../src`;
GeoLibre (`opengeos/GeoLibre`) supplies the desktop / web / Android / iOS shell,
offline area download, the Field Collection tool, and the Earth Engine panel.

## What is here

- `plugin.json` — the plugin manifest: three map layers (canals, head-to-tail
  equity styled by the flag, and the persistent-water raster for tracing),
  wired to the field-collection form and the farmer-card endpoint.
- `forms/ground_observation.json` — the field-collection form, bilingual
  (Arabic / English). Its field names mirror `GroundObservation` exactly, and
  `canopy_condition` + `water_reached_field` are required because they carry the
  reliability signal and the direct water-delivery signal.
- `bridge.py` — the two-way data flow, and the only part with logic:
  - **IN**: a submitted form → a `GroundObservation` → scored against the
    satellite record (`AGREE` / `SATELLITE_WORSE` / `GROUND_WORSE` / `UNCLEAR`,
    only clear cases scored) → written to the `ObservationStore`.
  - **OUT**: a farmer card (one sentence, per reach) served from the engine's
    results.

`bridge.py` is tested (`../tests/test_geolibre_bridge.py`) with a stub satellite
provider, so the two-way flow is verified without Earth Engine.

## Wiring it into GeoLibre

1. Produce the engine outputs: run the engine to write `results/equity.geojson`
   (canal geometry annotated with `head_tail_gap`, `head_tail_gap_ci95`,
   `flagged`, `gap_reliable`) and, from `../geometry/build_water_frequency.py`,
   `results/water_frequency.tif`.
2. Point GeoLibre's plugin loader at this directory. The map layers and the
   field-collection form load from the manifest.
3. Provide `bridge.submit_observation` with an EE-backed `satellite_provider`
   (lat, lon, date → the field's NDVI/CIre for that date) and the scheme's
   `cire`/NDVI `p25`. Everything else is already wired.

## What is deliberately NOT here

No re-implementation of the map, the offline download, or the collection UI —
those are GeoLibre's. This plugin is the manifest, the form schema, and the thin
bridge that connects GeoLibre to the analysis engine and enforces the integrity
rules at the boundary.
