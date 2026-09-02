# What has been validated, and what has not

This platform makes a lot of refusals. This file is the one place that says
what it does *not* refuse but probably should, and it is deliberately
unflattering.

**Nothing in this system has been validated against a Sudanese field.** Not one
figure has been checked against a measurement taken on the ground in Gezira, or
anywhere else in Sudan. Every number the tool produces is a satellite
measurement — real, calibrated by the agency that flew the instrument — passed
through a chain of thresholds and coefficients that were chosen here, and none
of those choices has been tested against reality.

That is not a reason to distrust the readings. It *is* the reason the yield is
locked, the nitrogen is locked, and no disease is ever named. The parts of the
platform that would need validation to be honest are the parts that are shut.

---

## The two hundred–odd numbers, and the seventy that admit it

`grep -c ARBITRARY src/*.py` returns **70**. Each of those is a constant that
controls what the tool says, was chosen by judgement, and says so at the point
of use:

| module | count | what they control |
|---|---:|---|
| `agronomy.py` | 11 | Kcb from NDVI, effective rainfall, interpolation gaps |
| `decision_logic.py` | 11 | thresholds, agreement verdicts, sufficiency bands |
| `agri_engine.py` | 10 | neighbourhood radius, scene minima, phenology |
| `engine.py` | 8 | command-area resolution, reach counts |
| `network.py` | 8 | continuity, siltation, head-to-tail |
| `rangeland.py` | 7 | neutrality, water-frequency bands |
| `nutrition_climate_ground.py` | 6 | chlorophyll bands, heat and GDD bases |
| the rest | 9 | coverage floors, overlap minima, noise floors |

Labelling is not validating. A number that says `ARBITRARY` is honest about its
provenance and still wrong if it is wrong.

---

## The ten that matter most

Ranked by how much of the output moves when they move.

### 1. `KCB_A` / `KCB_B` — Kcb from NDVI (`agronomy.py`)

Every water figure in the platform rests on these two. They come from published
relationships fitted in **other regions and other crops**, and they are clamped
because unclamped they go out of range on Sudanese canopy densities.

- **Falsified by:** a season of weighed irrigation deliveries against computed
  ETc on ten fields. If ETc is systematically 20% off delivery on a
  well-managed field, the coefficients are wrong for here.
- **Cost of being wrong:** the headline number of the whole water layer.

### 2. `K_SIGMA = 2.0` — the stress threshold (`agri_engine.py`)

A field is "needs attention" when its vigour falls two robust sigmas below its
neighbourhood. Two is a convention, not a finding. At 1.5 the tool flags more
fields; at 3.0 it flags almost none.

- **Falsified by:** 100 scouted fields scored healthy/stressed by a person,
  against the threshold's verdict. The right *k* is the one that maximises
  agreement.
- **Cost of being wrong:** every red field on the map, in both directions.

### 3. `NEIGHBOURHOOD_BUFFER_M = 3000` (`agri_engine.py`)

The reference population for a field's threshold. Three kilometres is wide
enough to contain other fields and narrow enough to stay in similar soil — an
assertion, untested.

- **Falsified by:** re-running one season at 1, 3 and 10 km and comparing the
  rankings. If the order of fields changes materially, the reference is doing
  more work than the measurement.

### 4. The disease weather windows (`disease.py`)

Twenty-odd temperature-and-wetness windows from published phytopathology,
**none validated against Sudanese disease surveys**. Leaf wetness — the
variable they actually want — is approximated by rain or by daily maximum
relative humidity, which is wrong on a windy night.

- **Falsified by:** one season of scouting records with dates, against the
  windows the model opened. The season scan already produces the dates to
  compare against.
- **Cost of being wrong:** sending somebody to scout in the wrong fortnight.
  Bounded, because the layer never names a disease as present.

### 5. `ANOMALY_K = 2.0`, `ANOMALY_MIN_FRACTION = 0.03` (`disease.py`)

Together they decide how often the tool says "walk to the north-east corner".
On the live run they produced patches of 0.01–0.02 ha, all below the floor —
so on that data the layer never spoke at all.

