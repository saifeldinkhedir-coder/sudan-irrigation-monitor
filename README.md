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
  registry.py                  the scheme hierarchy and roll-up.
  runs.py                      the run history; the default comparison.
  checkpoint.py                resuming a run that died part way.
  backup.py                    the half that cannot be recomputed.
  report_html.py               one self-contained file that prints.
  vocab.py                     engine vocabulary, translated once.
  weekly.py                    run, record, compare, export - one command.
  crops.py                     the crop library: parameters, aliases, and
                               the checks against a declared crop label.
  disease.py                   disease and pests as a three-rung ladder,
                               and the refusal at the top of it.
  change.py                    run-to-run comparison; ripening is not
                               failure.
  farm_records.py              REPORTED-side store (operations, costs, sales,
                               margins) and the rule-based advisory.
  farmer_channel.py            Stage 4 floor: one-sentence Arabic/English card.
  mock_ee.py                   deterministic offline Earth Engine mock, so the
                               whole pipeline runs with no network / auth / quota.
  cli.py                       command-line entry point.
console/
  app.py                       OPERATOR view: run the engine, records,
                               roll-up, backup, method. Its own address.
dashboard/
  app.py, data.py              scheme-MANAGER view: canals, equity, continuity.
farmer_app/
  app.py                       FARMER view: search, map beside list, field
                               detail. The sequence of what to show.
  search.py                    find a field: name / crop / date / harvest, and
                               selection by drawing a polygon on the map.
  fieldmap.py                  satellite map, polygon tool, place search, and
                               the frame that fits every field on opening.
  view.py, ui.py               display decisions; layout, tokens and copy.
  about.py                     the method, whole, off the working screen.
  changes.py                   what moved since the previous run.
  runner.py                    run the engine from the app, streamed.
  auth.py                      a gate on the door; not an identity system.
  onboarding.py                the first screen: three ways in.
geolibre_plugin/
  plugin.json, forms/, bridge.py   Stage 4: manifest, field form, two-way bridge.
geometry/
  build_water_frequency.py     build a persistent-water raster to trace canals.
  canal_geometry.py            fetch canals from OSM; validate ANY canal GeoJSON
                               against what the engine requires, before a run.
tests/                         873 tests; run with no Earth Engine.
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
pytest -q                              # 438 tests

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

**Logic tested, plumbing tested, measurements unvalidated.** All 438 tests pass
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

### The gates are legible, not just closed

Two outputs are deliberately locked: an absolute leaf-nitrogen percentage, and a
yield in tonnes per hectare. Neither is quoted without local measurements.

Those gates are correct and, on their own, a bad experience. A farmer who
collects twelve leaf samples and is still told "not available" cannot tell
whether they are nearly there or nowhere near — and a refusal that cannot say
what would lift it is indistinguishable from one that never lifts. The second
teaches people to stop collecting data.

So the **Record data** page in the farmer app puts a progress line under every
form: how many measurements remain, and once the count is met, whether the
obstacle is an unfitted model or a fitted one whose error is too large. The gate
does not move. It becomes legible.

| Tab | What it collects | What it unlocks |
|---|---|---|
| Scouting | photograph, canopy, weeds, pests, soil, salinity, outlet | the platform's own reliability figure |
| Nitrogen calibration | lab leaf N % or SPAD, paired with that field's red-edge indices | Level 3 nitrogen |
| Harvest | a **weighed** harvest from a known area, paired with canopy | a yield in t/ha |
| Costs | operations and sales | gross margin, water productivity |

The forms refuse what would poison the model. A calibration point with no
satellite indices to pair against is rejected; kilograms with no area is not a
yield; a harvest with no matching canopy observation trains nothing. An
unquantifiable error in training data becomes an unquantifiable error in every
prediction made from it, and unlike a satellite error it leaves no trace anyone
can find later.

Local databases (`*.db`) and submitted photographs (`observations/`) are
gitignored: they hold real people's fields, coordinates and money.

### The farmer app is Arabic first

The user is a farmer in Sudan. An interface whose chrome is English and whose
advice is Arabic asks the reader to switch language mid-sentence to use their own
tool — and the sentence they would switch for is usually the caveat, which is the
part that most needs to be understood. So the whole page flips: direction,
labels, status names, the legend, the variables table, and the reasons behind
every ranking. English is one click away.

