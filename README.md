# Sudan Irrigation & Agriculture Monitor

A multi-sensor Google Earth Engine analysis engine for irrigated agriculture in
Sudan, answering two questions usually asked separately:

- **FIELD** — how is this field doing, and is it short of water?
- **NETWORK** — did the water actually arrive, and was it shared fairly from
  source to farm?

The network layer — and specifically the **head-to-tail equity** figure — is the
uncommon part, and it is where a gravity-fed scheme's real problem lives. See
[`docs/STRATEGY.md`](docs/STRATEGY.md) for the build plan, the validation design,
the feasibility split, and the stage-by-stage status table (§8).

## Layout

```
src/
  decision_logic.py            pure, ee-free decisions (thresholds, Otsu, the
                               head-tail slope fit + CI, the nitrogen gate,
                               agreement). The tested heart of the integrity rules.
  engine.py                    network + field layers, EE plumbing, and the
                               assembly (analyse) that wires everything together.
  nutrition_climate_ground.py  red-edge nutrition ladder, calibration store,
                               climate (server-side dry spells / season history),
                               ground-observation store, drone roadmap.
  attribution.py               Stage 3: stratified control for soil/crop/planting
                               date, adjusted gap + CI, placebo test, green-up,
                               persistence, negative controls.
  agri_engine.py               THE AGRICULTURE ENGINE - fields in, farm report
                               out. Imports nothing from engine.py.
  farm_cli.py                  agriculture entry point (--fields only).
  network.py                   continuity (where the water stopped), siltation
                               candidates, water-use efficiency + its refusal,
                               radar resolvability qualifier.
  agronomy.py                  FAO-56 ET0, Kcb from NDVI, crop water requirement,
                               irrigation requirement, GFS outlook, yield gate.
  rangeland.py                 rangeland productivity + timing, water points,
                               corridors — with the neutrality guard enforced.
  farm_records.py              REPORTED-side store (operations, costs, sales,
                               margins) and the rule-based advisory.
  farmer_channel.py            Stage 4 floor: one-sentence Arabic/English card.
  mock_ee.py                   deterministic offline Earth Engine mock, so the
                               whole pipeline runs with no network / auth / quota.
  cli.py                       command-line entry point.
dashboard/
  app.py, data.py              scheme-MANAGER view: canals, equity, continuity.
farmer_app/
  app.py, view.py              FARMER view: map of fields, every measured
                               variable, nutrition ladder, advisory.
geolibre_plugin/
  plugin.json, forms/, bridge.py   Stage 4: manifest, field form, two-way bridge.
geometry/
  build_water_frequency.py     build a persistent-water raster to trace canals.
  canal_geometry.py            fetch canals from OSM; validate ANY canal GeoJSON
                               against what the engine requires, before a run.
tests/                         368 tests; run with no Earth Engine.
docs/STRATEGY.md               the thinking; docs/dashboard_screenshot.png; sample.
```

## The layers

| Layer | What it answers | State |
|---|---|---|
| **Network** | did water arrive, was it shared fairly | built |
| **Continuity** | *where* the water stopped, reach by reach | built |
| **Siltation** | reaches holding less water than in past seasons | built, candidates only |
| **Efficiency** | ET consumed ÷ water released | consumption built, ratio refused |
| **Field** | how is this field, is it short of water | built |
| **Water requirement** | how much did the crop need (≠ receive) | built |
| **Nutrition ladder** | relative → strip sufficiency → calibrated N | built, gated |
| **Climate** | GDD, heat stress, dry spells, vs 10-year history | built |
| **Ground observation** | what a person saw, and whether the satellite agreed | built |
| **Rangeland** | productivity, timing, water points, corridors | built, guarded |
| **Farm records** | operations, costs, margins — all REPORTED | built |
| **Advisory** | rule-based, one sentence per computed indicator | built |
| **Forecast** | 7-day scheme-scale outlook (GFS, ~28 km) | built |
| **Yield** | refused without ≥30 local harvest points | gated |
| **Drone** | verification and training data, not wide-area | roadmap only |

