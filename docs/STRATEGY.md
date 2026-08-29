# Sudan Irrigation & Agriculture Monitor — build plan, validation design, and an honest feasibility split

This document is the thinking behind the code. It exists to keep the platform
narrow and honest: to say plainly what can be measured today, what needs work
before it can be trusted, what should be fenced or dropped, and — the hardest
question in the whole brief — how you would ever *prove* that a head-to-tail gap
is a real distribution problem and not an artefact of soil, crop or planting
date.

It is deliberately blunt where the science is weak. A narrower tool that is
honest about its limits is worth more than a broad one that quietly invents
numbers.

---

## 0. Where the pilot stands

- **Site:** New Halfa Scheme, Kassala State (~164,000 feddans, fed by Khashm
  el-Girba Dam on the Atbara River). Chosen over Gezira not because Gezira is
  the wrong scientific target — it is the canonical one — but because as of 2026
  Gezira's canals are an active humanitarian and security site (SAF recaptured
  Wad Madani in January 2025; mass graves were documented in Gezira irrigation
  canals; the Managil reach has been dry since May 2024). New Halfa is a real
  multi-tier gravity network in a more stable state. **This is a judgement call,
  not a fact — revisit it if your access to Gezira is genuinely safe and you
  have people on the ground there.**
- **Geometry:** none surveyed. `geometry/build_water_frequency.py` is the way in
  — a persistent-water-frequency raster to trace canals over, instead of tracing
  a single cloudy scene by eye.
- **Engine:** the network/field/nutrition/climate/ground engine is built and its
  decision logic is tested (38 tests). The Earth-Engine-facing paths need your
  authenticated `EE_PROJECT` to run against real imagery; they have not been
  executed against a live scene here.

---

## 1. Staged build plan — each stage produces something you can show

The rule for every stage: it ends with an artefact you can put in front of a
manager or a farmer, not a milestone only you can see.

### Stage 0 — Honest engine (delivered)
Fixed engine, tested decision logic, geometry helper.
**Demo:** `pytest -q` green; `python cli.py --canal sample_canals.geojson --season 2022 --out demo.json`
produces a JSON where every number carries provenance and every missing number
says `NOT AVAILABLE` with a reason.

### Stage 1 — Real geometry for New Halfa
Run `build_water_frequency.py` over a New Halfa bounding box for a season you
believe carried water. Trace 3–5 major/branch canals as LineStrings in QGIS or
GeoLibre, attach `name` (and `canal`/`office` if you have them), draw rough
command-area polygons per canal, export GeoJSON.
**Demo:** the traced canal network laid over the water-frequency raster — visibly
following the bright persistent-water lines. This is also the first honest test
of the "canals narrower than ~20 m are invisible" limit: you will see exactly
which order of canal the method can and cannot resolve.

### Stage 2 — Real run + manager dashboard
Run the engine on the traced network for the three most recent seasons. Build the
Streamlit manager view: a sortable canal table (water status, head-tail gap with
its CI, irrigated extent with its bimodality flag, seasonal ET and rainfall),
each row expanding to the per-reach series and the full provenance.
**Demo:** a manager opens the dashboard, sorts by head-tail gap, and sees which
canals are flagged *and how confident the flag is* — not a bare number.

### Stage 3 — Attribution & validation harness (Section 2 below)
Add the soil, crop and phenology layers; fit the stratified model that asks how
much of each gap survives controlling for soil, crop and planting date; run the
negative controls and the placebo test. This is the stage that converts "there
is a gradient" into "there is a gradient not explained by soil, crop or planting
date, and it is corroborated by the canal-water signal."
**Demo:** for each flagged canal, "raw gap 38%; after controls 22% (95% CI
9–34%); radar wet-fraction also declines head→tail; rainfall uniform" — or the
opposite, "gap collapses to 4% after controlling for crop, so this was a crop-mix
artefact." Both are wins.

### Stage 4 — Ground layer + the farmer channel
Wire the GeoLibre field-collection form to `ObservationStore`. Start the
agreement figure accruing. Ship the farmer channel as the *simplest thing that
reaches reliably* (Section 5).
**Demo:** a real sentence generated for a real reach from real data, and the
first few ground observations scored against the satellite record.

### Stage 5 — Nutrition ladder in practice, rangeland, drone pilot
Reference strips with 2–3 cooperating farmers (Level 2 nitrogen — the honest
level). Rangeland layer, handled with the conflict-sensitivity in Section 6.
Drone verification pilot, pending Sudanese flight permits and radiometric
calibration.

