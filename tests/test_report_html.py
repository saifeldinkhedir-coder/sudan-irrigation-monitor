"""
The self-contained HTML report.

Two promises are being pinned. First, that the file needs nothing from the
network - a promise that decays the first time somebody adds a convenient icon
font, and one that matters beyond convenience because a page that phones home
tells a third party which tenancy is being looked at. Second, that it survives
a monochrome office printer, because the meeting where a block is discussed
gets a photocopy, and on a photocopy red and green are the same grey.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "farmer_app"))
import report_html as RH


def _field(name, vigour, threshold=0.30, **extra):
    rec = {"name": name,
           "crop_health": {"readings": {
               "vigour": {"status": "OK" if vigour is not None else "NO DATA",
                          "value": vigour, "threshold": threshold,
                          "sensor": "Sentinel-2 median", "scale_m": 10,
                          "reason": None if vigour is not None
                          else "no cloud-free scene"},
               "canopy_moisture": {"status": "OK", "value": 0.11,
                                   "threshold": 0.12,
                                   "sensor": "Sentinel-2 median",
                                   "scale_m": 10},
               "greenness": {"status": "NOT AVAILABLE",
                             "reason": "no pixels"}}},
           "rainfall": {"season_mm": 228.0, "last_14d_mm": 0.0,
                        "sensor": "CHIRPS daily"},
           "soil": {"status": "OK", "texture": "clay"}}
    rec.update(extra)
    return rec


def _square(lon, lat, name, side=0.005):
    return {"type": "Feature", "properties": {"name": name},
            "geometry": {"type": "Polygon", "coordinates": [[
                [lon, lat], [lon + side, lat], [lon + side, lat + side],
                [lon, lat + side], [lon, lat]]]}}


REPORT = {
    "generated_utc": "2026-08-30T17:05:04+00:00",
    "season": {"start": "2022-07-01", "end": "2023-03-31"},
    "crop": "sorghum", "n_fields": 3,
    "fields": [_field("Field 1", 0.20), _field("Field 2", 0.55),
               _field("Field 3", None)],
    "limitations": ["Thermal is 100 m."],
    "limitations_ar": ["الحرارة تُقاس عند 100 متر."],
}

FC = {"type": "FeatureCollection", "features": [
    _square(33.10, 14.42, "Field 1"), _square(33.14, 14.45, "Field 2"),
    _square(33.18, 14.48, "Field 3")]}


class TestItNeedsNothingFromTheNetwork:
    def test_the_document_has_no_external_reference(self):
        """No CDN, no webfont, no tile server, no analytics. This promise
        decays the first time somebody adds a convenient icon font."""
        assert RH.external_references(RH.build(REPORT, FC)) == []

    def test_the_map_is_drawn_not_fetched(self):
        doc = RH.build(REPORT, FC)
        assert "<svg" in doc and "<polygon" in doc
        assert "tile" not in doc.lower()

    def test_it_declares_no_referrer(self):
        assert 'name="referrer" content="no-referrer"' in RH.build(REPORT, FC)

    def test_the_styling_is_inline(self):
        doc = RH.build(REPORT, FC)
        assert "<style>" in doc
        assert "@import" not in doc


class TestItSurvivesAPhotocopier:
    def test_every_status_carries_a_mark_as_well_as_a_colour(self):
        """On the monochrome photocopy that reaches the meeting, red and green
        are the same grey."""
        assert len(set(RH.STATUS_MARK.values())) == 4
        doc = RH.build(REPORT, FC)
        for mark in RH.STATUS_MARK.values():
            assert mark in doc

    def test_a_field_does_not_split_across_two_sheets(self):
        doc = RH.build(REPORT, FC)
        assert "break-inside:avoid" in doc.replace(" ", "")

    def test_the_colours_are_forced_to_print(self):
        assert "print-color-adjust:exact" in RH.build(REPORT, FC).replace(" ", "")

    def test_the_palette_matches_the_screen(self):
        """A printed sheet and the screen must not disagree about what a colour
        means."""
        import view as D
        for key, hexv in RH.STATUS_HEX.items():
            rgba = {"attention": D.COLOUR_ATTENTION, "watch": D.COLOUR_WATCH,
                    "ok": D.COLOUR_OK, "unmeasured": D.COLOUR_UNMEASURED}[key]
            assert hexv.lower() == "#%02x%02x%02x" % tuple(rgba[:3])


class TestTheContentSurvivesTheTrip:
    def test_worst_field_first(self):
        doc = RH.build(REPORT, FC)
        assert doc.index("Field 1") < doc.index("Field 2")

    def test_an_unmeasured_field_is_grey_and_present(self):
        """A field silently missing from the printed sheet reads as a field the
        farmer does not have."""
        doc = RH.build(REPORT, FC)
        assert "Field 3" in doc
        assert RH.STATUS_HEX["unmeasured"] in doc

    def test_every_row_keeps_its_sensor_and_scale(self):
        doc = RH.build(REPORT, FC)
        assert "Sentinel-2 median" in doc and "5.5 km" in doc

    def test_an_unavailable_row_says_so_rather_than_showing_a_number(self):
        doc = RH.build(REPORT, FC, ar=False)
        assert "not available" in doc
        assert "no pixels" in doc          # the reason, as the row's tooltip

    def test_the_limitations_block_is_not_on_the_sheet(self):
        """Eleven bullets at the END of a printed sheet restate what is
        already said beside each number - "احتياج، لا ما وصل" on the water row,
        "not available" with its reason on any row that could not be measured,
        the sensor and scale columns on every row - and they are the last thing
        the eye lands on, AFTER the answer the reader came for. A caveat that
        arrives after the decision is decoration."""
        for ar in (True, False):
            doc = RH.build(REPORT, FC, ar=ar)
            # The HEADING and the bullets are gone. The footer still names the
            # section, which is the point of the footer.
            assert "<h2>ما لا تدّعيه هذه الأداة</h2>" not in doc
            assert "What this tool does not claim</h2>" not in doc
            assert "<ul class=" not in doc
            assert "الحرارة تُقاس عند 100 متر." not in doc
            assert "Thermal is 100 m." not in doc

    def test_but_the_footer_says_where_they_are_and_how_many(self):
        """Removed from the sheet is not removed from the record."""
        doc = RH.build(REPORT, FC, ar=True)
        assert "التقرير الأصلي" in doc and "عن البيانات" in doc
        assert "(1 بندًا)" in doc
        en = RH.build(REPORT, FC, ar=False)
        assert "(1 points)" in en and "About page" in en

    def test_the_caveats_that_change_a_decision_are_still_beside_the_number(
            self):
        """The block went; the inline refusals did not. These are the ones
        that change what a reader concludes, and they sit on the row."""
        doc = RH.build(REPORT, FC, ar=True)
        # "not available" with its reason, on the row it belongs to.
        assert "غير متاح" in doc
        assert "no cloud-free scene" in doc
        # and the provenance columns, so a 100 m reading is visibly not a 10 m
        # one without anybody reading a footnote.
        assert "Sentinel-2 median" in doc and "5.5 km" in doc

    def test_a_demonstration_report_says_so_on_the_page(self):
        r = dict(REPORT, note="DEMONSTRATION DATA. Boundaries are invented.",
                 note_ar="بيانات عرض توضيحي. الحدود مخترعة.")
        doc = RH.build(r, FC, ar=True)
        assert "عرض توضيحي" in doc
        assert "الحدود مخترعة" in doc

    def test_the_arabic_page_is_marked_right_to_left(self):
        assert 'dir="rtl"' in RH.build(REPORT, FC, ar=True)
        assert 'dir="ltr"' in RH.build(REPORT, FC, ar=False)

    def test_a_field_name_cannot_inject_markup(self):
        """Field names come from a file somebody else may have written."""
        r = dict(REPORT, fields=[_field("<script>alert(1)</script>", 0.5)])
        doc = RH.build(r, {"features": []})
        assert "<script>alert" not in doc
        assert "&lt;script&gt;" in doc


class TestTheMap:
    def test_no_boundaries_says_so_rather_than_drawing_nothing(self):
        doc = RH.build(REPORT, {"features": []}, ar=True)
        assert "لا حدود حقول" in doc

    def test_the_map_says_it_is_not_imagery(self):
        """A drawing that looks like imagery and is not would be worse than a
        plain drawing."""
        assert "ليس صورة قمر" in RH.build(REPORT, FC, ar=True)
        assert "not imagery" in RH.build(REPORT, FC, ar=False)

    def test_it_carries_a_scale_bar(self):
        assert re.search(r"~\d+ m", RH.build(REPORT, FC))

    def test_non_polygon_geometry_is_skipped_not_crashed_on(self):
        fc = {"features": [{"properties": {"name": "line"},
                            "geometry": {"type": "LineString",
                                         "coordinates": [[33, 14], [34, 15]]}}]}
        assert "لا حدود حقول" in RH.build(REPORT, fc, ar=True)

    def test_a_single_field_does_not_divide_by_zero(self):
        fc = {"features": [_square(33.1, 14.4, "Field 1")]}
        assert "<polygon" in RH.build(REPORT, fc)


class TestWritingIt:
    def test_it_writes_a_file_that_opens(self, tmp_path):
        p = str(tmp_path / "report.html")
        out = RH.write(p, REPORT, FC)
        assert os.path.exists(p) and out["bytes"] > 1000
        text = open(p, encoding="utf-8").read()
        assert text.startswith("<!doctype html>")
        assert RH.external_references(text) == []


class TestNoEnglishLeaksIntoTheArabicSheet:
    """
    The engine writes its verdicts in English and they travel to the reader.
    The farmer app had a translation table; the printable report, written
    later, did not - so an Arabic sheet came out of the printer reading
    "حرارة السطح 42.41 °C warmer than the surrounding land" and
    "قوام التربة clay". Not broken enough to fail a test, and exactly wrong
    enough to look unfinished to the person it was printed for.

    The table now lives once, in src/vocab.py, and every surface imports it.
    """
    def _rec(self):
        r = _field("F", 0.5)
        r["thermal_stress"] = {"status": "OK", "value": 42.4,
                               "neighbourhood_c": 39.0,
                               "reading": "warmer than the surrounding land",
                               "sensor": "Landsat 8/9 ST_B10", "scale_m": 100}
        r["soil"] = {"status": "OK", "texture": "clay"}
        r["climate"] = {"heat_stress_days": 80, "heat_stress_threshold_c": 38.0,
                        "season_vs_history": {"this_season_mm": 228.0,
                                              "verdict": "drier than usual"}}
        return r

    def test_the_thermal_reading_is_arabic_on_the_arabic_sheet(self):
        doc = RH.build(dict(REPORT, fields=[self._rec()]), FC, ar=True)
        assert "أدفأ من الأرض المحيطة" in doc
        assert "warmer than the surrounding land" not in doc

    def test_the_soil_texture_is_arabic(self):
        doc = RH.build(dict(REPORT, fields=[self._rec()]), FC, ar=True)
        assert "طين" in doc

    def test_the_season_verdict_reaches_the_sheet_and_is_arabic(self):
        """It is one of the few figures that puts a season in context, and it
        was not printed at all."""
        doc = RH.build(dict(REPORT, fields=[self._rec()]), FC, ar=True)
        assert "أجفّ من المعتاد" in doc

    def test_english_readers_get_the_engine_s_own_words(self):
        doc = RH.build(dict(REPORT, fields=[self._rec()]), FC, ar=False)
        assert "warmer than the surrounding land" in doc
        assert "drier than usual" in doc

    def test_units_and_sensor_names_stay_latin_in_both(self):
        """Identifiers to be checked against a catalogue, not prose to read."""
        for ar in (True, False):
            doc = RH.build(dict(REPORT, fields=[self._rec()]), FC, ar=ar)
            assert "Landsat 8/9 ST_B10" in doc and "100 m" in doc

    def test_one_table_serves_both_surfaces(self):
        """Duplicated tables drift, and the drift shows up as English on
        somebody's printed sheet."""
        import vocab as V
        import view as D
        assert D.THERMAL_READING is V.THERMAL_READING
        assert D.SOIL_TEXTURE is V.SOIL_TEXTURE

    def test_an_unrecognised_verdict_appears_rather_than_vanishing(self):
        import vocab as V
        assert V.tr(V.THERMAL_READING, "something new", ar=True) \
            == "something new"

    def test_every_table_is_bilingual_and_distinct(self):
        import vocab as V
        for name, table in V.TABLES.items():
            for key, pair in table.items():
                assert len(pair) == 2, f"{name}.{key}"
                assert pair[0] and pair[1], f"{name}.{key}"
                assert pair[0] != pair[1] or key in ("unknown",), \
                    f"{name}.{key} is untranslated"