Sensor names, units and dates are deliberately **not** translated. "Sentinel-2"
and "100 m" are not language, and translating them would make the provenance
harder to check rather than easier to read.

Layout, colour tokens and copy live in `farmer_app/ui.py`; the display decisions
in `view.py`; `app.py` is the sequence of what to show. A test asserts the CSS
hex palette and the pydeck RGBA palette describe the same four colours, so the
map, the chips and the legend cannot drift into disagreeing about what a colour
means. Another asserts no label is identical in both languages, which is almost
always an untranslated one that slipped through.

### Thermal now says when a field is too small for it

Landsat thermal is 100 m. A field spanning fewer than about two thermal pixels
shares most of them with its own surroundings, so the difference between field
and neighbourhood is suppressed by resolution rather than measured — the same
class of error as a stress threshold a field sets for itself. The engine reports
how many pixels across the field is and whether that is enough, rather than
leaving the reader to work it out from a suspiciously round zero.

### Fixes to the redesign itself

The first version of the styled app traded away three things it should not have,
and this pass takes them back.

**No webfont.** It pulled Inter and Noto Sans Arabic from Google Fonts on every
page load — a network dependency on the least reliable part of a Sudanese field
office's setup, and a request to a third party every time a farmer opens their
own crop data. Replaced with font stacks that reach a good Arabic face on every
platform this realistically runs on (Segoe UI, SF Arabic, Noto Sans Arabic,
Tahoma). Nothing is downloaded and nothing is bundled; a test asserts the
stylesheet fetches nothing.

**No brittle selectors.** It styled `[class*="css"]`, which matches Streamlit's
generated class names — they change between releases, so that selector was a
promise to break on the next upgrade. Everything is now scoped to this app's own
classes or to a documented `data-testid`; if a future release renames that id the
layout loosens rather than breaks.

**Sorting and export back.** `st.dataframe` gave sorting and a CSV download for
free, and the styled table replaced it because a dataframe cannot colour a
below-threshold reading or mark an unmeasured row. Both now coexist: the styled
table by default, a sortable one behind a toggle that states what it cannot show,
and a CSV export that includes the *reason* column — the most useful cell on an
unavailable row, and the one a screenshot loses.

**Translated at source, not afterwards.** Cells were generated in English and
translated by matching the generated text. That match breaks the moment the
engine rewords anything, and it breaks by leaving English inside an Arabic table
rather than by raising — the worst way for a translation to fail. `variables_table`
now generates in the requested language, engine vocabularies (thermal readings,
season verdicts, soil textures, condition bands) translate by lookup, and an
unrecognised value passes through verbatim so something new is visible rather
than blank. A test fails if any English phrase appears in the Arabic table.

**Thermal says when a field is too small for it.** Below about two 100 m pixels
across, a field shares most of them with its own surroundings, so the difference
between the two is suppressed by resolution rather than measured. The row now
carries that rather than letting a suspiciously round zero read as a finding.

### The map is a workspace, not an illustration

The first map drew the fields correctly and was useless for the job — it was
built to display a result rather than to be worked on. Three symptoms of one
mistake:

**The basemap was a road map.** A farmer looking at a pale rectangle over grey
streets cannot tell whether the outline sits on their field, and checking that is
the first thing anyone does with a farm map. It is now Esri World Imagery: the
reference the drawing is checked against, not decoration.

**Fields could only arrive as a file path.** That asked a farmer to produce a
GeoJSON to describe land they can see out of the window. There is now a polygon
and rectangle tool on the map — draw the boundary on the imagery, see the area in
hectares, and the file is written for you with the command to run it.

**There was no way to reach your own land.** A place search sits on the map, so
someone starting from nothing does not need their coordinates.

Only polygons and rectangles are offered: a circle or a marker cannot be a field
boundary, and offering a tool whose output the engine would reject wastes an
afternoon. A drawn shape below 0.1 ha is refused *there*, with the reason, rather
than failing three steps later in a console.

The imagery shows where a field **is**. It says nothing about how the crop is
doing this season — that is the coloured overlay and the numbers beside it, and
the caption says so.