---

## 2. Validation design — is the head/tail signal real, or an artefact?

This is the question the whole platform lives or dies on, so treat it as a
falsification exercise, not a demonstration. The engine as built **detects** a
gradient with a confidence interval. Detection is not attribution. A downward
NDVI gradient from head to tail can be produced by at least five things that have
nothing to do with unfair water distribution:

1. a **soil** gradient (heavier or more saline soils happening to lie at the tail);
2. a **crop-composition** gradient (more cotton at the head, more fallow at the tail);
3. a **planting-date** gradient (the tail plants later, so at any snapshot it is
   simply younger);
4. **drainage/salinity** problems concentrated at the tail (a real problem, but
   an on-farm/soil one, not a delivery one);
5. **upstream abstraction** — which *is* a delivery problem, but a different one
   from "the canal silted up."

A tool that reports the raw gap as "distribution inequity" is wrong five ways.
Here is how to earn the claim.

### 2.1 Detection (built)
OLS slope of NDVI against normalised position, bootstrap CI on the gap, flag only
when the 95% lower bound exceeds the threshold. This is in `decision_logic.
fit_head_tail_slope` / `equity_flag` and is tested. It establishes *that* there
is a gradient we are confident about. Nothing more.

### 2.2 Rule out planting date with the *shape* of the time series — satellite-only, strong
Planting-date differences and water-shortage differences leave **different
signatures in the NDVI time series**, and this is the single most useful
satellite-only discriminator you have:

- A tail field that merely **planted late** produces a *time-shifted* curve: it
  greens up later but reaches the same peak and the same integral once it
  catches up.
- A tail field that is **short of water** produces a *depressed* curve: it greens
  up on time but plateaus lower, or senesces early — a lower amplitude and a
  smaller season integral, not a shift.

So derive, per field, the green-up date (the inflection of the NDVI rise) and the
season integral / peak. If the tail's green-up date is later but its peak matches
the head, you are looking at a planting-date artefact and should say so. If
green-up is simultaneous but the tail's peak or integral is depressed, the
planting-date explanation is dead. This turns a confound into a measurement.

### 2.3 Rule out soil and crop by stratified comparison — the workhorse
Bring in two layers at Stage 3:

- **Soil:** SoilGrids (250 m) for texture/clay and, ideally, a salinity proxy
  (a bare-soil MNDWI/salinity-index composite from the dry season). Coarse, but
  enough to stratify.
- **Crop:** a per-season crop classification (even a coarse cropped/fallow +
  dominant-crop map from the S2 time series) or, better, farmer-declared crop
  from the ground layer.

Then compare head vs tail **within the same soil class and the same crop**. If
the gap survives inside every stratum, soil and crop are not the sole cause. The
formal version is one multiple regression per scheme:

```
NDVI_integral ~ position_along_canal
                + soil_class + crop + green_up_date
                + (1 | canal)          # canal as a random effect
```

The partial coefficient on `position_along_canal`, **after** the controls, is the
part of the gradient not explained by soil, crop or planting date. Report *that*
coefficient with its CI as the equity figure, not the raw slope. If it collapses
toward zero once the controls are in, the raw gap was an artefact and you have
just saved a manager a wasted week. This is honest attribution; it is still not
proof of causation, and the platform must keep saying so.

### 2.4 Corroborate with the canal-water signal — this is the differentiator
Here is where the network layer earns its existence. If the crop gap is caused by
water not reaching the tail, there should be a **matching signal in the canal
itself**: the Sentinel-1 wet-fraction along the reach should decline toward the
tail, or show a discontinuity, **in the same season** the crop gap appears. The
decisive cross-check:

- crop gap **and** a head→tail decline in canal wet-fraction → consistent with a
  delivery problem *to the reach*;
- crop gap **but** the canal reads wet and continuous all the way to the tail →
  the water reached the reach, so the cause is on-farm, soil, crop, or
  distribution *within* the reach — **not** the canal delivering less. That
  distinction is exactly what a farmer cannot see and what turns "my crop is bad"
  into a diagnosable statement.

Caveats that must ride with this, always: radar sees **standing water, not
flow**; a canal narrower than the pixel is invisible; a wet reading is not a
flowing one. So the canal signal corroborates or contradicts; it does not by
itself measure delivered volume.

