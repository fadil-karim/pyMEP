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
        pts, errs = PC.parse_points(u"1\n1, 2, 3, 4\na b 1 2\n5, 6")
        self.assertEqual(len(pts), 1)
        self.assertEqual([(e[0], e[2]) for e in errs],
                         [(1, u"needs at least x and y"), (2, u"more than three numbers"),
                          (3, u"more than one word on the line")])

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