### Two kinds of fact, kept apart

Everything from a satellite is **MEASURED**; everything a person types into the
records store is **REPORTED**. A measured value fails in ways the data can
reveal; a reported one fails in ways it cannot. Every record carries its
`provenance_kind`, and a figure combining both (margin per m³ of water) is
labelled `MIXED` and says which half came from where.

### Two things deliberately not built

An **AI chat assistant** would produce fluent, confident answers with no
provenance, no `NOT AVAILABLE`, and no way to enforce any of the eight rules the
rest of this code exists to enforce. The advisory is rule-based instead: every
sentence traces to an indicator the engine computed, and a missing indicator
produces silence, listed in `withheld`.

A **community forum** is a server and a moderation policy, not an analysis
feature — and in a context where farmer–herder tension is live, an unmoderated
one is a hazard rather than a missing nicety.

## The integrity rules, enforced in code

1. An indicator that cannot be computed is `NOT AVAILABLE` with a reason — never
   silently zero.
2. Thresholds are derived per command area per run (`median ± k·robust_sigma`),
   not fixed in advance. (Area classification uses Otsu, the right tool for a
   bimodal cropped/bare mixture, with a valley-depth flag when the split is weak.)
3. Stress is never reported without CHIRPS rainfall context.
4. No absolute nitrogen figure without a calibrated model whose RMSE is within
   limit — and the RMSE is always quoted alongside the number.
5. Equity figures describe measured differences and attribute nothing to any
   office, operator or decision. The attribution harness quantifies how much of a
   gap survives controlling for soil, crop and planting date — and still stops at
   "consistent with", never "caused by".
6. Every arbitrary constant is declared `ARBITRARY` in the code and the output.
7. Every number carries machine-readable provenance (sensor, dates, scenes,
   threshold basis, observed fraction).
8. Where satellite and ground disagree, it is recorded, not resolved; only clear
   cases are scored, the rest are `UNCLEAR` and are excluded from the reliability
   figure.

## Run

```bash
pip install -r requirements.txt        # earthengine-api, numpy, streamlit, pytest

# tests need NO Earth Engine and no auth:
pytest -q                              # 368 tests

# run the FULL pipeline offline against the mock backend (no auth, no quota):
python - <<'PY'
import sys, mock_ee; sys.modules['ee']=mock_ee
sys.path.insert(0,'src')
import importlib, nutrition_climate_ground, engine
importlib.reload(nutrition_climate_ground); importlib.reload(engine)
# ... build canal_fc / command_fc and call engine.analyse(...)
PY

# the manager dashboard against the sample results:
streamlit run dashboard/app.py -- --results docs/sample_results.json

# a live run (needs real canal geometry — see docs/STRATEGY.md Stage 1):
export EE_PROJECT=your-ee-project
python src/cli.py --canal canals.geojson --command-areas commands.geojson \
    --fields fields.geojson --rangeland range.geojson \
    --season 2022 --crop sorghum --out results.json
```

`--fields` turns on the **field layer** (per-field vigour, canopy moisture,
thermal stress, rainfall context, red-edge nutrition). It is off without
polygons, because there is no honest way to invent a field boundary from a canal
line. It also leans on `--command-areas`: a field's stress threshold is derived
from the surrounding population, and a reference that is not at least 10× the
field's area is refused — the field would otherwise set its own threshold and
could never be flagged. When no adequate reference exists the values are still
reported and the **verdict** is withheld, which is not the same thing as
"healthy" and is never displayed as one.

`--rangeland` turns on the **rangeland layer**. It carries a conflict-sensitivity
note on every result and **refuses** any area whose name contains claim language
(`belongs to`, `grazing rights`, `tribal land`, `تعدّي`, `حق الرعي`, …). That
refusal is enforced in code, not promised in documentation: rangeland and
corridor maps are the artefacts most likely to be carried into a dispute as
evidence, and the measurement itself — how green a strip of land was in
October — contains no claim at all until someone attaches one.