### 2.5 Rule out rainfall (built) and add an independent productivity check
Over a scheme as small as one command area, CHIRPS rainfall is close to uniform,
so a gap that tracks a rainfall gradient is not a network fault — the engine
already refuses to read stress without this context. For a second, independent
productivity measure, cross-check the NDVI gap against **FAO WaPOR ET** (100 m):
if an ET gradient corroborates the NDVI gradient, two independent sensors agree.
(This is the data source Elnour et al. 2024 used for their Gezira equity study —
worth reading, not least because they found the naive tail-end-deprivation story
does *not* hold at every scale in Gezira. Do not assume your sign.)

### 2.6 Negative controls and a placebo — the part people skip
- **Multi-season persistence:** a structural cause (siltation, a bottleneck)
  recurs across seasons; a one-off bad year does not. Soil recurs too, so
  persistence separates *structural* from *transient*, not soil from water — but
  a gap that appears once and never again was never a network fault.
- **A known-good canal:** pick a short, well-managed canal you have reason to
  believe delivers evenly, and confirm the method reports no gap. If it invents
  one, your pipeline has a bias.
- **A rain-fed / non-canal strip:** run the same machinery where there is no
  canal at all and confirm it does not manufacture a head-tail gap from a line
  drawn on a map.
- **Placebo positions:** shuffle the reach positions randomly and confirm the
  flag rate drops to your false-positive rate. If randomised "canals" flag as
  often as real ones, the signal is noise.

### 2.7 Ground truth closes the loop
The one direct measurement of the thing the satellite proxies is in the ground
layer already: `water_reached_field`, `days_since_irrigation` and
`outlet_condition`, recorded at head vs tail reaches. Ten honest observations at
each end of a flagged canal are worth more than any index. The `agreement_summary`
figure is what tells you, over a season, whether to trust the satellite equity
signal at all — and it is the only number in the platform that measures the
platform.

**The claim you can defend at the end of all this:** "The tail reaches of canal
7 showed lower crop vigour than the head across three seasons; the gap persisted
after controlling for soil, crop and planting date; the canal's own radar
wet-fraction declined toward the tail in the same seasons; and rainfall was
uniform. This is consistent with less water reaching the tail." Not "canal 7 was
mismanaged." The engine measures and corroborates; it never attributes to a
person or a decision.

---

## 3. Feasible today vs needs R&D vs scientifically weak

### Feasible today — defensible now, with the caveats already in the code

| Capability | Why it holds |
|---|---|
| Vigour / canopy moisture (NDVI, EVI, NDMI) | Standard, robust, per-field. |
| Canal **standing-water presence** (S1) | Specular water at C-band is a real, cloud-proof signal for canals wider than the pixel. |
| Thermal water stress on **large** fields (LST) | A direct physical measure; sound at 100 m for large fields and command scale. |
| Rainfall context, dry spells, season-vs-history (CHIRPS) | Mature dataset; the cause-separation the whole tool depends on. |
| Irrigated extent via **Otsu** | Correct method for a bimodal cropped/bare mixture — *with* the valley-depth flag that admits when the split is weak. |
| Head-tail gap **detection** (slope + CI) | Honest as a detector. Attribution is Stage 3, not today. |
| **Relative** red-edge condition (Level 1 nitrogen) | Ranking fields is honest and immediately useful; no calibration needed. |
| Ground observation store + **self-reliability figure** | Simple, and the most valuable honesty mechanism in the platform. |
| GDD / heat-stress days | Fine as computed — but the thresholds are published figures, not Sudanese trials (flagged ARBITRARY in code). |

### Needs R&D or calibration before it can be trusted

| Capability | What is missing |
|---|---|
| **Absolute nitrogen** (Level 3) | ≥30 lab/SPAD points *per crop*, and local validation that red-edge→N even holds in Sudanese sorghum/wheat under endemic water and salinity stress. The engine already refuses until this exists — keep it refused. |
| **Attribution** of the head-tail gap | The Stage-3 stratified model + soil/crop/phenology layers + ground outlet observations. Without these you have a detector, not an equity claim. |
| **Network water-use efficiency** (ET ÷ released) | You need the denominator — water *released* to the command — from the scheme authority. If you do not have release volumes, you cannot compute efficiency, only consumption. And MODIS ET at 500 m does not resolve anything smaller than a major command. |
| **S1 canal *continuity/flow*** | Presence is fine today; inferring continuous *flow* from standing-water snapshots is R&D and may not be achievable at 10 m. Do not promise flow. |
| Soil-moisture as a product | S1 soil-moisture retrieval under a crop canopy is genuinely hard (vegetation dominates backscatter); NDMI is *canopy* water, not soil water. Do not sell root-zone soil moisture. |

