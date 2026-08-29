"""
Pure decision logic for the Sudan Irrigation & Agriculture Monitor.

WHY THIS FILE EXISTS
--------------------
The scientific-integrity rules the platform promises - report NOT AVAILABLE
rather than zero, derive thresholds from the data, gate the nitrogen number
behind calibration, score only the clear satellite/ground cases - are decisions
about *what to report and what to withhold*. Those decisions are the thing most
worth testing, and they are impossible to test if they are tangled up with
Earth Engine calls that need a network and a project.

So every rule that can be expressed as arithmetic on numbers already pulled from
Earth Engine lives here, with NO dependency on `ee`. The engine modules call
these functions; the test-suite calls the same functions directly with crafted
inputs. When a test says "a model with 29 points must refuse to quote a
nitrogen figure", it is exercising the exact code path the live engine runs.

Nothing in here talks to a satellite. Everything in here is deterministic and
unit-testable.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Optional, Sequence


# ==============================================================================
# ROBUST, DATA-DERIVED THRESHOLDS  (integrity rule 2)
# ==============================================================================

def robust_sigma(p16: float, p84: float) -> float:
    """
    Spread estimator that the extreme values in an index image cannot drag
    around: half the 16-84 percentile span. For a Gaussian this equals the
    standard deviation, but unlike the standard deviation it is barely moved by
    the handful of cloud, shadow or water pixels that always survive into an
    index distribution.
    """
    return (p84 - p16) / 2.0


def robust_threshold(p16: float, p50: float, p84: float,
                     k_sigma: float, low_tail: bool) -> Optional[float]:
    """
    median +/- k * robust_sigma, computed per command area per run.

    low_tail=True gives a stress threshold (median - k*sigma): a field is a
    candidate for concern when it sits in the low tail of its OWN scheme, not
    below some number carried over from another scheme.

    Returns None when any percentile is missing, so the caller reports
    NOT AVAILABLE rather than inventing a threshold.

    NOTE: this is the correct tool for flagging an outlier field against a
    roughly unimodal neighbourhood. It is the WRONG tool for splitting a
    bimodal cropped-vs-bare distribution into two classes - use otsu_threshold
    for that. Mixing the two up is defect #2 in the original engine.
    """
    if None in (p16, p50, p84):
        return None
    sigma = robust_sigma(p16, p84)
    return p50 - k_sigma * sigma if low_tail else p50 + k_sigma * sigma


# ==============================================================================
# OTSU: the honest way to split a bimodal cropped/bare distribution
# ==============================================================================

def otsu_threshold(hist_counts: Sequence[float],
                   bin_mids: Sequence[float]) -> dict:
    """
    Otsu's method: the threshold that maximises between-class variance in a
    histogram. This is what "irrigated extent" actually needs, because a command
    area is a MIXTURE of two populations - cropped land with high NDVI and bare
    soil / fallow with low NDVI - not one population with noise around a median.

    median + k*sigma assumes the second thing and mis-classifies the first; over
    a desert-fringe command area it typically calls almost nothing cropped. Otsu
    finds the valley between the two humps instead.

    Returns:
      threshold        the split point (a bin mid), or None
      separability     eta = between-class variance / total variance, in [0,1].
                       Reported for information, but NOT used as the bimodality
                       test: it stays high even on a single symmetric hump.
      bimodality       valley depth between the two class peaks, in [0,1]. This
                       is the honest quality flag: ~1 = two clean humps, ~0 = one
                       hump and the split is arbitrary.
      is_bimodal       bimodality >= 0.5, a hand-chosen, declared cut-off
      basis            provenance string
    """
    counts = [float(c) for c in hist_counts]
    mids = [float(m) for m in bin_mids]
    total = sum(counts)
    if total <= 0 or len(counts) < 2:
        return {"threshold": None, "separability": None, "is_bimodal": False,
                "basis": "NOT AVAILABLE: empty or degenerate histogram"}

    # Global mean and total variance.
    grand_mean = sum(c * m for c, m in zip(counts, mids)) / total
    total_var = sum(c * (m - grand_mean) ** 2 for c, m in zip(counts, mids)) / total
    if total_var == 0:
        return {"threshold": None, "separability": None, "is_bimodal": False,
                "basis": "NOT AVAILABLE: histogram has no variance"}

    # NOTE: `is_bimodal` is set from the valley-depth `bimodality` below, NOT
    # from `separability` - separability stays high on a single hump, so it
    # cannot be the bimodality test (see the comment where bimodality is set).
    best_thr, best_between, best_i = None, -1.0, None
    w0 = 0.0            # cumulative weight of class 0 (below threshold)
    sum0 = 0.0         # cumulative weighted value of class 0
    for i in range(len(counts) - 1):
        w0 += counts[i]
        sum0 += counts[i] * mids[i]
        w1 = total - w0
        if w0 == 0 or w1 == 0:
            continue
        m0 = sum0 / w0
        m1 = (grand_mean * total - sum0) / w1
        between = (w0 / total) * (w1 / total) * (m0 - m1) ** 2
        if between > best_between:
            best_between, best_thr, best_i = between, (mids[i] + mids[i + 1]) / 2.0, i

    separability = best_between / total_var if total_var else 0.0

    # Separability alone does NOT distinguish a genuine two-hump distribution
    # from a single symmetric hump: splitting a unimodal bell at its mean still
    # explains a large share of the variance, so eta stays high. To decide
    # whether the extent figure can be trusted we need the depth of the VALLEY
    # between the two class peaks. A real cropped/bare mixture has a deep dip
    # between the humps; a unimodal distribution has its own peak sitting right
    # at the split, so there is no dip.
    bimodality = 0.0
    if best_i is not None:
        lower, upper = counts[:best_i + 1], counts[best_i + 1:]
        if lower and upper:
            i0 = max(range(len(lower)), key=lambda j: lower[j])
            i1u = max(range(len(upper)), key=lambda j: upper[j])
            i1 = best_i + 1 + i1u
            peak0, peak1 = lower[i0], upper[i1u]
            valley = min(counts[i0:i1 + 1]) if i1 >= i0 else min(peak0, peak1)
            shallower = min(peak0, peak1)
            bimodality = (1 - valley / shallower) if shallower > 0 else 0.0

    return {
        "threshold": round(best_thr, 4) if best_thr is not None else None,
        "separability": round(separability, 4),
        "bimodality": round(bimodality, 4),
        "is_bimodal": bool(bimodality >= 0.5),
        "basis": ("DERIVED: Otsu split of the NDVI histogram over this command "
                  "area. `bimodality` is the depth of the valley between the two "
                  "class peaks (0 = one hump, ~1 = two clean humps); when it is "
                  "low the cropped and bare populations are not separable and "
                  "the extent figure is weak and must be surfaced as such. "
                  "The 0.5 cut is ARBITRARY."),
    }


# ==============================================================================
# HEAD-TO-TAIL EQUITY  (the flagship output - integrity rule 5)
# ==============================================================================

@dataclass
class SlopeFit:
    slope: float
    intercept: float
    r2: float
    n: int
    head_fit: float
    tail_fit: float
    gap: float                       # (head_fit - tail_fit) / head_fit
    gap_ci_low: float
    gap_ci_high: float


def fit_head_tail_slope(positions: Sequence[float],
                        values: Sequence[float],
                        n_boot: int = 2000,
                        seed: int = 12345) -> Optional[SlopeFit]:
    """
    Fit crop vigour against normalised position along the canal (0 = head,
    1 = tail) by ordinary least squares, and put a confidence interval on the
    head-to-tail gap by bootstrapping the reaches.

    WHY A SLOPE FIT AND NOT A TWO-POINT DIFFERENCE
    The original engine reported (first reach - last reach) / first reach as
    "the head-tail gap". Two points cannot tell a real gradient from noise: one
    unlucky cloud-contaminated reach at either end swings the whole figure, and
    there is no way to say whether the gap is distinguishable from zero. A line
    through ALL the reaches uses every reach, and the bootstrap says how sure we
    are. A manager should be handed "40% lower at the tail, and we are confident
    it is at least 25% lower" - not a bare 40% that might be an artefact of one
    bad pixel.

    WHAT IT STILL DOES NOT DO
    A downward slope is a measured difference in outcome along the canal. It is
    NOT proof that the network under-delivered water to the tail. Soil, crop
    choice, planting date and upstream abstraction can each produce the same
    gradient. Separating those is the job of the validation design, not of this
    function. This function measures; it attributes nothing.

    Returns None if fewer than three reaches carry a value (a line through two
    points has no residual and no meaningful CI).
    """
    pts = [(float(p), float(v)) for p, v in zip(positions, values) if v is not None]
    n = len(pts)
    if n < 3:
        return None
    xs = [p for p, _ in pts]
    ys = [v for _, v in pts]

    def ols(xs_, ys_):
        m = len(xs_)
        mx = sum(xs_) / m
        my = sum(ys_) / m
        sxx = sum((x - mx) ** 2 for x in xs_)
        if sxx == 0:
            return None
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs_, ys_))
        slope = sxy / sxx
        intercept = my - slope * mx
        return slope, intercept

    fit = ols(xs, ys)
    if fit is None:
        return None
    slope, intercept = fit
    preds = [slope * x + intercept for x in xs]
    my = sum(ys) / n
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, preds))
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0

    # Fitted endpoints define the gap. Using the fitted line rather than the raw
    # end reaches is deliberate: it is less sensitive to one noisy terminal reach.
    head_fit = intercept + slope * 0.0
    tail_fit = intercept + slope * 1.0

    def gap_of(hf, tf):
        return (hf - tf) / hf if hf else float("nan")

    gap = gap_of(head_fit, tail_fit)

    # Bootstrap the reaches (resample pairs with replacement) to bound the gap.
    rng = random.Random(seed)
    gaps = []
    for _ in range(n_boot):
        sample = [rng.randrange(n) for _ in range(n)]
        bx = [xs[i] for i in sample]
        by = [ys[i] for i in sample]
        bf = ols(bx, by)
        if bf is None:
            continue
        bs, bi = bf
        hf = bi
        tf = bi + bs
        if hf:
            gaps.append(gap_of(hf, tf))
    if len(gaps) >= 20:
        gaps.sort()
        lo = gaps[int(0.025 * len(gaps))]
        hi = gaps[int(0.975 * len(gaps)) - 1]
    else:
        lo = hi = float("nan")

    return SlopeFit(
        slope=round(slope, 5), intercept=round(intercept, 5), r2=round(r2, 4),
        n=n, head_fit=round(head_fit, 4), tail_fit=round(tail_fit, 4),
        gap=round(gap, 4),
        gap_ci_low=round(lo, 4) if not math.isnan(lo) else None,
        gap_ci_high=round(hi, 4) if not math.isnan(hi) else None)


# Below this fitted head NDVI, the head-to-tail gap is expressed as a fraction of
# a near-zero denominator and becomes meaningless (a tiny head value makes any
# ratio explode to absurd magnitudes). A head this low means there is essentially
# no crop at the head, so "X% below the head" is not a sensible statement.
# ARBITRARY: 0.10 NDVI is roughly bare-soil / sparse-cover.
HEAD_NDVI_FLOOR = 0.10


def equity_flag(fit: Optional[SlopeFit], flag_threshold: float,
                head_floor: float = HEAD_NDVI_FLOOR) -> dict:
    """
    Decide whether a canal is flagged for a manager's attention.

    The flag fires only when the LOWER bound of the bootstrap gap exceeds the
    threshold - i.e. when we are confident (95%) that the tail is at least
    `flag_threshold` below the head. Flagging on the point estimate alone would
    hand managers a list padded with canals whose "gap" is really sampling
    noise, and every false alarm erodes trust in the ones that are real.

    When the fitted head vigour is below `head_floor`, the gap ratio has a
    near-zero denominator and is not a meaningful percentage; it is reported as
    unreliable and never flagged.
    """
    if fit is None:
        return {"status": "INSUFFICIENT DATA",
                "reason": "fewer than three reaches produced a usable value",
                "flagged": False}
    if fit.head_fit is not None and fit.head_fit < head_floor:
        return {
            "status": "OK",
            "flagged": False,
            "gap_reliable": False,
            "gap_point_estimate": fit.gap,
            "gap_ci": [fit.gap_ci_low, fit.gap_ci_high],
            "slope": fit.slope,
            "r2": fit.r2,
            "n_reaches": fit.n,
            "flag_threshold": flag_threshold,
            "reason": (f"fitted head vigour {fit.head_fit} is below the "
                       f"{head_floor} NDVI floor, so the head-to-tail ratio has a "
                       "near-zero denominator and is not a meaningful percentage; "
                       "reported as unreliable and not flagged"),
            "attribution_caveat": ("A gap is a measured difference in outcome and "
                                   "attributes nothing to any office, operator or "
                                   "decision."),
        }
    confident = (fit.gap_ci_low is not None and fit.gap_ci_low > flag_threshold)
    return {
        "status": "OK",
        "flagged": bool(confident),
        "gap_reliable": True,
        "gap_point_estimate": fit.gap,
        "gap_ci": [fit.gap_ci_low, fit.gap_ci_high],
        "slope": fit.slope,
        "r2": fit.r2,
        "n_reaches": fit.n,
        "flag_threshold": flag_threshold,
        "flag_rule": ("FLAGGED only when the 95% lower bound of the head-to-tail "
                      "gap exceeds the threshold, so a flag means a gradient we "
                      "are confident is real, not a noisy point estimate."),
        "flag_basis": ("ARBITRARY: the threshold controls how many canals a "
                       "manager is asked to review and carries no regulatory "
                       "meaning."),
        "attribution_caveat": ("A gap is a measured difference in outcome. "
                               "Siltation, upstream abstraction, soil, crop "
                               "choice and planting date can each cause it; this "
                               "figure separates none of them and attributes "
                               "nothing to any office, operator or decision."),
    }


# ==============================================================================
# CANAL DIRECTION  (integrity rules 1 and 6 - the SIGN of the gap is an input,
#                   not a measurement, and must never be assumed silently)
# ==============================================================================

# Two endpoints count as equally far from the offtake - i.e. the offtake cannot
# decide which end is the head - when the nearer distance exceeds this fraction
# of the farther one. ARBITRARY: 0.8 is a starting point; tighten it once real
# offtake coordinates and canal lengths are in hand.
OFFTAKE_AMBIGUITY_RATIO = 0.8

_VERTEX_ORDERS = ("head_first", "tail_first")


def _approx_metres(a: Sequence[float], b: Sequence[float]) -> float:
    """
    Equirectangular distance in metres. Accurate enough to say which of two
    endpoints of one canal is nearer a point a few kilometres away; it is not a
    geodesic and never appears in a reported figure.
    """
    lon1, lat1 = float(a[0]), float(a[1])
    lon2, lat2 = float(b[0]), float(b[1])
    mlat = math.radians((lat1 + lat2) / 2.0)
    dx = math.radians(lon2 - lon1) * math.cos(mlat)
    dy = math.radians(lat2 - lat1)
    return math.hypot(dx, dy) * 6371000.0


def resolve_canal_direction(coords: Sequence[Sequence[float]],
                            declared: Optional[str] = None,
                            offtake: Optional[Sequence[float]] = None,
                            ambiguity_ratio: float = OFFTAKE_AMBIGUITY_RATIO
                            ) -> dict:
    """
    Decide which end of a canal LineString is the HEAD.

    WHY THIS EXISTS
    The head-to-tail gap is signed. Reverse the vertex order of the same canal
    and the same imagery yields the same magnitude with the opposite sign: a
    tail-starved canal is reported as head-starved, and a manager is pointed at
    the wrong end of the network with full statistical confidence behind the
    error. Nothing about a LineString records which end the water enters. A file
    digitised over imagery, traced from a water-frequency raster, or pulled from
    OpenStreetMap carries whatever direction the person drawing it happened to
    move their mouse in.

    So direction is an INPUT, and this function makes the engine say where it
    came from:

      DECLARED  a `vertex_order` property on the canal feature, set by someone
                who knows the scheme. Trusted, and recorded as a human statement.
      TOPOLOGY  an `offtake` [lon, lat] - the junction with the parent canal.
                The endpoint nearer the offtake is the head. Trusted, and
                reproducible from geometry alone.
      ASSUMED   neither supplied. Vertex order is used because there is nothing
                else, and `verified` is False so every figure downstream carries
                the admission.

    Elevation deliberately plays no part. In a scheme as flat as Gezira the
    head-to-tail fall is of the order of centimetres per kilometre, far inside
    SRTM's vertical noise, so a DEM cannot separate the ends and would only
    dress an assumption up as a measurement.

    When `declared` and `offtake` disagree, this returns NOT AVAILABLE rather
    than picking one. A conflict means one of the two inputs is wrong, and which
    one is not something arithmetic can settle.
    """
    if coords is None or len(coords) < 2:
        return {"status": "NOT AVAILABLE", "verified": False, "reverse": False,
                "basis": None,
                "reason": "canal geometry has too few vertices to have a direction"}

    declared_norm = None
    if declared is not None:
        declared_norm = str(declared).strip().lower().replace("-", "_")
        if declared_norm not in _VERTEX_ORDERS:
            return {"status": "NOT AVAILABLE", "verified": False, "reverse": False,
                    "basis": None,
                    "reason": (f"vertex_order property is {declared!r}; expected "
                               f"one of {list(_VERTEX_ORDERS)}")}

    topo_norm = None
    topo_detail = None
    if offtake is not None:
        d_start = _approx_metres(coords[0], offtake)
        d_end = _approx_metres(coords[-1], offtake)
        near, far = min(d_start, d_end), max(d_start, d_end)
        topo_detail = {"offtake_to_first_vertex_m": round(d_start, 1),
                       "offtake_to_last_vertex_m": round(d_end, 1)}
        if far == 0 or near > ambiguity_ratio * far:
            return {"status": "NOT AVAILABLE", "verified": False, "reverse": False,
                    "basis": None, "offtake_distances": topo_detail,
                    "reason": (f"both endpoints are a similar distance from the "
                               f"offtake ({round(d_start, 1)} m and "
                               f"{round(d_end, 1)} m, ratio above the "
                               f"{ambiguity_ratio} ambiguity cut), so the offtake "
                               "cannot say which end is the head"),
                    "ambiguity_ratio_basis": (
                        "ARBITRARY: the cut only controls when the topological "
                        "test admits it cannot decide.")}
        topo_norm = "head_first" if d_start < d_end else "tail_first"

    if declared_norm and topo_norm and declared_norm != topo_norm:
        return {"status": "NOT AVAILABLE", "verified": False, "reverse": False,
                "basis": None, "offtake_distances": topo_detail,
                "reason": (f"declared vertex_order {declared_norm!r} conflicts "
                           f"with the offtake, which puts the head at the "
                           f"{'first' if topo_norm == 'head_first' else 'last'} "
                           "vertex; one of the two inputs is wrong and this is "
                           "not resolved automatically")}

    if declared_norm:
        orientation, basis, verified = declared_norm, "DECLARED: vertex_order property", True
    elif topo_norm:
        orientation, verified = topo_norm, True
        basis = "TOPOLOGY: endpoint nearest the supplied offtake is the head"
    else:
        orientation, verified = "head_first", False
        basis = ("ASSUMED: no vertex_order property and no offtake supplied, so "
                 "the first vertex is taken as the head. The SIGN of the "
                 "head-to-tail gap rests on this assumption and is unverified.")

    out = {"status": "OK", "orientation": orientation, "verified": verified,
           "reverse": orientation == "tail_first", "basis": basis}
    if topo_detail:
        out["offtake_distances"] = topo_detail
    return out


# ==============================================================================
# FIELD REFERENCE AREA  (integrity rule 1 - a threshold that can never fire is
#                        not a threshold, and must not be presented as one)
# ==============================================================================

# A field's stress threshold is derived from a surrounding population. That
# population must be substantially larger than the field, or the field dominates
# its own reference distribution and (median - k*sigma) sits below the field's
# own mean by construction - a verdict of "not stressed" that was guaranteed
# before any pixel was read.
# ARBITRARY: 10x is a starting point. The defensible value depends on how many
# independent fields the reference actually contains, which is a question for
# real command-area geometry, not for a constant.
MIN_REFERENCE_AREA_RATIO = 10.0

_EARTH_R_M = 6371000.0


def _ring_area_m2(ring: Sequence[Sequence[float]]) -> float:
    """
    Shoelace area of a lon/lat ring, with longitudes scaled by cos(mean latitude).

    This is an equal-area approximation good to a per cent or so over a field or
    a command area at Sudanese latitudes. It exists only to compare two areas
    with each other; it is never reported as a measurement of anything.
    """
    if not ring or len(ring) < 3:
        return 0.0
    lats = [float(p[1]) for p in ring]
    cos_lat = math.cos(math.radians(sum(lats) / len(lats)))
    pts = [(math.radians(float(p[0])) * cos_lat, math.radians(float(p[1])))
           for p in ring]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    s = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0 * _EARTH_R_M ** 2


def geojson_area_m2(geometry: Optional[dict]) -> Optional[float]:
    """Area of a GeoJSON Polygon or MultiPolygon, holes subtracted. None if the
    geometry is not an area type - a LineString has no area and must not be
    silently treated as having one."""
    if not geometry:
        return None
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return None
    if gtype == "Polygon":
        polys = [coords]
    elif gtype == "MultiPolygon":
        polys = coords
    else:
        return None
    total = 0.0
    for rings in polys:
        if not rings:
            continue
        total += _ring_area_m2(rings[0])
        for hole in rings[1:]:
            total -= _ring_area_m2(hole)
    return total


def _point_in_ring(pt: Sequence[float], ring: Sequence[Sequence[float]]) -> bool:
    """Ray-casting test. Points exactly on an edge are not guaranteed either way,
    which does not matter here: a field sitting exactly on a command boundary is
    ambiguous in reality too, and the caller falls through to a wider reference."""
    x, y = float(pt[0]), float(pt[1])
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = float(ring[i][0]), float(ring[i][1])
        x2, y2 = float(ring[(i + 1) % n][0]), float(ring[(i + 1) % n][1])
        if (y1 > y) != (y2 > y):
            if y2 != y1 and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                inside = not inside
    return inside


def point_in_geometry(pt: Sequence[float], geometry: Optional[dict]) -> bool:
    """True when the point falls inside a GeoJSON Polygon / MultiPolygon."""
    if not geometry or not pt:
        return False
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return False
    polys = [coords] if gtype == "Polygon" else coords if gtype == "MultiPolygon" else []
    for rings in polys:
        if not rings:
            continue
        if _point_in_ring(pt, rings[0]) and not any(
                _point_in_ring(pt, h) for h in rings[1:]):
            return True
    return False


def geojson_centroid(geometry: Optional[dict]) -> Optional[list]:
    """Mean of the outer-ring vertices. Adequate for asking which command area a
    field sits in; not a centre of mass and not reported."""
    if not geometry:
        return None
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return None
    if gtype == "Polygon":
        ring = coords[0]
    elif gtype == "MultiPolygon":
        ring = coords[0][0]
    elif gtype == "LineString":
        ring = coords
    else:
        return None
    if not ring:
        return None
    return [sum(float(p[0]) for p in ring) / len(ring),
            sum(float(p[1]) for p in ring) / len(ring)]


def reference_adequate(field_area_m2: Optional[float],
                       reference_area_m2: Optional[float],
                       min_ratio: float = MIN_REFERENCE_AREA_RATIO) -> dict:
    """
    Decide whether a candidate reference area is a wide enough population to
    derive a field's stress threshold from.

    WHY THIS IS A DECISION AND NOT A DETAIL
    field_condition derives its threshold as (median - k*sigma) over the
    reference. If the reference is the field, or barely larger than it, the
    field's own pixels set the median, and its mean cannot fall two robust sigma
    below a distribution it dominates. The verdict "not stressed" is then an
    artefact of the geometry, not an observation - and it is indistinguishable in
    the output from a real one. So an inadequate reference is refused here, and
    the caller reports the values with NO verdict rather than a verdict that was
    decided in advance.
    """
    if field_area_m2 is None or reference_area_m2 is None:
        return {"ok": False, "ratio": None,
                "reason": "field or reference area could not be computed",
                "basis": f"ARBITRARY: reference must exceed the field by {min_ratio}x"}
    if field_area_m2 <= 0:
        return {"ok": False, "ratio": None,
                "reason": "field geometry has zero area",
                "basis": f"ARBITRARY: reference must exceed the field by {min_ratio}x"}
    ratio = reference_area_m2 / field_area_m2
    ok = ratio >= min_ratio
    return {
        "ok": bool(ok),
        "ratio": round(ratio, 2),
        "min_ratio": min_ratio,
        "reason": None if ok else (
            f"reference area is only {round(ratio, 2)}x the field, below the "
            f"{min_ratio}x minimum; a threshold derived from it would be set by "
            "the field's own pixels and could never flag the field"),
        "basis": (f"ARBITRARY: {min_ratio}x. The defensible value depends on how "
                  "many independent fields the reference contains."),
    }


# ==============================================================================
# FIELD STRESS READING  (integrity rule 3 - the cause-separating decision)
# ==============================================================================

def stress_reading(value: Optional[float], threshold: Optional[float],
                   rain_mm: Optional[float], rain_floor_mm: float) -> dict:
    """
    Decide whether a field is stressed and, if so, whether rainfall can explain
    it - the drought-vs-network separation that integrity rule 3 requires.

    CRUCIAL: `threshold` must be derived from a REFERENCE population (the command
    area / neighbourhood), and `value` is the field's mean. Comparing a field
    against its OWN low tail is the bug this function's callers must avoid - a
    field's mean is never below its own (median - 2 sigma), so stress would never
    fire. Here the field mean is compared against the neighbourhood's low tail:
    "is this field an outlier-low within its command area?"

    Returns NOT AVAILABLE (no verdict) if the threshold or the rainfall context
    is missing - a stress reading without rainfall context is never offered.
    """
    if value is None or threshold is None:
        return {"status": "NOT AVAILABLE",
                "reason": "no reference threshold, so no relative stress verdict",
                "stressed": None}
    if rain_mm is None:
        return {"status": "NOT AVAILABLE",
                "reason": "no rainfall context, so no stress interpretation is "
                          "offered (integrity rule 3)",
                "stressed": None}
    stressed = value < threshold
    if stressed and rain_mm < rain_floor_mm:
        reading = ("STRESS WITH LITTLE RAIN - consistent with drought OR a supply "
                   "failure; not separable from this data alone.")
    elif stressed:
        reading = ("STRESS DESPITE RAIN - drought is a poor explanation, so "
                   "supply, drainage, salinity or crop management are where to "
                   "look.")
    else:
        reading = "No stress signal against the command-area reference threshold."
    return {"status": "OK", "stressed": bool(stressed), "reading": reading}


# ==============================================================================
# DRY SPELLS  (climate - pure part, ee part lives in the climate module)
# ==============================================================================

def longest_dry_run(daily_mm: Sequence[float], rain_floor_mm: float) -> int:
    """
    Longest run of consecutive days below the rain floor.

    A season can deliver a normal total and still fail if it arrives in two
    downpours three weeks apart; the total hides that and this does not. Pure
    arithmetic on a list of daily values, so it is trivially testable and the
    Earth Engine layer only has to hand it the daily series.
    """
    longest = run = 0
    for v in daily_mm:
        run = run + 1 if (v is not None and v < rain_floor_mm) else 0
        longest = max(longest, run)
    return longest


# ==============================================================================
# SEASON vs HISTORY  (climate)
# ==============================================================================

def season_percentile_verdict(this_season_mm: float,
                              history_mm: Sequence[float]) -> Optional[dict]:
    """
    Where this season's rainfall sits in the site's own recent history.

    Absolute millimetres are close to meaningless without local context: the
    same 200 mm is generous in one place and a failure in another. Needs at
    least three historical years to say anything; returns None otherwise rather
    than pretending a percentile computed from two numbers means something.
    """
    hist = [h for h in history_mm if h is not None]
    if this_season_mm is None or len(hist) < 3:
        return None
    below = sum(1 for h in hist if h < this_season_mm)
    pct = 100.0 * below / len(hist)
    if pct < 20:
        verdict = "MUCH DRIER than this site's recent seasons"
    elif pct < 40:
        verdict = "drier than usual"
    elif pct > 80:
        verdict = "MUCH WETTER than usual"
    elif pct > 60:
        verdict = "wetter than usual"
    else:
        verdict = "near this site's normal"
    return {
        "this_season_mm": round(this_season_mm, 1),
        "historical_mean_mm": round(sum(hist) / len(hist), 1),
        "historical_min_mm": round(min(hist), 1),
        "historical_max_mm": round(max(hist), 1),
        "percentile": round(pct, 1),
        "years_compared": len(hist),
        "verdict": verdict,
    }


# ==============================================================================
# NITROGEN LADDER  (integrity rule 4)
# ==============================================================================

def sufficiency_reading(field_value: float, reference_value: float,
                        deficient_cut: float, marginal_cut: float) -> dict:
    """
    Level 2 of the nitrogen ladder: field vs its own over-fertilised reference
    strip. The strip is nitrogen-saturated by construction, so variety, growth
    stage and soil are shared by both and cancel - which is exactly why this
    needs no laboratory. The cut-offs are the conventional reference-strip
    values and are declared ARBITRARY until local yield-response data replaces
    them.
    """
    if not reference_value:
        return {"status": "NOT AVAILABLE",
                "reason": "reference strip has no usable value"}
    si = field_value / reference_value
    if si < deficient_cut:
        reading = ("LIKELY DEFICIENT - well below the field's own "
                   "nitrogen-saturated strip")
    elif si < marginal_cut:
        reading = "MARGINAL - worth watching over the next fortnight"
    else:
        reading = "SUFFICIENT against the reference strip"
    return {"status": "OK", "sufficiency_index": round(si, 3),
            "reading": reading,
            "cutoffs": {"deficient": deficient_cut, "marginal": marginal_cut},
            "basis": "ARBITRARY: conventional reference-strip cut-offs, not "
                     "derived from Sudanese yield-response data"}


def calibration_gate(n_points: int, rmse: Optional[float],
                     min_points: int, max_rmse: float,
                     quantity: str = "an absolute nitrogen figure",
                     unit: str = "% leaf N") -> dict:
    """
    Level 3 gate: may we quote an absolute calibrated number at all?

    `quantity` and `unit` only change the wording of the refusal. The gate is
    shared deliberately: nitrogen and yield fail for the same reason - a
    canopy measurement extrapolated through an unfitted relationship - so they
    are refused by the same code rather than by two rules that could drift
    apart.

    Two independent reasons to refuse, and the caller must fall back to the
    relative statement in either case:
      - too few calibration points (a linear fit on a handful of samples reports
        a flatteringly small error that will not survive contact with new data)
      - a fitted model whose RMSE exceeds the stated limit (a number we cannot
        stand behind is worse than an honest "not measured")

    A calibrated estimate quoted WITH its RMSE is a stronger claim than an
    uncalibrated index dressed up as nitrogen, not a weaker one. This gate is
    what keeps the strong version honest.
    """
    if n_points is None or n_points < min_points:
        return {"may_quote": False, "reason": (
            f"{n_points} calibration points; {min_points} needed before "
            f"{quantity} can be quoted")}
    if rmse is None:
        return {"may_quote": False, "reason": "model not yet fitted"}
    if rmse > max_rmse:
        return {"may_quote": False, "reason": (
            f"model RMSE {rmse:.2f}{unit} exceeds the {max_rmse}{unit} limit; "
            "the relative statement is reported instead")}
    return {"may_quote": True,
            "reason": f"n={n_points} >= {min_points} and RMSE {rmse:.2f}{unit} "
                      f"<= {max_rmse}{unit}"}


def relative_condition(value: float, scheme_p25: float,
                       scheme_p75: float) -> dict:
    """
    Level 1, always available: rank a field against the scheme rather than
    against an absolute scale. Ranking is the honest form of the measurement
    when no calibration exists, and it is what a manager acts on anyway - which
    fields to visit first, not the leaf nitrogen percentage.
    """
    if scheme_p25 is None or scheme_p75 is None or scheme_p25 == scheme_p75:
        return {"status": "NOT AVAILABLE",
                "reason": "scheme percentile spread unavailable"}
    if value < scheme_p25:
        return {"status": "OK", "condition": "BELOW SCHEME NORM", "band": 25.0}
    if value > scheme_p75:
        return {"status": "OK", "condition": "ABOVE SCHEME NORM", "band": 75.0}
    return {"status": "OK", "condition": "WITHIN SCHEME NORM", "band": 50.0}


# ==============================================================================
# SATELLITE vs GROUND AGREEMENT  (integrity rule 8)
# ==============================================================================

def agreement_verdict(satellite_ndvi: Optional[float],
                      canopy_condition: Optional[str],
                      scheme_p25: Optional[float]) -> str:
    """
    Did the satellite see what the observer saw?

    Only the clear cases are scored. Where either side is missing or ambiguous
    the result is UNCLEAR, never a guess - because a forced verdict corrupts the
    reliability figure this whole layer exists to produce. This is the one
    number in the platform that describes the platform's own accuracy, so it
    must be built only from cases where both sides actually said something.
    """
    if satellite_ndvi is None or not canopy_condition or scheme_p25 is None:
        return "UNCLEAR"
    poor_terms = {"yellowing", "wilting", "patchy"}
    healthy_terms = {"healthy"}
    if canopy_condition not in poor_terms and canopy_condition not in healthy_terms:
        return "UNCLEAR"                    # e.g. an unrecognised free-text value
    sat_poor = satellite_ndvi < scheme_p25
    ground_poor = canopy_condition in poor_terms
    if sat_poor and ground_poor:
        return "AGREE"
    if not sat_poor and not ground_poor:
        return "AGREE"
    if sat_poor and not ground_poor:
        return "SATELLITE_WORSE"           # cloud, shadow, mixed pixel, recent cut
    return "GROUND_WORSE"                   # early stress, sub-pixel problem, pest