### Check your geometry before you run

Most real canal geometry in Sudan will be hand-digitised over sub-metre imagery,
because a Gezira minor canal is 5–15 m wide and a 10 m water-frequency raster
cannot see it. Hand digitising is the right method, and it produces exactly the
defects that later become indefensible numbers. Catch them at ingest:

```bash
python geometry/canal_geometry.py validate --canal canals.geojson --command-areas commands.geojson
```

It refuses: no direction property, fewer than 4 vertices (no reaches to fit),
`MultiLineString` (no unambiguous head-to-tail order), duplicate names (command
areas silently merge and the equity figure belongs to neither canal), projected
coordinates, and zero-length lines. It warns about: missing `width_m` (without
it no radar figure can be qualified as resolvable), repeated vertices,
implausible lengths, and canals with no matching command polygon.

`fetch` pulls `waterway=canal|ditch|drain` from OpenStreetMap — useful for main
and major canals, which are often mapped. Minor canals usually are not. Fetched
canals are written **without** a direction property on purpose, so the validator
refuses them until a person supplies it: an inherited arbitrary direction is
worse than an absent one, because it looks like information.

### Standing water is not flow

Every network figure rests on C-band backscatter, which says a surface is smooth
and wet. It does not say the water is moving, moving the intended way, or usable
downstream — a canal full and static reads identically to one carrying its
design discharge. The word "flow" appears in no output string in `network.py`.

**Continuity** is the figure that earns its place: a canal wet at reaches 1–3
and dry at 4–8, and a canal half-wet along its whole length, have the same
seasonal average and are completely different problems. Only one of them names a
place someone can go and look at. An **unobserved** reach is never counted as
dry — it interrupts a dry run rather than extending it, because calling an
unseen reach dry would manufacture the exact finding the layer exists to report.

**Efficiency is refused by default.** The numerator (ET over the command) is a
satellite measurement; the denominator (volume released through the offtake) is
the scheme authority's gauge reading. Studies routinely substitute a design
discharge or an allocation for the measured release, which turns a measurement
into an assumption while keeping the word "efficiency" on it. This engine
reports the consumption it can measure and withholds the ratio.

### Canal direction is an input, not an assumption

The head-to-tail gap is **signed**, and nothing in a LineString records which end
the water enters. Reverse the vertex order of the same canal and the same
imagery yields the same magnitude with the opposite sign — pointing a manager at
the wrong end of the network with a confidence interval behind the error. So
direction comes from a `vertex_order` property or an `offtake` coordinate; with
neither, vertex order is used and marked `verified: false`; when the two
disagree, the gap is **withheld** rather than resolved by guesswork. Elevation is
deliberately not used: Gezira's fall is centimetres per kilometre, inside SRTM's
vertical noise, so a DEM would dress an assumption up as a measurement.

## Status — honest

**Logic tested, plumbing tested, measurements unvalidated.** All 368 tests pass
with no Earth Engine. The mock backend runs the whole `analyse()` pipeline
offline, so the wiring is verified — but the mock returns synthetic values, so it
proves the pipeline is *wired* correctly, never that the *measurements* are
correct. The measurements become real only when you run the engine against real
New Halfa geometry with your `EE_PROJECT` (Stage 1 → 2). `sample_canals.geojson`
and `docs/sample_results.json` are labelled illustrative/demo, not measurements.

## Licence and citation

Copyright 2026 Seifeldin Alkedir. Licensed under the
[Apache License 2.0](LICENSE).

You may use, modify and redistribute this work, including commercially, provided
you keep the copyright and attribution notices, state any changes you made, and
carry forward the [`NOTICE`](NOTICE) file. The licence includes an explicit
patent grant. It does not grant permission to use the author's name to endorse
or promote derived work.

