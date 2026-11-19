#!/usr/bin/env python3
"""Tests for Copy CAD Lines (CAD Coordination panel): the layer list,
the line styles, the layer search, laying lines flat, and the script's
flat_curves run against Revit stand-ins (extracted by AST - the script
imports the Revit API).

Run:  python3 tests/test_cad_lines.py
"""

import ast
import io
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "..", "pyMEP.extension", "lib")
SCRIPT = os.path.join(HERE, "..", "pyMEP.extension", "pyMEP.tab", "15_CADCoordination.panel",
                      "01_CopyCADLines.pushbutton", "script.py")
sys.path.insert(0, LIB)

import pymep_cad_lines as CL


class Layers(unittest.TestCase):

    def test_rows_sorted_and_ticked_from_last_time(self):
        rows = CL.layer_rows({u"WALLS": 12, u"a-door": 3, CL.NO_LAYER: 1}, ticked=[u"walls"])
        self.assertEqual([r["name"] for r in rows], [CL.NO_LAYER, u"a-door", u"WALLS"])
        self.assertEqual([r["pick"] for r in rows], [False, False, True])
        self.assertEqual(CL.total(rows), 12)

    def test_search(self):
        self.assertTrue(CL.matches(u"C-ROAD-KERB", u"road kerb"))
        self.assertFalse(CL.matches(u"C-ROAD-KERB", u"road edge"))
        self.assertTrue(CL.matches(u"anything", u""))
        self.assertEqual(CL.rowfilter(u""), u"")
        self.assertEqual(CL.rowfilter(u"road kerb"), u"Layer LIKE '%road%' AND Layer LIKE '%kerb%'")
        self.assertEqual(CL.rowfilter(u"o'clock 50% [x]*"),
                         u"Layer LIKE '%o''clock%' AND Layer LIKE '%50[%]%' AND Layer LIKE '%[[]x[]][*]%'")


class Styles(unittest.TestCase):

    def test_usable_and_default(self):
        names = CL.usable_styles([u"Kerb", u"<Thin Lines>", u"<Room Separation>", u"<Sketch>", u"<Hidden>", u"Boundary"])
        self.assertEqual(names, [u"<Hidden>", u"<Thin Lines>", u"Boundary", u"Kerb"])
        self.assertEqual(CL.default_style(names, u"Kerb"), u"Kerb")
        self.assertEqual(CL.default_style(names, u"Gone"), u"<Thin Lines>")
        self.assertEqual(CL.default_style([u"Kerb"], None), u"Kerb")
        self.assertIsNone(CL.default_style([], None))


class Flat(unittest.TestCase):

    def test_segments_on_the_plane(self):
        segs = CL.segments([(0, 0, 5), (0.001, 0, 5), (1, 0, 6), (1, 1, 7)], 2.0, 0.01)
        self.assertEqual(segs, [((0.0, 0.0, 2.0), (1.0, 0.0, 2.0)), ((1.0, 0.0, 2.0), (1.0, 1.0, 2.0))])
        self.assertEqual(CL.segments([(0, 0, 0), (0, 0, 9)], 0.0, 0.01), [])   # vertical: nothing in plan
        self.assertTrue(CL.is_flat([3.0, 3.0, 3.0000001]))
        self.assertFalse(CL.is_flat([3.0, 3.1]))

    def test_report(self):
        rows = CL.report_rows({u"B": 3, u"a": 2}, {u"B": 1}, {u"c": 4})
        self.assertEqual(rows, [[u"a", 2, 0, 0], [u"B", 3, 1, 0], [u"c", 0, 0, 4], [u"Total", 5, 1, 4]])


# ---------------------------------------------------------------------------
# the script's flat_curves against stand-ins
# ---------------------------------------------------------------------------
class XYZ(object):
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = float(x), float(y), float(z)


XYZ.BasisZ = XYZ(0, 0, 1)


