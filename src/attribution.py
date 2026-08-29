"""
Attribution & validation harness (Stage 3) - turning a DETECTED head-to-tail gap
into a claim you can defend.

THE PROBLEM THIS EXISTS TO SOLVE
--------------------------------
engine.head_tail_equity DETECTS a gradient: crop vigour falls from head to tail,
with a confidence interval. Detection is not attribution. The same downward
gradient can be produced by things that have nothing to do with unfair water
distribution:

  - a SOIL gradient (heavier or saltier soil happening to lie at the tail)
  - a CROP-composition gradient (cotton at the head, fallow at the tail)
  - a PLANTING-DATE gradient (the tail plants later, so at any snapshot it is
    simply younger)

A tool that reports the raw gap as "distribution inequity" is wrong three ways.
This module earns the claim, or retracts it, by asking: how much of the gradient
SURVIVES once soil, crop and planting date are controlled for?

WHAT IS PURE (and tested here) vs WHAT NEEDS EARTH ENGINE
--------------------------------------------------------
The statistics - the stratified regression, the partial position effect, the
adjusted gap, the placebo permutation test, green-up extraction, multi-season
persistence - are pure numpy and are unit-tested in tests/test_attribution.py.
The layer FETCHING (SoilGrids soil class, a crop map, per-field NDVI time series
for green-up) is Earth-Engine-facing and lives at the bottom, clearly separated.
You assemble a table of per-field records with EE, then hand it to the pure
functions.

WHAT THIS STILL DOES NOT DO
---------------------------
Controlling for measured confounders is not proof of causation. Unmeasured
confounders remain possible, and "consistent with less water reaching the tail"
is the strongest honest statement even after everything here passes. The module
attributes a difference to POSITION-ALONG-THE-CANAL, never to any office,
operator or decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Optional, Sequence

try:
    import numpy as np
except ImportError:                       # numpy is required for this module only
    np = None

try:
    import ee
except ImportError:
    ee = None


# ==============================================================================
# CONFIGURATION - declared, arbitrary where arbitrary
# ==============================================================================

# A field is dropped from the model if it has no usable response value. ARBITRARY
# minimum sample below which a stratified fit is not attempted at all: with fewer
# fields than predictors + this margin the CI is meaningless.
MIN_FIELDS_FOR_FIT = 12

# Number of permutations in the placebo test. Higher = finer p-value resolution.
N_PERMUTATIONS = 2000

# Green-up is taken as the day the NDVI rise first crosses this fraction of the
# way from the season's baseline to its peak. 0.5 = the classic half-maximum.
# ARBITRARY but standard.
GREENUP_FRACTION = 0.5


# ==============================================================================
# RESULT TYPE
# ==============================================================================

@dataclass
class AttributionResult:
    status: str                                   # OK | INSUFFICIENT DATA | NOT AVAILABLE
    n_fields: int = 0
    raw_gap: Optional[float] = None               # from the univariate position slope
    adjusted_gap: Optional[float] = None          # after soil/crop/planting controls
    adjusted_gap_ci95: Optional[list] = None
    position_coef: Optional[float] = None
    position_coef_ci95: Optional[list] = None
    placebo_p_value: Optional[float] = None
    controls_used: list = dc_field(default_factory=list)
    r2: Optional[float] = None
    reason: Optional[str] = None
    interpretation: str = ""
    caveat: str = (
        "Controlling for measured soil, crop and planting date does not prove "
        "causation; unmeasured confounders remain possible. This attributes a "
        "difference to position along the canal, never to any office, operator "
        "or decision.")


# ==============================================================================
# DESIGN MATRIX
# ==============================================================================

def _one_hot(values: Sequence, drop_first: bool = True):
    """Categorical -> dummy columns. drop_first avoids the dummy-variable trap
    (perfect collinearity with the intercept). Returns (matrix, level_names)."""
    levels = sorted({v for v in values if v is not None and v != ""})
    if drop_first and levels:
        levels = levels[1:]                # reference level folded into intercept
    cols = []
    for lv in levels:
        cols.append([1.0 if v == lv else 0.0 for v in values])
    if not cols:
        return np.zeros((len(values), 0)), []
    return np.array(cols, dtype=float).T, list(levels)


def build_design(records: list, controls: Sequence[str]) -> Optional[dict]:
    """
    Build the OLS design matrix from per-field records.

    Each record is a dict:
        position   float in [0,1], 0 = head, 1 = tail        (required)
        response   float, e.g. season NDVI integral or peak  (required)
        soil_class categorical (optional control)
        crop       categorical (optional control)
        green_up   float, days from season start (optional control)

    `controls` selects which of soil_class / crop / green_up to include. Only
    controls that actually vary in the data are used - a constant column is
    dropped and reported, so the result never claims to have controlled for
    something that had no variation to control for.
    """
    if np is None:
        return None
    usable = [r for r in records
              if r.get("position") is not None and r.get("response") is not None]
    n = len(usable)
    if n < MIN_FIELDS_FOR_FIT:
        return {"ok": False, "n": n,
                "reason": f"{n} usable fields; {MIN_FIELDS_FOR_FIT} needed for a "
                          "stratified fit with a meaningful CI"}

    y = np.array([float(r["response"]) for r in usable])
    pos = np.array([float(r["position"]) for r in usable])
    cols = [np.ones(n), pos]                # intercept, position
    names = ["intercept", "position"]
    used_controls = []

    if "soil_class" in controls:
        m, lv = _one_hot([r.get("soil_class") for r in usable])
        if m.shape[1] > 0:
            cols.append(m); names += [f"soil={l}" for l in lv]
            used_controls.append("soil_class")
    if "crop" in controls:
        m, lv = _one_hot([r.get("crop") for r in usable])
        if m.shape[1] > 0:
            cols.append(m); names += [f"crop={l}" for l in lv]
            used_controls.append("crop")
    if "green_up" in controls:
        g = np.array([r.get("green_up") if r.get("green_up") is not None else np.nan
                      for r in usable])
        if np.isfinite(g).sum() >= 3 and np.nanstd(g) > 0:
            g = np.where(np.isfinite(g), g, np.nanmean(g))   # mean-impute gaps
            cols.append(g.reshape(-1, 1)); names.append("green_up")
            used_controls.append("green_up")

    X = np.column_stack(cols)
    return {"ok": True, "X": X, "y": y, "pos": pos, "names": names,
            "used_controls": used_controls, "n": n}


# ==============================================================================
# OLS with coefficient CIs
# ==============================================================================

def _ols(X, y):
    """OLS by normal equations, with per-coefficient standard errors from the
    residual variance and (X'X)^-1. Returns None if X'X is singular (perfectly
    collinear predictors) rather than emitting an unstable coefficient."""
    n, p = X.shape
    if n <= p:
        return None
    XtX = X.T @ X
    try:
        XtX_inv = np.linalg.inv(XtX)
    except np.linalg.LinAlgError:
        return None
    beta = XtX_inv @ X.T @ y
    resid = y - X @ beta
    dof = n - p
    sigma2 = (resid @ resid) / dof
    var_beta = sigma2 * np.diag(XtX_inv)
    se = np.sqrt(np.maximum(var_beta, 0))
    ss_res = resid @ resid
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {"beta": beta, "se": se, "dof": dof, "r2": r2}


def _t_crit(dof: int) -> float:
    """95% two-sided t critical value. Uses scipy if present, else a small table
    plus the 1.96 asymptote - accurate enough for a CI whose job is to say
    'excludes zero or not'."""
    try:
        from scipy import stats
        return float(stats.t.ppf(0.975, dof))
    except Exception:
        table = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45,
                 7: 2.36, 8: 2.31, 9: 2.26, 10: 2.23, 12: 2.18, 15: 2.13,
                 20: 2.09, 25: 2.06, 30: 2.04, 40: 2.02, 60: 2.00, 120: 1.98}
        keys = sorted(table)
        for k in keys:
            if dof <= k:
                return table[k]
        return 1.96


def fit_attribution(records: list,
                    controls: Sequence[str] = ("soil_class", "crop", "green_up"),
                    n_permutations: int = N_PERMUTATIONS,
                    seed: int = 12345) -> AttributionResult:
    """
    Fit the stratified model and report how much of the head-to-tail gap survives
    the controls, plus a placebo test that the position effect is not an artefact
    of the covariate structure.

    The adjusted gap is the predicted head->tail drop in the response, expressed
    as a fraction of the head value, holding the controls at their means. If it
    collapses toward zero once the controls are in, the raw gap was confounded -
    and saying so is a win, not a failure.
    """
    if np is None:
        return AttributionResult(status="NOT AVAILABLE",
                                 reason="numpy is required for attribution")

    design = build_design(records, controls)
    if design is None:
        return AttributionResult(status="NOT AVAILABLE",
                                 reason="numpy is required for attribution")
    if not design["ok"]:
        return AttributionResult(status="INSUFFICIENT DATA", n_fields=design["n"],
                                 reason=design["reason"])

    X, y, pos, names = design["X"], design["y"], design["pos"], design["names"]
    n = design["n"]

    # Raw (univariate) gap: response ~ position only.
    Xr = np.column_stack([np.ones(n), pos])
    raw = _ols(Xr, y)
    raw_gap = None
    if raw is not None:
        b0, b1 = raw["beta"]
        head, tail = b0, b0 + b1
        raw_gap = (head - tail) / head if head else None

    # Adjusted model.
    full = _ols(X, y)
    if full is None:
        return AttributionResult(
            status="NOT AVAILABLE", n_fields=n,
            controls_used=design["used_controls"],
            reason="design matrix is singular (collinear predictors); cannot fit")

    pos_idx = names.index("position")
    beta, se, dof = full["beta"], full["se"], full["dof"]
    tc = _t_crit(dof)
    pcoef = beta[pos_idx]
    pcoef_ci = [pcoef - tc * se[pos_idx], pcoef + tc * se[pos_idx]]

    # Adjusted gap: predicted head->tail drop holding controls at their means.
    # Head and tail differ ONLY by the position term (controls held at their
    # means), so ytail - yhead = position_coef and the adjusted gap is simply
    # -position_coef / yhead. The CI follows directly from the coefficient CI.
    # APPROXIMATION: this treats yhead (the fitted head value) as fixed. yhead
    # carries its own uncertainty and covaries with the position coefficient, so
    # the true CI is marginally wider. It is close enough for the survive/collapse
    # decision, not a substitute for a full delta-method interval.
    xbar = X.mean(axis=0)
    x_head = xbar.copy(); x_head[pos_idx] = 0.0
    yhead = float(x_head @ beta)
    adj_gap = (-pcoef / yhead) if yhead else None
    adj_gap_ci = sorted([-pcoef_ci[0] / yhead, -pcoef_ci[1] / yhead]) if yhead else None

    # Placebo permutation test: shuffle position, refit the FULL model, collect
    # the position coefficient. p = fraction of permuted |coef| >= observed.
    rng = np.random.default_rng(seed)
    perm_abs = []
    Xp = X.copy()
    for _ in range(n_permutations):
        Xp[:, pos_idx] = rng.permutation(pos)
        f = _ols(Xp, y)
        if f is not None:
            perm_abs.append(abs(f["beta"][pos_idx]))
    placebo_p = None
    if perm_abs:
        perm_abs = np.array(perm_abs)
        # A permutation p-value counts the observed statistic among the reference
        # set: (1 + #{perm >= obs}) / (1 + M). This can never be exactly 0, which
        # would be an impossible p-value; the floor is 1/(M+1).
        placebo_p = float((1 + (perm_abs >= abs(pcoef)).sum()) / (1 + len(perm_abs)))

    # Interpretation.
    survived = (adj_gap is not None and adj_gap_ci is not None
                and min(adj_gap_ci) > 0)
    if survived and (placebo_p is not None and placebo_p < 0.05):
        interp = ("The head-to-tail gap SURVIVES controlling for "
                  + ", ".join(design["used_controls"] or ["(no controls varied)"])
                  + ", and the position effect is not reproduced by chance "
                  f"(placebo p={placebo_p:.3f}). This is consistent with less "
                  "reaching the tail; corroborate with the canal-water signal "
                  "before treating it as a delivery finding.")
    elif adj_gap is not None and raw_gap is not None and abs(adj_gap) < 0.5 * abs(raw_gap):
        interp = (f"The raw gap ({raw_gap:.2f}) LARGELY COLLAPSES after controls "
                  f"(adjusted {adj_gap:.2f}). Most of the apparent gradient is "
                  "explained by soil, crop or planting date - not a network "
                  "finding.")
    else:
        interp = ("The gap is neither clearly confirmed nor clearly explained "
                  "away by the available controls; treat as unresolved and get "
                  "more fields or ground observations.")

    return AttributionResult(
        status="OK", n_fields=n,
        raw_gap=round(raw_gap, 4) if raw_gap is not None else None,
        adjusted_gap=round(adj_gap, 4) if adj_gap is not None else None,
        adjusted_gap_ci95=[round(c, 4) for c in adj_gap_ci] if adj_gap_ci else None,
        position_coef=round(float(pcoef), 5),
        position_coef_ci95=[round(float(c), 5) for c in pcoef_ci],
        placebo_p_value=round(placebo_p, 4) if placebo_p is not None else None,
        controls_used=design["used_controls"], r2=round(full["r2"], 4),
        interpretation=interp)


# ==============================================================================
# GREEN-UP DATE  (planting-date proxy from the NDVI time series)
# ==============================================================================

def green_up_day(days: Sequence[float], ndvi: Sequence[float],
                 fraction: float = GREENUP_FRACTION) -> Optional[float]:
    """
    Day (from season start) on which NDVI first crosses `fraction` of the way
    from the season baseline (min) to the season peak (max), on the rising limb.

    This is the discriminator between a planting-date artefact and a water
    problem: a field that merely planted late is time-SHIFTED (later green-up,
    same peak); a field short of water is DEPRESSED (on-time green-up, lower
    peak). Feeding green-up into the model as a control removes the planting-date
    explanation from the position effect.
    """
    pts = sorted((float(d), float(v)) for d, v in zip(days, ndvi) if v is not None)
    if len(pts) < 4:
        return None
    ds = [d for d, _ in pts]
    vs = [v for _, v in pts]
    vmin, vmax = min(vs), max(vs)
    if vmax - vmin < 1e-6:
        return None
    target = vmin + fraction * (vmax - vmin)
    peak_i = vs.index(vmax)
    if peak_i == 0:
        return None
    # walk the rising limb up to the peak; interpolate the first crossing.
    for i in range(1, peak_i + 1):
        if vs[i - 1] < target <= vs[i]:
            frac = (target - vs[i - 1]) / (vs[i] - vs[i - 1]) if vs[i] != vs[i - 1] else 0
            return round(ds[i - 1] + frac * (ds[i] - ds[i - 1]), 1)
    return round(ds[peak_i], 1)


# ==============================================================================
# MULTI-SEASON PERSISTENCE
# ==============================================================================

def persistence(seasonal_adjusted_gaps: list) -> dict:
    """
    Does the adjusted gap recur across seasons?

    Each item: {"season": int, "adjusted_gap": float, "ci95": [lo, hi]}.
    A structural cause (siltation, a bottleneck) recurs; a one-off does not.
    A gap that is significantly positive in most seasons is structural evidence;
    a gap that appears once is not a network finding.
    """
    seasons = [s for s in seasonal_adjusted_gaps
               if s.get("adjusted_gap") is not None]
    if len(seasons) < 2:
        return {"status": "INSUFFICIENT DATA",
                "reason": "need at least two seasons with an adjusted gap"}
    sig_pos = [s for s in seasons
               if s.get("ci95") and s["ci95"][0] > 0]
    same_sign = all(s["adjusted_gap"] > 0 for s in seasons) or \
        all(s["adjusted_gap"] < 0 for s in seasons)
    return {
        "status": "OK",
        "seasons_evaluated": len(seasons),
        "seasons_significant": len(sig_pos),
        "consistent_sign": bool(same_sign),
        "structural_evidence": bool(len(sig_pos) >= max(2, len(seasons) - 1)),
        "interpretation": (
            "A gap that is significantly positive in most seasons is consistent "
            "with a structural cause (which recurs); one that appears in a single "
            "season is not a network finding. This still does not separate a "
            "recurring soil effect from a recurring water effect - that is what "
            "the per-season controls and the canal-water corroboration are for."),
    }


def negative_control_ok(control_result: AttributionResult) -> dict:
    """
    A rain-fed / non-canal strip (or a canal believed to deliver evenly) run
    through the SAME machinery must show no gap. If it does, the pipeline has a
    bias and every real finding is suspect until it is found.
    """
    if control_result.status != "OK":
        return {"status": control_result.status,
                "reason": "control did not produce a fit"}
    gap = control_result.adjusted_gap
    ci = control_result.adjusted_gap_ci95
    bias = bool(ci and (ci[0] > 0 or ci[1] < 0))     # CI excludes zero on a control
    return {
        "status": "OK",
        "control_adjusted_gap": gap,
        "pipeline_bias_suspected": bias,
        "interpretation": ("A control strip should show a gap whose CI includes "
                           "zero. If it excludes zero, the pipeline is "
                           "manufacturing gradients and real findings must wait "
                           "until the bias is understood."),
    }


# ==============================================================================
# EARTH-ENGINE-FACING LAYER FETCH  (assemble the per-field records)
# ==============================================================================
# These build the inputs the pure functions above consume. They are NOT unit
# tested (they need a live Earth Engine session); keep them thin and obvious.

def soil_class_at(point, dataset: str = "OpenLandMap/SOL/SOL_TEXTURE-CLASS_USDA-TT_M/v02"):
    """USDA soil-texture class at a point, as a coarse soil stratifier. Coarse,
    but enough to stratify head vs tail. Returns an int class or None."""
    if ee is None:
        return None
    try:
        img = ee.Image(dataset).select("b0")
        v = img.reduceRegion(reducer=ee.Reducer.first(), geometry=point,
                             scale=250, maxPixels=1e6, bestEffort=True).getInfo()
        return v.get("b0")
    except Exception:
        return None


def ndvi_time_series(field_geom, start: str, end: str) -> dict:
    """Per-field NDVI time series for green-up extraction. Returns
    {days:[...], ndvi:[...]} with days measured from `start`. Pulls the whole
    series in one aggregate_array call, mirroring the climate module's pattern."""
    if ee is None:
        return {"days": [], "ndvi": []}
    try:
        from datetime import datetime as _dt
        s0 = _dt.strptime(start, "%Y-%m-%d")
        col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
               .filterBounds(field_geom).filterDate(start, end)
               .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40)))

        def to_feat(img):
            b = img.divide(10000)
            ndvi = b.normalizedDifference(["B8", "B4"])
            v = ndvi.reduceRegion(reducer=ee.Reducer.mean(), geometry=field_geom,
                                  scale=20, maxPixels=1e8, bestEffort=True).get("nd")
            return ee.Feature(None, {"t": img.date().millis(), "ndvi": v})

        fc = ee.FeatureCollection(col.map(to_feat))
        ts = fc.aggregate_array("t").getInfo()
        vs = fc.aggregate_array("ndvi").getInfo()
        days, ndvi = [], []
        for t, v in zip(ts, vs):
            if v is None:
                continue
            d = (_dt.utcfromtimestamp(t / 1000.0) - s0).days
            days.append(d); ndvi.append(v)
        return {"days": days, "ndvi": ndvi}
    except Exception:
        return {"days": [], "ndvi": []}