### Method moved out of the way

The app explained itself at the reader. Under "which field first" sat a paragraph
about how the sort works; under a two-number forecast, two sentences on model
resolution; beside the water figure, a note on integration method. All of it true
and none of it what a farmer opens the app to find out.

The caveats that change a conclusion stay inline — *unmeasured is not healthy*,
*needed is not received*. Everything that explains **how** a number was reached
moved behind a single «لماذا؟» / *Why?* — one click for anyone checking the work,
invisible to everyone else. Nothing was deleted.

A second pass finished the job. Expanders on the working screen are still
explanation on the working screen, so the method now lives on one **«عن
البيانات» / About the data** page: what the tool is, where each number comes
from, how the fields were ordered, the water calculation, green-up and season
length, and what the search does with what it does not know. The report's own
sensor list, provenance and limitations sit under it.

Two warnings stayed inline, by one test: *would knowing this change what the
reader DOES today?* "Not measured is not healthy" passes — you go and look.
"Green-up is the first crossing of half the seasonal amplitude" does not. The
reason a row reads *not available* rides on the row as a hover tooltip, because
it says whether to wait for a clear scene or to go and look.

The header lost its paragraph. It used to open with the provenance discipline —
true, and the first thing a farmer read every morning before reaching an answer.
The promise did not weaken; it lives where it is exercised, in the sensor and
scale columns on every row.

### Finding a field

A search that only works while the farm fits on one screen is a demonstration.
Gezira is roughly two million feddans across tens of thousands of tenancies, so
the toolbar is on the working screen, not behind a menu: **name or crop**,
**crop**, **status**, a **date window** on green-up / harvest / last-seen /
sowing, **harvest**, and **inside the drawn shape** — draw a polygon and the
list narrows to the fields whose centre falls in it. Clicking a field on the map
selects it, resolved by point-in-polygon rather than by matching tooltip text.

Arabic search folds what a keyboard produces several ways: أ إ آ read as ا, ة as
ه, ى as ي, and Arabic-Indic digits find Western ones. Typing ٣ finds Field 3.

Filtering obeys the rule the colours already obey. A field with no crop recorded
is not silently dropped by a crop filter — dropping it says *this is not
sorghum*, when the truth is that nobody said. It goes to a visible **unknown**
list with the reason. Wheat, by contrast, is a genuine non-match and is not
reported as uncertainty. On the map, unmatched fields are **dimmed, not
removed**: a filter that made a field vanish would leave a farmer unable to tell
a filtered field from one the tool never had.

The harvest filter offers *harvest reported* and *no harvest reported*, not
"harvested / standing". Nothing measures standing, and an expected harvest date
is this tool's own arithmetic — labelled ESTIMATED wherever it appears, against
a farmer's own date labelled REPORTED.

### The map opens on the fields

A farm ten kilometres across has a mean position with no field anywhere near it,
so a fixed zoom around that mean opened on bare ground with every polygon just
off the edge — indistinguishable, to the person looking, from a map that failed
to draw them. The frame is now computed from the bounds of the fields. folium's
own `fit_bounds` is not used: streamlit-folium rebuilds the map and the call
does not survive the trip, so the zoom is computed here, tested without a
browser, and cannot be dropped by a component upgrade.

### One language at a time

The advisory wrote its sentences in Arabic and interpolated the engine's English
verdicts into them, so a farmer read *«حالة الكلوروفيل مقارنة بالمخطط: WITHIN
SCHEME NORM»* — the sentence in their language, the finding in someone else's.
The finding is the half that carries the meaning. Engine verdicts are now
translated at the point they are written, with anything unrecognised passed
through in English so a new verdict is visible and fixable rather than blanked.

The list of things the tool does **not** claim is emitted by the engine in both
languages. It is the last list that should reach a Sudanese farmer in English.

## Disease and pests, and the claim this tool will not make

A satellite cannot name a disease. That is not a limitation of the current
implementation to be lifted by better code — it is a property of the
measurement. Sentinel-2 sees reflectance in a handful of broad bands, and
anthracnose, water stress, nitrogen deficiency, salinity, stem borer, lodging
and a badly set seed drill all move those bands together.