class Curve(object):
    IsBound = True

    def __init__(self, pts):
        self.pts = pts

    def Tessellate(self):
        return self.pts

    @property
    def Length(self):
        return sum(math.hypot(b.X - a.X, b.Y - a.Y) for a, b in zip(self.pts, self.pts[1:]))

    def CreateTransformed(self, tf):
        return type(self)([XYZ(p.X, p.Y, p.Z + tf.dz) for p in self.pts])


class Line(Curve):
    @staticmethod
    def CreateBound(a, b):
        return Line([a, b])


class Arc(Curve):
    def __init__(self, pts=None, center=None, radius=0.0, bound=True):
        Curve.__init__(self, pts or [])
        self.Center, self.Radius, self.IsBound = center, radius, bound

    @staticmethod
    def Create(plane, r, a0, a1):
        a = Arc(center=plane.origin, radius=r)
        a.full = (a0, a1)
        return a

    def CreateTransformed(self, tf):
        return Arc([XYZ(p.X, p.Y, p.Z + tf.dz) for p in self.pts], self.Center, self.Radius)


class Spline(Curve):
    pass


class PolyLine(object):
    def __init__(self, pts):
        self.pts = pts

    def GetCoordinates(self):
        return self.pts


class Plane(object):
    @staticmethod
    def CreateByNormalAndOrigin(n, o):
        p = Plane()
        p.origin = o
        return p


class Transform(object):
    @staticmethod
    def CreateTranslation(v):
        t = Transform()
        t.dz = v.Z
        return t


def _extract():
    src = io.open(SCRIPT, encoding="utf-8").read()
    ns = {"Arc": Arc, "Line": Line, "PolyLine": PolyLine, "Plane": Plane, "XYZ": XYZ,
          "Transform": Transform, "CL": CL, "math": math, "min_len": 0.01}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == "flat_curves":
            exec(compile(ast.get_source_segment(src, node), SCRIPT, "exec"), ns)
    return ns["flat_curves"]


flat_curves = _extract()


class FlatCurves(unittest.TestCase):

    def test_flat_line_moved_to_the_plane(self):
        out = flat_curves(Line([XYZ(0, 0, 5), XYZ(3, 4, 5)]), 2.0)
        self.assertEqual(len(out), 1)
        self.assertEqual([(p.X, p.Y, p.Z) for p in out[0].pts], [(0, 0, 2), (3, 4, 2)])

    def test_flat_arc_and_spline_keep_their_kind(self):
        arc = Arc([XYZ(1, 0, 0), XYZ(0.7, 0.7, 0), XYZ(0, 1, 0)], XYZ(0, 0, 0), 1.0)
        out = flat_curves(arc, 3.0)
        self.assertIsInstance(out[0], Arc)
        self.assertTrue(all(p.Z == 3.0 for p in out[0].pts))
        sp = Spline([XYZ(0, 0, 1), XYZ(1, 1, 1), XYZ(2, 0, 1)])
        self.assertIsInstance(flat_curves(sp, 0.0)[0], Spline)

    def test_full_circle(self):
        circle = Arc([], XYZ(5, 6, 9), 2.5, bound=False)
        out = flat_curves(circle, 1.0)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0].Center.X, out[0].Center.Y, out[0].Center.Z, out[0].Radius), (5, 6, 1, 2.5))
        self.assertAlmostEqual(out[0].full[1], 2 * math.pi)

    def test_polyline_and_3d_curve_become_segments(self):
        pl = PolyLine([XYZ(0, 0, 0), XYZ(1, 0, 0), XYZ(1, 1, 0)])
        out = flat_curves(pl, 2.0)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(isinstance(c, Line) for c in out))
        sloped = Spline([XYZ(0, 0, 0), XYZ(1, 0, 1), XYZ(2, 0, 2)])
        out = flat_curves(sloped, 0.5)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(p.Z == 0.5 for c in out for p in c.pts))

    def test_too_short(self):
        self.assertEqual(flat_curves(Line([XYZ(0, 0, 0), XYZ(0.001, 0, 0)]), 0.0), [])


if __name__ == "__main__":
    unittest.main()