### Scientifically weak — fence hard or drop, and say why

- **Canal siltation / degradation from satellite.** Siltation is a change in the
  canal's *cross-section and bed level*. Satellite cannot see canal bathymetry.
  The only proxy is a fall in wet-fraction or wet-width across seasons *at
  constant upstream supply* — and supply is never constant, so the confound
  swamps the signal. Keep this explicitly exploratory; never a headline number.
- **Efficiency without release data.** As above: without the volume released, an
  "efficiency" figure is a ratio with an invented denominator. Withhold it.
- **Absolute nitrogen in mixed-stress fields even *with* a scheme-wide model.**
  Water stress and salinity — both endemic in Sudanese irrigation — depress
  chlorophyll and will bias a regional CIre→N calibration. The reference-strip
  method (Level 2) is far more defensible because the confounds cancel inside the
  field. **Push Level 2; be very cautious with a scheme-wide Level 3.**
- **GRACE-FO at field or canal scale.** ~300 km footprint. Already fenced to
  regional context in the engine. Never let it near a canal figure.
- **App features borrowing scientific credibility.** The AI chat assistant,
  community forum and cost tracking are reasonable product features, but they are
  not measurements and must not inherit the analysis engine's provenance or
  authority. Keep them visibly separate from the numbers.

---

## 4. Where this genuinely differs from existing farm apps — without overclaiming

**What is actually different.** The mainstream tools — EO Browser / Sentinel Hub,
OneSoil, Cropin, Digital Earth Africa, and the field-scale layer of most national
systems — answer *"how is this field?"* very well. They do not answer *"did the
water arrive, and was it shared from head to tail?"* This platform's real
contribution is three things working together:

1. a **per-canal head-to-tail equity figure reported with a confidence
   interval**, not a point number;
2. that figure **cross-checked against the canal's own radar water signal and
   against rainfall**, so a crop gap can be separated from a delivery gap from a
   drought; and
3. a **measured self-reliability figure** from the ground layer, so the platform
   states how often its own indicators are right.

**What is NOT new, and should not be dressed up as new.** Field-scale NDVI
monitoring is a commodity. Red-edge chlorophyll indices are widely used, mostly
at the relative (Level 1) level this platform is honest about. Even scheme-scale
*equity analysis* exists — Elnour et al. (2024) did exactly this for Gezira with
WaPOR. The difference there is operational, not conceptual: theirs is a one-off
research study; this is a per-canal tool a manager runs each season and a farmer
gets one defensible sentence from. Claim the operationalisation and the
cross-layer corroboration. Do not claim to have invented equity remote sensing.

**The blunt version.** The differentiator is real but narrow, and it is only as
good as two things you do not yet have at scale: accurate canal geometry, and a
stream of ground observations to validate against. Until the observation layer is
feeding back, the equity number is an unvalidated remote-sensing
artefact-candidate — a strong hypothesis, not a finding. The platform's honesty
machinery exists precisely so it never has to pretend otherwise.

---

## 5. The farmer channel — start with what reaches reliably

The brief is right that these farmers are technically capable and that you should
not design around assumed illiteracy or absent data. But the design criterion for
the *first* channel is **reliability of reach**, and a one-way message reaches
more reliably than anything that requires an app session on a specific device
with connectivity at a specific moment. So:

- **Floor (build first):** a generated, per-reach text card — one sentence, in
  Arabic — deliverable as SMS/WhatsApp or printed at the scheme office. Example:
  *"Your reach, canal 7, this season: crop vigour about 40% below the canal head;
  canal water was present on 3 of 9 radar passes at your reach versus 8 of 9 at
  the head; rainfall was normal for the season."* Every clause is a measured
  quantity with a provenance behind it, and the phrasing attributes nothing.
- **Rich channel (for those who install it):** the GeoLibre app — map, offline
  area, field-collection form — is the two-way channel. The farmer submits a
  photo and a structured observation; the satellite verifies; the verified report
  reaches the manager. The farmer becomes a data source, which is what makes the
  self-reliability figure possible.

One analysis engine, three phrasings of the same number, exactly as you designed:
farmer sentence, manager row, researcher time-series API.

---

## 6. Two risks that are not technical, and must not be treated as technical