The third-party datasets this software reads — Copernicus Sentinel-1/2, Landsat
8/9, CHIRPS, ERA5-Land, MODIS, GRACE-FO, JRC Global Surface Water, NOAA GFS —
each carry their own terms and citation requirements, which this licence neither
grants nor alters.

If you publish figures produced by this software, please carry forward the
limitations recorded in `NOTICE` and in every result's own `limitations` block.
They are the conditions under which the numbers mean anything.

## Two products, one measurement core

The repository holds **two engines with different input contracts**, because they
answer different questions for different people and forcing them into one entry
point would make both worse.

| | Irrigation | Agriculture |
|---|---|---|
| Entry point | `src/cli.py` | `src/farm_cli.py` |
| Engine | `engine.py` + `network.py` | `agri_engine.py` |
| Needs | canal centrelines + command areas | **field polygons only** |
| Interface | `dashboard/` — scheme manager | `farmer_app/` — the farmer |
| Question | did water arrive, was it shared fairly | how is my crop, what do I do this week |

`agri_engine` imports nothing from `engine`, and a test parses the AST to keep it
that way. A farmer has fields and no canal geometry, may not be in a gravity
scheme at all, and should never be asked for a command-area polygon to find out
how their crop is doing.

```bash
export EE_PROJECT=your-ee-project
python src/farm_cli.py --fields my_fields.geojson --season 2022 --crop sorghum --out farm_report.json
streamlit run farmer_app/app.py --server.address 127.0.0.1 -- --report farm_report.json --fields my_fields.geojson
```

### The map has three colours, not two

A map is more persuasive than a table, and nobody reads a colour sceptically. A
field drawn confident green because nothing could be measured on it would be a
worse lie than a blank cell. So **grey — not measured — is never collapsed into
green or red**, and the legend says so in words: *"this is NOT a healthy field,
it is an unseen one."*

The same four-state classification drives the map, the ranked list and the header
count. An earlier version marked the list from a two-state flag while the map
used four, and a live run showed one field amber on the map, green in the list,
and "need attention: 0" in the header — three answers to one question on one
screen.

### What the farmer report contains

Vigour, canopy moisture and greenness with thresholds derived from the field's
own 3 km neighbourhood; surface temperature against the surrounding land;
rainfall for the season and the last fortnight; reference ET0 and crop water
**needed**; growing degree days, heat-stress days, longest dry spell, and this
season against the site's own ten-year history; soil texture; the nutrition
ladder; a gated yield line; and a rule-based advisory in Arabic and English.

Every row names its sensor **and the scale it was measured at**, because a 100 m
thermal reading and a 10 m vigour reading are not the same kind of statement
about a small field, and the farmer should be able to see which is which without
reading documentation.

Fields are ranked by which needs attention first. That is an **ordering, not a
score**: no calibrated health scale exists, and one is not invented. Fields that
could not be measured are set aside rather than sinking to the bottom of the
list, because unmeasured is neither healthy nor sick.

### Crop water need is an integral, not a product of averages

ETc is the daily sum of **Kcb(t) × ET0(t)**, not season-mean NDVI turned into one
coefficient and multiplied by total ET0. The shortcut is exact only when canopy
and ET0 are uncorrelated across the season, and in an irrigated Sudanese season
they are strongly correlated in the worst direction: the canopy is near zero
during the hottest, highest-ET0 weeks before planting and after harvest.

Measured on a live Gezira field, 2022/23: the shortcut gave **385 mm**, the
integral gives **305 mm** — the shortcut overstated by 21%, because it charged
the bare-soil weeks with the crop's coefficient. A synthetic season built to
isolate the effect reproduces the same direction and magnitude.

Sparse scenes are interpolated to daily steps, but **gaps longer than 30 days are
left empty rather than bridged**, nothing is extrapolated before the first or
after the last observation, and if less than half the season ends up covered the
seasonal total is refused rather than scaled up — the missing days are not a
random sample, they are the cloudy ones. A caller with no dated series still gets
a number, labelled `APPROXIMATE` in the output and flagged in the app.