Products in this market do claim it: a field drawn red and captioned with a
pathogen. That is a guess wearing the clothes of a measurement, and the cost is
not abstract — a farmer sprays a fungicide against a disease they do not have,
spends money they do not have, and learns that the tool lies. The second
consequence is worse than the first, because it also destroys the value of the
readings that were real.

So the layer is a ladder, and each rung says what it is:

| Rung | Source | Claim | Colour |
|---|---|---|---|
| **REPORTED** | a person who walked out and looked | names a disease as present | red |
| **ANOMALY** | Sentinel-2 | a patch is unlike the rest of *this field*; size and direction, **no cause** | amber |
| **RISK** | ERA5-Land + CHIRPS | the weather was favourable to *X*; true of every field under that sky | grey |

The colours carry the argument. REPORTED is the only red, because it is the only
rung that names a disease. A weather window drawn red would be this whole
product category's failure in one colour.

**The anomaly** compares a field with its own interior rather than with its
neighbours — median minus two robust sigmas of the field's own spread — and
returns a size in hectares and one of eight compass directions, because a
coordinate pair is not a direction to anybody standing in a field. A uniformly
poor field produces no anomaly, and that is correct: *unlike the rest of this
field* and *bad* are different statements.

**The weather models** are published infection windows — a temperature range plus
leaf wetness — from phytopathology, mostly from other countries, **none validated
against Sudanese disease surveys**. Leaf wetness is the variable they want and
nothing measures it; the proxy is a rain day or a daily maximum relative humidity
above threshold, computed from ERA5-Land dewpoint against the day's minimum
temperature. It is a proxy, it is named as one, and it will be wrong on a windy
night. Sudanese winter wheat sits under heavy dew and almost no rain, so a model
counting only rain would report no rust risk all season.

Problems with **no defensible daily model get none**. A whitefly-borne virus is
driven by insect population dynamics and a soil-borne wilt by inoculum that has
been in that soil for years; a temperature window for either would produce a
number every day and mean nothing. Those are returned in a visible `no_model`
list, so the absence of a fall-armyworm risk line reads as *nothing here can
predict it*, never as *it is fine*. Striga is registered as a **parasitic weed**,
not a disease — it is the largest biological constraint on Sudanese sorghum and
it is a flowering plant, and calling it a disease would send a farmer for a
fungicide.

Rung 3 closes the loop: the scouting form asks **which** problem, offered only
from the selected crop's own registry, and the engine reads named findings back
out of the observation store. A ticked "disease signs" checkbox does not lift the
ladder — *I walked the field* is not *I found anthracnose*.

## Crop diversity

The engine applied one crop to a whole run. A Gezira tenancy rotates cotton,
sorghum, wheat and groundnut, so a wheat block inside a sorghum run was given
sorghum's growing-degree base and its 38 °C heat threshold — six degrees above
where wheat actually starts losing grain. **The number was not missing. It was
wrong, and nothing on the screen said so.**

`src/crops.py` now holds eleven crops of Sudanese irrigated agriculture —
sorghum, wheat, cotton, groundnut, sesame, maize, sunflower, onion, faba bean,
alfalfa, tomato — each with its base temperature, heat threshold, FAO-56 Kc
stages, rooting depth, sowing window and registered problems. Every field is
analysed as **its own** crop, taken from its `crop` property and falling back to
the run's; the report records which, per field. Arabic crop names resolve.

The Kc table is not used to compute the water requirement — that is derived from
observed greenness on purpose. It is used as a **check**: a Kcb from NDVI far
outside the published range for the declared crop means either the label is wrong
or the field is not carrying the canopy the label implies, and both are worth
knowing. A crop the library does not know is analysed with generic parameters and
says so on the field itself, because "nobody declared a crop" and "somebody
declared one I did not recognise" are different facts.

## What changed since last time

Every report was a season summary: you could read one and know how the farm
stood, and could not read two and know what had moved.

The verdict this page exists to get right is that **a decline past the NDVI peak
is a crop ripening, not a crop failing**. Sorghum greens up in August, peaks in
October and senesces on purpose all the way to harvest. A change detector that
flags every decline flags every field on the scheme every autumn — and buries the
one field that is actually failing. The same fall before the peak is a decline;
what separates them is the green-up date the engine already computed.