- **Rangeland and corridor maps in a live conflict.** The brief already flags
  this and it is right. Farmer–herder conflict is a live driver of violence in
  Sudan, and a map of grazing corridors or water points is a targeting artefact
  in the wrong hands. Design the rangeland layer as neutral information for all
  parties — the same greenness-timing and water-point data offered identically to
  pastoralist and farmer — never as a claim that supports one party's access over
  another's. When in doubt, withhold the layer rather than ship a version that
  can be weaponised.
- **The war context around the schemes themselves.** Gezira's canals are, as of
  2026, a documented atrocity site. Any public-facing framing of "monitoring
  Sudan's canals" inherits that context whether you intend it or not. This is a
  reason to lead with a scheme where the framing is about water and crops, not
  about a recent massacre — and a reason to be careful about who gets access to
  reach-level maps, for the same reason as the rangeland layer.

---

## 7. What is built and tested, and what is still genuinely unrun

**Fixed in the engine (with tests):** the silently-discarded command-area
geometry; the irrigated-extent method (Otsu with a valley-depth bimodality flag,
not median±kσ); the two-point head-tail difference (now a slope fit with a
bootstrap CI, plus a near-zero-head guard so a tiny denominator can no longer
produce a −1000 % "gap"); the per-day / per-year Earth Engine round-trips in the
climate module (now one `aggregate_array` each); the disconnected nutrition and
climate modules (now wired into `analyse`); and provenance on every number.

**Built and tested since Stage 0:**

- **Attribution & validation harness (Stage 3, `src/attribution.py`)** — the
  Section-2 design in code: a stratified OLS that controls for soil, crop and
  planting date; the partial position effect and the adjusted gap with a CI; a
  placebo permutation test; green-up extraction; multi-season persistence; and a
  negative-control check. The tests prove the two behaviours that matter: a real
  gap survives the controls, and a soil-artefact gap collapses after them.
- **Manager dashboard (Stage 2, `dashboard/`)** — a Streamlit view that reads the
  engine's results JSON, orders canals by the manager's real question, and
  carries every integrity guarantee to the screen (NOT AVAILABLE shown as words,
  flags shown with their CI, weak extents marked, unreliable gaps refused). Its
  data logic is tested and a rendered screenshot is in `docs/`.
- **Farmer channel (Stage 4 floor, `src/farmer_channel.py`)** — a one-sentence
  Arabic/English per-reach card; every clause a measured quantity, nothing
  attributed, no percentage when the gap is unreliable. Tested.
- **GeoLibre plugin (Stage 4, `geolibre_plugin/`)** — manifest, bilingual
  field-collection form mirroring `GroundObservation`, and a tested two-way
  bridge that scores each observation against the satellite record and accrues
  the reliability figure.
- **Offline assembly test (`src/mock_ee.py` + `tests/test_engine_assembly.py`)** —
  a deterministic mock Earth Engine backend that runs the FULL `analyse()`
  pipeline with no network, no auth and no quota. This tests the plumbing —
  signatures, reduceRegion keys, the nutrition/climate wiring, the command-area
  path, JSON shape — which the pure-logic tests cannot reach.

**Still genuinely unrun, and why.** The engine has never touched real imagery,
because that needs *your* authenticated `EE_PROJECT`. The mock backend proves the
pipeline is wired correctly; it does not and cannot prove the measurements are
physically correct, because it returns synthetic values, not satellite data. So
the honest status is: **logic tested, plumbing tested, measurements unvalidated.**
The measurements become real only when Stage 1 supplies traced New Halfa geometry
and you run the engine against a live scene — that first real run is where the
numbers, as opposed to the wiring, get their first test. Everything downstream
(the attribution model, the reliability figure) then needs the ground-observation
stream before its outputs are anything more than strong hypotheses.

## 8. Stage status at a glance

| Stage | State |
|---|---|
| 0 — honest engine | **Done, tested.** Fixes + integrity rules + provenance. |
| 1 — real New Halfa geometry | **Tooling delivered** (`geometry/build_water_frequency.py`); needs you to run it and trace. |
| 2 — manager dashboard | **Built, tested, screenshotted.** Runs against real results the moment Stage 1 + a live run exist. |
| 3 — attribution & validation | **Built, tested** on synthetic truth; needs the soil/crop/phenology layers and real fields to run for real. |
| 4 — ground layer + farmer channel | **Built, tested** (GeoLibre plugin + bridge + farmer card); needs deployment and real observations. |
| 5 — nutrition strips, rangeland, drone | Specified; not built (correctly gated on field work and permits). |
