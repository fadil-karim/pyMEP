#!/usr/bin/env python3
"""Unit tests for pymep_place_coords - the typed points, units and
coordinate systems, and the family search.

Run:  python3 tests/test_place_coords.py
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "lib"))

import pymep_place_coords as PC


class Parsing(unittest.TestCase):

    def test_xy_and_xyz(self):
        pts, errs = PC.parse_points(u"10, 20\n30, 40, 1.5\n")
        self.assertEqual(errs, [])
        self.assertEqual([(p["x"], p["y"], p["z"], p["has_z"]) for p in pts],
                         [(10.0, 20.0, 0.0, False), (30.0, 40.0, 1.5, True)])
        self.assertEqual([p["line"] for p in pts], [1, 2])

    def test_separators_and_labels(self):
        pts, errs = PC.parse_points(u"1;2;3\n4\t5\n6 7 8\nx=9, y=10, z=11\nE: 12  N: 13")
        self.assertEqual(errs, [])
        self.assertEqual([(p["x"], p["y"], p["z"]) for p in pts],
                         [(1, 2, 3), (4, 5, 0), (6, 7, 8), (9, 10, 11), (12, 13, 0)])

    def test_names_blank_lines_and_comments(self):
        pts, errs = PC.parse_points(u"# piles\n\nP1605, 10, 20\n30, 40, 2, P1606\n  \n")
        self.assertEqual(errs, [])
        self.assertEqual([p["name"] for p in pts], [u"P1605", u"P1606"])
        self.assertEqual(pts[1]["z"], 2.0)

    def test_default_z(self):
        pts, _ = PC.parse_points(u"1, 2", default_z=0.5)
        self.assertEqual(pts[0]["z"], 0.5)
        self.assertFalse(pts[0]["has_z"])

    def test_errors(self):
        pts, errs = PC.parse_points(u"1\n1, 2, 3, 4, 5, 6\na b 1 2\n5, 6")
        self.assertEqual(len(pts), 1)
        self.assertEqual([(e[0], e[2]) for e in errs],
                         [(1, u"needs at least x and y"), (2, u"more than five numbers"),
                          (3, u"more than one word on the line")])

    def test_lines_with_an_end_point(self):
        pts, errs = PC.parse_points(u"1, 2, 3, 4\nB1, 1, 2, 3, 4, 0.5\n7, 8, 9")
        self.assertEqual(errs, [])
        self.assertEqual((pts[0]["x2"], pts[0]["y2"], pts[0]["has_end"], pts[0]["z"]),
                         (3.0, 4.0, True, 0.0))
        self.assertEqual((pts[1]["name"], pts[1]["z"], pts[1]["has_z"]), (u"B1", 0.5, True))
        self.assertEqual((pts[2]["has_end"], pts[2]["z"]), (False, 9.0))
        self.assertIn(u"2 with an end point", PC.summary_text(pts, errs, "m"))
        out = PC.prepare(pts, "m", 0.0)
        self.assertAlmostEqual(out[0]["x2"], 3 / 0.3048)
        self.assertIsNone(out[2]["x2"])
        self.assertEqual(PC.line_end(out[0]), (out[0]["x2"], out[0]["y2"]))
        ex, ey = PC.line_end(out[2])
        self.assertAlmostEqual(ex - out[2]["x"], 1 / 0.3048)
        self.assertAlmostEqual(ey, out[2]["y"])

    def test_adaptive_points_spread(self):
        pt = {"x": 0.0, "y": 0.0, "has_end": False}
        self.assertEqual(PC.spread_points(pt, 1), [(0.0, 0.0)])
        three = PC.spread_points(pt, 3)
        self.assertAlmostEqual(three[2][0], 2 / 0.3048)
        pt2 = {"x": 0.0, "y": 0.0, "x2": 0.0, "y2": 10.0, "has_end": True}
        four = PC.spread_points(pt2, 4)
        self.assertEqual(four[:2], [(0.0, 0.0), (0.0, 10.0)])
        self.assertAlmostEqual(four[3][1], 30.0)

    def test_summary(self):
        pts, errs = PC.parse_points(u"1, 2\n3, 4, 5\nbad")
        s = PC.summary_text(pts, errs, "m")
        self.assertIn(u"2 point(s) read in metres", s)
        self.assertIn(u"1 with an offset, the rest at the level", s)
        self.assertIn(u"line 3 skipped - needs at least x and y: 'bad'", s)
        self.assertEqual(PC.summary_text([], [], "m"), u"Type one point per line: x, y  or  x, y, z.")
        pts, _ = PC.parse_points(u"P1, 1, 2")
        self.assertIn(u"1 named (goes to Mark)", PC.summary_text(pts, [], "mm"))
        self.assertIn(u"all at the level (no z typed)", PC.summary_text(pts, [], "mm"))


class Maths(unittest.TestCase):

    def test_units(self):
        self.assertAlmostEqual(PC.to_feet(1, "ft"), 1.0)
        self.assertAlmostEqual(PC.to_feet(0.3048, "m"), 1.0)
        self.assertAlmostEqual(PC.to_feet(304.8, "mm"), 1.0)
        with self.assertRaises(ValueError):
            PC.to_feet(1, "yd")

    def test_internal_and_pbp(self):
        self.assertEqual(PC.internal_xy(1, 2), (1.0, 2.0))
        self.assertEqual(PC.internal_xy(1, 2, "pbp", pbp_x=10, pbp_y=20), (11.0, 22.0))
        with self.assertRaises(ValueError):
            PC.internal_xy(1, 2, "elsewhere")

    def test_shared_round_trip(self):
        east, north, angle = 523000.0, 181000.0, math.radians(30.0)
        for ix, iy in [(0, 0), (100, 0), (0, 100), (-37.5, 12.25)]:
            sx, sy = PC.shared_xy(ix, iy, east, north, angle)
            bx, by = PC.internal_xy(sx, sy, "shared", east=east, north=north, angle=angle)
            self.assertAlmostEqual(bx, ix, places=6)
            self.assertAlmostEqual(by, iy, places=6)
        # the internal origin reads as (east, north) in shared coordinates
        self.assertEqual(PC.internal_xy(east, north, "shared", east=east, north=north,
                                        angle=angle), (0.0, 0.0))
        # a point 100 ft along project north, model rotated 90 deg anticlockwise:
        # shared = (east - 100, north)
        x, y = PC.internal_xy(east - 100, north, "shared", east=east, north=north,
                              angle=math.radians(90))
        self.assertAlmostEqual(x, 0.0, places=6)
        self.assertAlmostEqual(y, 100.0, places=6)

    def test_prepare(self):
        pts, _ = PC.parse_points(u"P1, 1000, 2000\n3000, 4000, 500")
        out = PC.prepare(pts, "mm", level_elevation_ft=10.0)
        self.assertAlmostEqual(out[0]["x"], 1000 / 304.8)
        self.assertAlmostEqual(out[0]["z"], 10.0)
        self.assertEqual(out[0]["name"], u"P1")
        self.assertAlmostEqual(out[1]["z"], 10.0 + 500 / 304.8)
        self.assertAlmostEqual(out[1]["offset"], 500 / 304.8)
        self.assertEqual(out[1]["typed_z"], 500.0)
        out2 = PC.prepare(pts, "m", 0.0, system="pbp", pbp_x=1.0, pbp_y=2.0)
        self.assertAlmostEqual(out2[0]["x"], 1000 / 0.3048 + 1.0)


class Reading(unittest.TestCase):
    """Element Coordinates: internal feet out to the three systems."""
    POS = dict(east=1000.0, north=2000.0, elev=50.0, angle=math.radians(30.0),
               pbp_x=10.0, pbp_y=20.0, pbp_z=5.0)
    ROWS = [{"name": u"P1605", "point": u"location", "x": 100.0, "y": 200.0, "z": 30.0,
             "level": u"L1", "label": u"Pile Cap : 600 dia", "id": 1234},
            {"name": u"", "point": u"start", "x": 0.0, "y": 0.0, "z": 0.0,
             "level": u"", "label": u"Pipe", "id": 99}]

    def test_from_feet(self):
        self.assertAlmostEqual(PC.from_feet(1.0, "m"), 0.3048)
        self.assertAlmostEqual(PC.from_feet(1.0, "mm"), 304.8)
        self.assertAlmostEqual(PC.from_feet(2.0, "ft"), 2.0)

    def test_systems(self):
        self.assertEqual(PC.coords_in("internal", 1, 2, 3, **self.POS), (1.0, 2.0, 3.0))
        self.assertEqual(PC.coords_in("pbp", 11, 22, 8, **self.POS), (1.0, 2.0, 3.0))
        X, Y, Z = PC.coords_in("shared", 0, 0, 0, **self.POS)
        self.assertAlmostEqual(X, 1000.0)
        self.assertAlmostEqual(Y, 2000.0)
        self.assertAlmostEqual(Z, 50.0)
        # back through internal_xy
        x, y = PC.internal_xy(*PC.coords_in("shared", 100, 200, 30, **self.POS)[:2],
                              system="shared", east=1000.0, north=2000.0,
                              angle=math.radians(30.0))
        self.assertAlmostEqual(x, 100.0, places=6)
        self.assertAlmostEqual(y, 200.0, places=6)
        with self.assertRaises(ValueError):
            PC.coords_in("nope", 0, 0, 0)

    def test_table(self):
        text = PC.coords_table(self.ROWS, "shared", "m", **self.POS)
        lines = text.split(u"\n")
        self.assertEqual(lines[0].split(u"\t")[:5],
                         [u"Name", u"Point", u"Easting (metres)", u"Northing (metres)",
                          u"Elevation (metres)"])
        cells = lines[1].split(u"\t")
        self.assertEqual(cells[0], u"P1605")
        self.assertEqual(cells[-1], u"1234")
        self.assertEqual(cells[5], u"L1")
        X, Y, Z = PC.coords_in("shared", 100.0, 200.0, 30.0, **self.POS)
        self.assertEqual(cells[2], u"{0:.3f}".format(X * 0.3048))
        self.assertEqual(cells[4], u"{0:.3f}".format(80.0 * 0.3048))
        mm = PC.coords_table(self.ROWS, "internal", "mm")
        self.assertEqual(mm.split(u"\n")[1].split(u"\t")[2], u"30480")      # 100 ft
        self.assertIn(u"X (millimetres)", mm)

    def test_place_lines(self):
        text = PC.place_lines(self.ROWS, "internal", "m")
        self.assertEqual(text.split(u"\n"), [u"P1605, 30.480, 60.960", u"0.000, 0.000"])
        pts, errs = PC.parse_points(text)
        self.assertEqual(errs, [])
        self.assertEqual(pts[0]["name"], u"P1605")
        self.assertAlmostEqual(pts[0]["x"], 30.48)


class Moves(unittest.TestCase):
    """Move to Coordinates: pairing elements with typed points."""

    def elems(self):
        return [{"id": 1, "name": u"P1", "x": 10.0, "y": 10.0, "z": 5.0, "level_z": 0.0, "flat": False},
                {"id": 2, "name": u"P2", "x": 20.0, "y": 10.0, "z": 5.0, "level_z": 0.0, "flat": False},
                {"id": 3, "name": u"", "label": u"Filled Region", "x": 30.0, "y": 10.0, "z": 0.0,
                 "level_z": None, "flat": True}]

    def targets(self, text):
        pts, errs = PC.parse_points(text)
        assert not errs, errs
        return PC.prepare(pts, "ft", 0.0)

    def test_group_move_keeps_offsets(self):
        plan = PC.plan_moves(self.elems(), self.targets(u"100, 50"), reference=1)
        self.assertEqual(plan["mode"], "group")
        self.assertEqual([(m["to"][0], m["to"][1]) for m in plan["moves"]],
                         [(90.0, 50.0), (100.0, 50.0), (110.0, 50.0)])
        self.assertEqual([m["dz"] for m in plan["moves"]], [0.0, 0.0, 0.0])
        plan_z = PC.plan_moves(self.elems(), self.targets(u"100, 50, 2"), reference=0)
        self.assertEqual([m["dz"] for m in plan_z["moves"]], [-3.0, -3.0, 0.0])   # flat one stays level

    def test_named(self):
        plan = PC.plan_moves(self.elems(), self.targets(u"p2, 0, 0\nP9, 1, 1"))
        self.assertEqual(plan["mode"], "named")
        self.assertEqual([m["id"] for m in plan["moves"]], [2])
        self.assertEqual(plan["moves"][0]["to"][:2], (0.0, 0.0))
        self.assertEqual([e["id"] for e in plan["stay"]], [1, 3])
        self.assertIn(u"no selected element has the Mark 'P9'", plan["warnings"][0])

    def test_ordered_and_z(self):
        plan = PC.plan_moves(self.elems(), self.targets(u"1, 1\n2, 2, 7\n3, 3, 7"))
        self.assertEqual(plan["mode"], "ordered")
        self.assertEqual([m["dz"] for m in plan["moves"]], [0.0, 2.0, 0.0])
        self.assertAlmostEqual(plan["moves"][1]["dist"], math.sqrt(18 ** 2 + 8 ** 2 + 2 ** 2))

    def test_refusals(self):
        with self.assertRaises(ValueError):
            PC.plan_moves(self.elems(), self.targets(u"1, 1\n2, 2"))
        with self.assertRaises(ValueError):
            PC.plan_moves(self.elems(), self.targets(u"P1, 1, 1\n2, 2\n3, 3"))
        with self.assertRaises(ValueError):
            PC.plan_moves([], self.targets(u"1, 1"))
        with self.assertRaises(ValueError):
            PC.plan_moves(self.elems(), [])

    def test_text(self):
        plan = PC.plan_moves(self.elems(), self.targets(u"100, 50"), reference=0)
        text = PC.moves_text(plan, "internal", "ft")
        self.assertIn(u"3 element(s) move (moved together", text)
        self.assertIn(u"P1: 10.000, 10.000  ->  100.000, 50.000   (98.489 ft)", text)
        self.assertIn(u"Filled Region: 30.000, 10.000  ->  120.000, 50.000", text)


class Search(unittest.TestCase):
    ENTRIES = [
        {"label": u"Pile Cap : 600 dia", "category": u"Structural Foundations"},
        {"label": u"Pile Cap : 900 dia", "category": u"Structural Foundations"},
        {"label": u"Pad Footing : 1200 x 1200", "category": u"Structural Foundations"},
        {"label": u"Concrete Column : 600 dia", "category": u"Structural Columns"},
    ]

    def test_words_and_case(self):
        self.assertEqual(len(PC.search(self.ENTRIES, u"")), 4)
        self.assertEqual([e["label"] for e in PC.search(self.ENTRIES, u"600 DIA")],
                         [u"Pile Cap : 600 dia", u"Concrete Column : 600 dia"])
        self.assertEqual([e["label"] for e in PC.search(self.ENTRIES, u"cap 900")],
                         [u"Pile Cap : 900 dia"])
        self.assertEqual(PC.search(self.ENTRIES, u"steel"), [])

    def test_category_filter(self):
        got = PC.search(self.ENTRIES, u"600", category=u"Structural Columns")
        self.assertEqual([e["label"] for e in got], [u"Concrete Column : 600 dia"])
        self.assertEqual(len(PC.search(self.ENTRIES, u"", category=u"Structural Foundations")), 3)

    def test_label(self):
        self.assertEqual(PC.type_label(u"Pile Cap", u"600 dia"), u"Pile Cap : 600 dia")


if __name__ == "__main__":
    unittest.main()