A move smaller than the field's own robust spread is reported as steady, so a
noisy field needs a bigger move to be believed. Dates are **scene** dates, not run
dates: two runs a week apart can rest on scenes a month apart when the newer run
found nothing but cloud, and reporting "7 days" for a 31-day gap would make a slow
drift look like a collapse. A field present in only one run is listed, never
silently dropped.

## Running it from the app

Drawing a field and then being handed a shell command is a step where people
stop. The **Run the analysis** page takes the field file, the season, the default
crop and the output path, prints the exact command, gives a rough time estimate,
and streams the engine's own output — refusals included. It runs the engine as a
child process: Earth Engine authentication, quota errors and network stalls all
fail in ways that would otherwise take the app down with them.

Drawn shapes now go through a **field editor** before saving: name, crop, sowing
date and tenancy number. A boundary with no name and no crop is a shape, not a
field — the search cannot find it, and the report calls it "حقل 3".

## Running it safely

The app binds to `127.0.0.1` by default (`.streamlit/config.toml`). Streamlit's
own default binds every interface and prints an External URL, which is what it
says it is. This app draws real tenancy boundaries, their coordinates, and
whatever a farmer has recorded about their land and their money. Bind it wider
only behind authentication.

Earth Engine credentials are never asked for by the app and never stored by it;
the child process uses whatever `earthengine authenticate` put in the user's own
home directory.

## One weather series for one weather pixel

ERA5-Land is 11 km and CHIRPS is 5.5 km. A farm that fits inside one of those
cells has one weather series, not one per field — and fetching it per field made
the same round trip four times for four identical answers, or forty times on a
forty-field scheme. The extent is measured against the native pixel and the
decision is recorded in the report. A scheme spread over thirty kilometres spans
several cells, and sharing one series across those would be inventing weather for
the far end.

## Running it as a system, not as a program

Eleven changes, in the order they depend on each other.

### The administrative hierarchy

The platform held a flat list of polygons. Gezira is administered in nested
units, and every question anybody with authority asks is asked at one of them.
*How is block 14 doing* is not a question a flat list answers without a
spreadsheet — and a tool that needs a spreadsheet for the commonest question in
the institution gets replaced by the spreadsheet.

The level names are **data, not fact**. The default follows the structure
commonly described for Gezira — group, block, number, tenancy — and its own
`name` field says **CONFIRM WITH THE SCHEME**: naming has changed with
successive reorganisations, and the wrong label on the right structure looks
correct in a report. A farm with no hierarchy is a valid deployment, not a
degraded one.

Aggregation will not average away an unmeasured field. Forty fields of which six
could not be seen produce a number describing thirty-four, and presenting it as
the block's is the map's grey problem one level up. Every unit carries its
coverage; below the floor the mean is **withheld and the coverage reported in its
place**. Fields that cannot be placed are listed, never bundled into an invented
unit that then appears in reports as a real one.

### The run history

The change page used to ask the reader to **type the path** of an older report.
That was not a rough edge; it was the interface admitting there was no history.
Runs are now copied into `runs/<farm>/` and indexed, so the honest comparison —
the previous run over the *same* farm — is what happens when nobody chooses.

It refuses to compare two runs whose field names barely overlap: that produces a
page full of "new" and "missing" fields which a reader takes as churn on their own
land. A boundary file that changed between runs is flagged but not refused —
fields do get redrawn, and a "change" in a redrawn field is partly the redrawing.

Directories and JSON, deliberately. This runs on a laptop in a field office, gets
copied to a USB stick, and must be readable in five years by somebody who has
never heard of this program.

### The weekly job

A monitor that needs somebody to press a button is not a monitor. `src/weekly.py`
runs, records, compares and exports in one command.

It will **not** install a scheduled task. A tool that quietly arranges to run
itself every week on a laptop it does not own has made a decision that was not
its to make, and whoever inherits that laptop finds a job they cannot explain.
`--show-schedule` prints the exact line for the platform; the operator runs it.

The exit code is set honestly, because a scheduled job that fails silently is
worse than none: everybody believes the farm is being watched, and it has not
been watched since March.

### Resumable runs