- **Falsified by:** walking to twenty flagged patches and recording what was
  there. A patch rate that finds nothing at 3% means the floor is too low, or
  the whole rung is noise.

### 6. `PHENOLOGY_GREENUP_FRACTION = 0.5` (`agri_engine.py`)

Green-up is the first crossing of half the seasonal amplitude. A convention
with no physical claim, and every derived date — season length, expected
harvest, and which half of the disease scan is "before peak" — moves with it.

- **Falsified by:** thirty recorded sowing dates against the computed green-up.

### 7. `GDD_BASE_C` and `HEAT_STRESS_C` (`nutrition_climate_ground.py`, `crops.py`)

FAO-56 and conventional published figures. **A heat threshold is as much a
variety property as a species one**, and Gezira varieties have been selected
under heat for a century — so the published 38 °C for sorghum is likely
conservative *here* in a way nobody has measured.

- **Falsified by:** a variety trial. This is the constant most likely to be
  wrong and least likely to be corrected without an agricultural research
  station.

### 8. `MIN_COVERAGE = 0.6` — the roll-up floor (`registry.py`)

Below 60% of a block's fields measured, the mean is withheld. Sixty is a
judgement about when a partial figure becomes misleading.

### 9. `NDVI_FLOOR = 0.03`, `SIGMA_K = 1.0` — what counts as change (`change.py`)

The line between "this field moved" and "this is the same field measured
twice". The floor comes from Sentinel-2 surface-reflectance repeatability over
a stable target, which is a published figure for the instrument and not for
this landscape.

### 10. `MIN_OVERLAP = 0.5` — comparable runs (`runs.py`)

Below half the field names shared, two runs are declared different farms. Never
tested against a real scheme where boundaries get redrawn between seasons.

---

## What has actually been checked

Honest list. It is short.

| | |
|---|---|
| **857 automated tests** | logic, refusals, translations, provenance. They test that the code does what it was written to do. |
| **84% statement coverage** | measured with `coverage`. The two CLI wrappers show 0% because they are exercised through subprocesses, which the tracer does not follow. |
| **Six live Earth Engine runs** | over four invented squares near Wad Medani. Found six defects the test suite could not — a missing database column, a null read as a failure, a false resume count, a window describing the dry season, a summary contradicting its own data, and an Arabic preposition. |
| **One agreement of model with reality** | the disease season scan, given only temperature, dewpoint and rainfall, located its wet window in **31 July – 20 August 2022** — the Gezira kharif. That says the model reads real weather. It says nothing about whether the thresholds are right. |

## What has never been checked

| | |
|---|---|
| **Any real field boundary** | the demonstration is four invented 40-hectare squares. |
| **Any ground measurement** | 0 scouting records, 0 leaf-nitrogen samples, 0 weighed harvests, 0 recorded operations. |
| **The satellite-versus-observer rate** | cannot exist until somebody scouts. It is the only figure here that would measure this platform's accuracy rather than claim it. |
| **The network engine** | 1,610 lines. It has never run on a real canal, because no canal centreline has been digitised. It runs in tests against a mock. |
| **Any of the 70 constants** | none has been tested against a Sudanese measurement. |

---

## The order to fix this in

1. **One season, twenty real tenancies, one extension officer.** Everything
   below is cheaper after this and guesswork before it.
2. **Thirty weighed harvests.** No code. A balance, a notebook, one season.
   Unlocks the only number a bank or a crop insurer can use.
3. **One hundred scouted fields with a healthy/stressed verdict.** Calibrates
   `K_SIGMA`, and starts the agreement rate.
4. **Twenty walks to flagged anomaly patches.** Tells you whether rung 1 is a
   finding or noise.
5. **One digitised Gezira minor canal** with vertex order and offtake, so the
   network engine stops being 1,610 untested lines.

Until (1) happens, this document is the most accurate description of the
platform's epistemic state, and every figure the tool shows should be read with
it in mind.