class TestTheSheetLooksLikeADocument:
    """
    On a SCREEN this is the thing somebody was handed - by email, on a memory
    stick. A white page with hairlines reads as a debug dump, and a reader who
    thinks they have been sent a debug dump reads the numbers with less care
    than they deserve.

    On PAPER it is a working sheet that gets photocopied for a meeting, so
    every ornament has to come off. Both, from one file.
    """
    def test_the_page_has_a_ground_to_sit_on(self):
        doc = RH.build(REPORT, FC)
        assert "radial-gradient" in doc
        assert "body {" in doc.replace("\n", " ") or "body{" in doc

    def test_the_header_is_a_band_not_a_bare_line(self):
        doc = RH.build(REPORT, FC)
        assert 'class="head"' in doc
        assert "linear-gradient" in doc

    def test_the_pattern_sits_behind_the_text_not_over_it(self):
        """As ::after with no z-index it painted last and washed the subtitle
        out. A decoration that makes a sentence harder to read has taken
        something real and given back nothing."""
        doc = RH.build(REPORT, FC)
        assert ".head::after" in doc
        assert "z-index:0" in doc
        assert ".head h1, .head .sub, .head .tags { position:relative; " \
               "z-index:1; }" in doc

    def test_print_strips_every_ornament(self):
        """A gradient across an A4 sheet is a cartridge, and the photocopy it
        becomes is a grey wash over the numbers."""
        block = RH.CSS.split("@media print")[1].split("@media (max-width")[0]
        assert "background:#fff" in block.replace(" ", "")
        assert ".head::after{display:none}" in block.replace(" ", "").replace(
            ";}", "}")
        assert "box-shadow:none" in block.replace(" ", "")

    def test_the_status_colours_still_print(self):
        """They carry the only claim the map makes, and each also has a mark
        for the monochrome copy."""
        block = RH.CSS.split("@media print")[1]
        assert "print-color-adjust:exact" in block.replace(" ", "")

    def test_a_wide_table_scrolls_in_its_box_and_never_the_page(self):
        """Six columns forced into 400 px do not become a small table - they
        become a tall one, every row wrapping to three lines, and a reader
        scanning for one number loses the row they were on."""
        assert "min-width:560px" in RH.CSS.replace(" ", "")
        assert "overflow-x:auto" in RH.CSS.replace(" ", "")

    def test_a_field_label_on_the_map_stays_readable_over_any_colour(self):
        assert "paint-order:stroke" in RH.CSS

    def test_none_of_it_costs_a_network_request(self):
        """The whole point of the file. A gradient is CSS; a background image
        would not be."""
        assert RH.external_references(RH.build(REPORT, FC)) == []