A run over a scheme is thousands of round trips over an unreliable connection
against a finite quota. It dies at field 3,700 of 4,000 and everything is lost,
including the 3,699 that worked — so the person decides not to spend another
three hours, and the tool that could have monitored the scheme does not, for
reasons that have nothing to do with remote sensing.

Each field's result is written as it arrives. Resuming is only safe while the
question is unchanged, so the checkpoint fingerprints the boundaries, the season,
the crop and the series setting; a mismatch **discards it and says so**, because
merging would produce a report half one question and half another with nothing on
its face to say which. The partial is written beside the report, never at it: a
half-finished report at the report's own path is read *as* a report, and a farm
whose worst fields came last looks fine.

Also: ERA5-Land at 11 km was fetched once per field for identical answers. Now
once per farm — but only while the farm measurably fits inside one cell.

### The accuracy figure, on the front screen

`agreement_summary` measures how often the satellite agreed with a person who
walked out and looked. **No competitor shows this** — not because they are
better, but because they do not collect it. A modest known number beats a total
unknown claim.

It could never accumulate. Observations were saved with no satellite side, so
`satellite_agreement` was NULL on every row ever written and the figure was
structurally stuck at zero while the screen said "no clear comparisons yet" for
ever. Observations are now scored at save time, and an UNCLEAR says which side
was missing. Unclear cases stay out of the rate: a forced verdict would corrupt
the one number that describes this platform's own accuracy.

### A file that prints, and needs no network

Streamlit is a live websocket app — over a block office's connection, close to
the worst possible architecture: blank until the socket comes up, dead when it
drops. One HTML file with its data, styling and map inside it opens from a USB
stick, survives being emailed, and needs no server. For a field office that is
not a fallback; it is the better artefact.

It prints because the Scheme runs on paper and the meetings where a block is
decided have no laptop in them. Page breaks keep a field off two sheets, and
every status carries a **mark as well as a colour** — on the photocopy that
reaches the meeting, red and green are the same grey.

The map is an SVG drawn from the coordinates with a scale bar, and it says it is
**not imagery**: a drawing that looks like satellite imagery and is not would be
worse than a plain drawing. No CDN, no webfont, no tiles, no analytics — pinned
by a test, for a reason beyond convenience: a page that phones home tells a third
party which tenancy is being looked at.

### The first screen

The app opened on a demonstration farm: not the reader's land, looking like a
working product, with nothing on it saying how to reach their own fields.
Somebody opening it for the first time had two choices — believe the demo was
theirs, or close the tab.

There are exactly three ways in, and naming all three is the whole feature: draw
your fields, load a file, or see the demonstration. The demo option carries its
own caveat in the same breath, not on a page the reader has to find.

### Backup

Every satellite figure here can be regenerated; a lost report costs an afternoon
of compute. Thirty weighed harvests, thirty leaf-nitrogen samples and a season of
scouting photographs cannot. They exist on one laptop, they took a season of
somebody's labour, and they are what unlocks the gated figures.

The archive covers that half and says plainly why it skips the other. It verifies
against its own checksums — a truncated copy of a season of records looks exactly
like a good one until the day it is needed — and it says, every time, that a copy
on the same disk is a filing habit, not a backup.

### A gate on the door

`farmer_app/auth.py` is a password check with per-user farm scoping: PBKDF2 with
a per-user salt, constant-time comparison, one message for both failure modes.

It is **not an identity system**, and the login screen says so. No transport
security, no meaningful session expiry, no audit trail, no second factor. It is
correct behind a reverse proxy that terminates TLS, or on a machine only the
operator can reach; it is not sufficient alone on a public address holding real
tenancy records. **A security control that overstates itself is worse than none,
because people stop taking the other precautions.**

There is **no default password**. A deployment with no users file is OPEN and
says so on every screen, because a default credential is a credential everybody
has. A corrupt users file does not fall open.

```bash
python -m farmer_app.auth add ali --farms "block 14"
```

### A phone view

The officer standing in a field has a phone, and neither the map nor the drawing
tools work with a thumb on a five-inch screen in daylight. The compact view is a
read-only list: worst first, the crop, the reading, and the first line of advice.

### A label that falls back to its own key

