"""
The head-to-tail gap is a SIGNED number, and the sign comes from an assumption
about the file, not from a measurement. These tests pin down what the engine
does with that assumption: trust it when it is declared or derivable, admit it
when it is not, and refuse the figure outright when the two available inputs
disagree.

The failure this guards against is not a crash. It is the engine reporting a
confident gap with the head and tail swapped, which sends a manager to the wrong
end of the canal with a bootstrap CI backing the error.
"""

import decision_logic as dl


# A canal running roughly west to east. The offtake sits off the western end.
WEST_TO_EAST = [[33.00, 14.40], [33.05, 14.40], [33.10, 14.41], [33.15, 14.41]]
EAST_TO_WEST = list(reversed(WEST_TO_EAST))
WEST_OFFTAKE = [32.98, 14.40]


def test_no_declaration_and_no_offtake_is_reported_as_assumed():
    d = dl.resolve_canal_direction(WEST_TO_EAST)
    assert d["status"] == "OK"
    assert d["verified"] is False
    assert d["reverse"] is False
    assert d["basis"].startswith("ASSUMED")


def test_declared_tail_first_reverses_the_canal():
    d = dl.resolve_canal_direction(EAST_TO_WEST, declared="tail_first")
    assert d["status"] == "OK"
    assert d["verified"] is True
    assert d["reverse"] is True
    assert "DECLARED" in d["basis"]


def test_offtake_puts_the_head_at_the_nearer_end():
    forward = dl.resolve_canal_direction(WEST_TO_EAST, offtake=WEST_OFFTAKE)
    backward = dl.resolve_canal_direction(EAST_TO_WEST, offtake=WEST_OFFTAKE)
    assert forward["verified"] and backward["verified"]
    # Same physical canal, opposite files: exactly one of them must be reversed.
    assert forward["reverse"] is False
    assert backward["reverse"] is True


def test_offtake_equidistant_from_both_ends_is_not_available():
    # An offtake on the perpendicular bisector cannot tell the ends apart.
    midpoint_offtake = [33.075, 14.60]
    d = dl.resolve_canal_direction(WEST_TO_EAST, offtake=midpoint_offtake)
    assert d["status"] == "NOT AVAILABLE"
    assert "similar distance" in d["reason"]
    assert d["verified"] is False


def test_declaration_conflicting_with_the_offtake_is_refused_not_resolved():
    d = dl.resolve_canal_direction(WEST_TO_EAST, declared="tail_first",
                                   offtake=WEST_OFFTAKE)
    assert d["status"] == "NOT AVAILABLE"
    assert "conflicts" in d["reason"]
    assert d["verified"] is False


def test_unrecognised_vertex_order_value_is_refused():
    d = dl.resolve_canal_direction(WEST_TO_EAST, declared="downstream")
    assert d["status"] == "NOT AVAILABLE"
    assert "vertex_order" in d["reason"]


def test_two_vertices_are_enough_but_one_is_not():
    assert dl.resolve_canal_direction([[33.0, 14.4]])["status"] == "NOT AVAILABLE"
    assert dl.resolve_canal_direction(WEST_TO_EAST[:2])["status"] == "OK"


def test_reversing_the_file_flips_the_gap_when_direction_is_ignored():
    """
    The reason the guard exists, stated as a test: the raw slope fit has no idea
    which end is which, so the same canal digitised backwards produces a gap of
    the opposite sign. Nothing downstream can recover the truth from the numbers
    alone - only the direction input can.
    """
    positions = [0.0, 0.25, 0.5, 0.75, 1.0]
    declining = [0.62, 0.55, 0.47, 0.40, 0.33]

    forward = dl.fit_head_tail_slope(positions, declining)
    reversed_file = dl.fit_head_tail_slope(positions, list(reversed(declining)))

    assert forward.gap > 0          # tail below head
    assert reversed_file.gap < 0    # same data, file drawn the other way
    assert abs(forward.slope + reversed_file.slope) < 1e-9


def test_engine_withholds_the_gap_when_direction_conflicts(ee_env):
    """A conflict must reach the engine's output as NOT AVAILABLE, not as a
    number carrying a coin-flip sign."""
    import engine

    canal = {"type": "Feature",
             "geometry": {"type": "LineString", "coordinates": WEST_TO_EAST},
             "properties": {"name": "Conflicted Canal",
                            "vertex_order": "tail_first",
                            "offtake": WEST_OFFTAKE}}
    geom = engine.ee.Geometry(canal["geometry"])
    out = engine.head_tail_equity(geom, geom.buffer(1500),
                                  "2021-07-01", "2022-03-31",
                                  canal_props=canal["properties"])
    assert out["status"] == "NOT AVAILABLE"
    assert "sign of the gap is unknown" in out["reason"]
    assert "head_tail_gap" not in out


def test_engine_records_the_assumption_when_direction_is_undeclared(ee_env):
    import engine

    canal = {"type": "Feature",
             "geometry": {"type": "LineString", "coordinates": WEST_TO_EAST},
             "properties": {"name": "Undeclared Canal"}}
    geom = engine.ee.Geometry(canal["geometry"])
    out = engine.head_tail_equity(geom, geom.buffer(1500),
                                  "2021-07-01", "2022-03-31",
                                  canal_props=canal["properties"])
    assert out["direction"]["verified"] is False
    assert out["direction"]["basis"].startswith("ASSUMED")