Not a feature — a defect this work produced and a test now prevents. `t()`
returns the key when a label is missing, so a whole page of the sidebar rendered
as `page_units` and `page_backup` in the browser while every test passed. A test
now walks the app source for every `ui.t("…")` and asserts the key exists. The
failure mode of a silent fallback is that nobody notices.


## Three applications, split by audience

The farm app is ONE screen: the map, the list, and the field you picked. That is
what a farm-monitoring tool is.

Eleven operator features were commissioned and built, and each honestly needed
somewhere to live. They went into the farm app's sidebar, which became a
seven-item menu with the farm as item one. Moving them into a collapsed drawer
was not a fix — a drawer full of pages is still pages, and the reader has to open
it to find out it is not for them. Every session began with a decision that had
nothing to do with their crop.

So the split is by **audience**, not by tidiness:

| | who opens it | how often |
|---|---|---|
| `farmer_app/` | a farmer or field officer | every morning |
| `console/` | an operator | occasionally |
| `dashboard/` | a scheme manager | for the canal network |

```bash
streamlit run farmer_app/app.py -- --report farm_report.json --fields my_fields.geojson
streamlit run console/app.py
```

Nothing was deleted; every console page is the module it always was. What changed
is that a farmer no longer walks past it. The split is also the right security
boundary: running the engine, editing the sources and writing a backup archive
spend money, change files and copy other people's records, and they now sit
behind a different address — the natural place for a different password, or for
no route at all from wherever the farm screen is published.

The farm app's sidebar is the language switch. The header is the name and one
line. For demonstration data that line carries the caveat as a clause — the
obligation is real (real imagery over invented boundaries is the most misleading
thing this tool produces) but it does not need its own element.

### A bug the split uncovered

`_render_map` had been called and never defined since an earlier pass. It sits in
the branch for *boundaries drawn, nothing analysed yet* — the one path nobody
opens twice — so it imported cleanly, passed every test, and would have died with
`NameError` in front of the first person who drew a field and reloaded.

A name used only inside a function body is not resolved until that body runs. A
test now parses the app, collects every local function it calls, and asserts each
one exists. It found `_render_map` on its first run.


## Running the demonstration

```bash
cd ~/Desktop/sudan_irrigation_monitor && python tools/seed_demo.py
```

```bash
cd ~/Desktop/sudan_irrigation_monitor && streamlit run farmer_app/app.py
```

```bash
cd ~/Desktop/sudan_irrigation_monitor && streamlit run console/app.py -- --data demo --farm demo
```

Half of what this platform does is refuse, and a demonstration in which every
gate is shut shows only half the design — a reader sees "not available" nine
times and cannot tell whether the tool is careful or unfinished. What makes it
careful is what happens **when a gate opens**: the figure arrives with its
error, its sample count, and the name of whoever measured it.

`tools/seed_demo.py` writes a synthetic ground-truth set so that can be seen:
30 weighed harvests (the number the yield gate asks for), 4 scouting records —
two named findings and two clean walks — and 20 field operations. With it, the
yield unlocks with an RMSE, the disease ladder reaches REPORTED, and the
satellite-versus-observer rate exists for the first time.

Four things keep it from becoming a lie:

- It writes to `demo/`, never the working directory. The real stores stay
  empty, because they should be: nobody has measured anything yet.
- Every row carries `observer = DEMONSTRATION` and a note saying it is
  synthetic. **The stamp is in the data**, so it survives being copied out of
  the directory that explains it, and the console warns wherever it reads one.
- It refuses to run against a directory holding records it did not write. A
  tool that can overwrite a season of somebody's scouting with invented rows is
  a tool that eventually will.
- The harvests are drawn from a **declared line with declared noise**, printed
  on creation: `yield t/ha = 8.5 × NDVI − 1.1 + N(0, 0.18)`. The fit recovers
  it at slope 8.71, r² 0.97. That is the point — the model is this line found
  again, not a finding about sorghum.

It invents no satellite measurement. It reads the real report to score the
scouting records against what the satellite saw, and a test asserts the report
is byte-identical afterwards.

See **[VALIDATION.md](VALIDATION.md)** for what has and has not been checked,
the seventy arbitrary constants ranked by how much of the output moves with
them, and what would falsify each of the ten that matter most.
