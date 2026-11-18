#!/usr/bin/env python3
"""Tests for the Revit-side post measuring of the bridges (stdlib only,
no Revit needed).

The chords stop a cutback short of the posts' inner faces. The faces
used to come from the post type's size parameters (b, d, h ...), which
can be smaller than the post - and a framing post's location line can
sit off its steel with its justification - so the chords ran into the
posts. The posts' centres and faces now come from their solid geometry
at mid-height (a base plate, cap or bracket does not count), and after
every build and update the steel of every member meeting a post is
measured against the post faces and named when it still reaches in.

The functions are extracted from pymep_bridge_revit.py and
pymep_bridge_sync.py by AST (those modules import the Revit API and
cannot be imported under CPython) and run against stand-ins.

Run:  python3 tests/test_bridge_revit.py
"""

import ast
import math
import os
import sys
import types
import unittest

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "pyMEP.extension", "lib")
sys.path.insert(0, LIB)

import pymep_bridge as BR

FT = 0.3048


def m_ft(v):
    return v / FT


# ---------------------------------------------------------------------------
# minimal Revit stand-ins (added to any fake Autodesk.Revit.DB another test
# module put in place, never replacing what it has)
# ---------------------------------------------------------------------------
class XYZ(object):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.X, self.Y, self.Z = float(x), float(y), float(z)


class Options(object):
    pass


class Solid(object):
    def __init__(self, edges, volume=1.0):
        self.Edges = edges
        self.Volume = volume


class GeometryInstance(object):
    def __init__(self, items):
        self._items = items

    def GetInstanceGeometry(self):
        return self._items


class Edge(object):
    def __init__(self, pts):
        self._pts = pts

    def Tessellate(self):
        return self._pts


def install_stand_ins():
    """Put the stand-ins on the fake Autodesk.Revit.DB in sys.modules - one
    another test module may have put in place (and may put in place again
    after this module is imported), so this runs before every test."""
    db = sys.modules.get("Autodesk.Revit.DB")
    if db is None:
        db = types.ModuleType("Autodesk.Revit.DB")
        revit = sys.modules.get("Autodesk.Revit") or types.ModuleType("Autodesk.Revit")
        autodesk = sys.modules.get("Autodesk") or types.ModuleType("Autodesk")
        revit.DB = db
        autodesk.Revit = revit
        sys.modules.setdefault("Autodesk", autodesk)
        sys.modules.setdefault("Autodesk.Revit", revit)
        sys.modules["Autodesk.Revit.DB"] = db
    # the extracted code imports these and checks isinstance against them
    db.Solid, db.GeometryInstance, db.Options = Solid, GeometryInstance, Options


class StandIns(unittest.TestCase):
    def setUp(self):
        install_stand_ins()


def box_edges(x0, y0, x1, y1, z0, z1):
    """The 12 edges of a box in metres, as model-feet edges."""
    c = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    edges = []
    for z in (z0, z1):
        for i in range(4):
            a, b = c[i], c[(i + 1) % 4]
            edges.append(Edge([XYZ(m_ft(a[0]), m_ft(a[1]), m_ft(z)), XYZ(m_ft(b[0]), m_ft(b[1]), m_ft(z))]))
    for x, y in c:
        edges.append(Edge([XYZ(m_ft(x), m_ft(y), m_ft(z0)), XYZ(m_ft(x), m_ft(y), m_ft(z1))]))
    return edges


def rim_edges(cx, cy, r, z, n=24):
    pts = [XYZ(m_ft(cx + r * math.cos(2 * math.pi * k / n)), m_ft(cy + r * math.sin(2 * math.pi * k / n)), m_ft(z))
           for k in range(n + 1)]
    return [Edge(pts)]


class El(object):
    def __init__(self, geometry):
        self._g = geometry

    def get_Geometry(self, opt):
        return self._g


def extract(module_file, names, ns):
    path = os.path.join(LIB, module_file)
    with open(path) as f:
        src = f.read()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.get_source_segment(src, node), path, "exec"), ns)
    missing = [n for n in names if n not in ns]
    assert not missing, missing
    return ns


RV = extract("pymep_bridge_revit.py", ["_solid_points", "post_section", "into_posts"],
             {"math": math, "BR": BR, "ft_to_m": lambda v: v * FT})
SY = extract("pymep_bridge_sync.py", ["problem_line", "_into_text", "post_line"], {})


class PostSection(StandIns):

    def test_shaft_not_base_plate_or_bracket(self):
        # a 300 x 200 shaft from 0 to 2.3 m on a 500 x 500 x 20 base plate,
        # with a 600 mm wide fin plate at mid-height on one side
        shaft = Solid(box_edges(9.85, 4.9, 10.15, 5.1, 0.02, 2.3))
        plate = Solid(box_edges(9.75, 4.75, 10.25, 5.25, 0.0, 0.02))
        fin = Solid(box_edges(10.15, 4.7, 10.25, 5.3, 1.0, 1.3))
        el = El([GeometryInstance([shaft, plate, fin])])
        sec = RV["post_section"](el, m_ft(0.0), m_ft(2.3))
        xs = [p[0] for p in sec]
        ys = [p[1] for p in sec]
        self.assertAlmostEqual(min(xs), 9.85, places=6)
        self.assertAlmostEqual(max(xs), 10.15, places=6)
        self.assertAlmostEqual(min(ys), 4.9, places=6)
        self.assertAlmostEqual(max(ys), 5.1, places=6)

    def test_round_post_by_its_rims(self):
        # a round post: no vertical edges but its bottom and top rims
        tube = Solid(rim_edges(3.0, 4.0, 0.0845, 0.0) + rim_edges(3.0, 4.0, 0.0845, 2.0))
        sec = RV["post_section"](El([tube]), m_ft(0.0), m_ft(2.0))
        self.assertAlmostEqual(max(p[0] for p in sec) - min(p[0] for p in sec), 0.169, places=4)
        self.assertAlmostEqual(max(p[1] for p in sec) - min(p[1] for p in sec), 0.169, places=3)

    def test_no_geometry_or_no_shaft(self):
        self.assertIsNone(RV["post_section"](El(None), 0.0, m_ft(2.0)))
        self.assertIsNone(RV["post_section"](El([]), 0.0, m_ft(2.0)))
        only_plate = Solid(box_edges(0, 0, 0.5, 0.5, 0.0, 0.02))
        self.assertIsNone(RV["post_section"](El([only_plate]), 0.0, m_ft(2.0)))
        empty = Solid(box_edges(0, 0, 0.2, 0.2, 0.0, 2.0), volume=0.0)   # a zero-volume solid is skipped
        self.assertIsNone(RV["post_section"](El([empty]), 0.0, m_ft(2.0)))

    def test_section_sizes_the_frame(self):
        # what post_info hands on: the section, its centre as the post's x, y
        def post(x, y, z0, z1):
            sec = RV["post_section"](El([Solid(box_edges(x - 0.15, y - 0.1, x + 0.15, y + 0.1, z0, z1))]), m_ft(z0), m_ft(z1))
            return {"x": x, "y": y, "z_bottom": z0, "z_top": z1, "size_m": 0.2, "section": sec}
        fr = BR.frame_from_posts([post(0, 1.5, 10, 12.3), post(0, -1.5, 10, 12.3)],
                                 [post(20, 1.5, 12, 14.3), post(20, -1.5, 12, 14.3)])
        self.assertAlmostEqual(fr["post_size"], 0.3)
        self.assertAlmostEqual(fr["post_size_t"], 0.2)


class IntoPosts(StandIns):

    FRAME = {"origin": (0.0, 0.0), "u": (1.0, 0.0), "v": (0.0, 1.0), "span": 20.0,
             "tL": 1.5, "tR": -1.5, "post_size": 0.3, "post_size_t": 0.2}

    def chord(self, s0, s1, t=1.5):
        return El([Solid(box_edges(s0, t - 0.05, s1, t + 0.05, 0.0, 0.2))])

    def test_clear_of_the_faces(self):
        self.assertEqual(RV["into_posts"](self.chord(0.16, 19.84), self.FRAME), 0.0)

    def test_into_a_start_and_an_end_post(self):
        self.assertAlmostEqual(RV["into_posts"](self.chord(0.0, 19.84), self.FRAME), 0.15)
        self.assertAlmostEqual(RV["into_posts"](self.chord(0.16, 19.95), self.FRAME), 0.10)

    def test_turned_bridge(self):
        # the same bridge turned 90 degrees in plan: measured in its own frame
        fr = dict(self.FRAME, u=(0.0, 1.0), v=(-1.0, 0.0))
        el = El([Solid(box_edges(-1.55, 0.05, -1.45, 19.84, 0.0, 0.2))])   # starts 100 mm into the start post
        self.assertAlmostEqual(RV["into_posts"](el, fr), 0.10)

    def test_wider_than_the_post_or_right_through_it(self):
        # a 300 mm wide chord on a 200 mm wide post: no corner of it lies inside the post
        wide = El([Solid(box_edges(0.05, 1.35, 19.84, 1.65, 0.0, 0.2))])
        self.assertAlmostEqual(RV["into_posts"](wide, self.FRAME), 0.10)
        # one piece running through the start post and out the far side
        through = El([Solid(box_edges(-0.4, 1.45, 19.84, 1.55, 0.0, 0.2))])
        self.assertAlmostEqual(RV["into_posts"](through, self.FRAME), 0.30)     # the post's whole depth

    def test_beside_the_posts_or_no_geometry(self):
        # a member between the posts across (a cross beam between the inner faces) does not count
        between = El([Solid(box_edges(-0.1, -1.4, 0.1, 1.4, 0.0, 0.2))])
        self.assertEqual(RV["into_posts"](between, self.FRAME), 0.0)
        # no geometry: not measured, which is not the same as clear
        self.assertIsNone(RV["into_posts"](El(None), self.FRAME))


class Report(unittest.TestCase):

    def test_named_with_the_depth_and_the_typed_extension(self):
        res = {"into_posts": [("top_chord:L:0:0", 0.095)], "chord_extension_typed": False,
               "post_size": (0.3, 0.2, "geometry")}
        line = SY["problem_line"](res)
        self.assertIn("INTO THE POSTS", line)
        self.assertIn("top_chord:L:0:0 (95 mm)", line)
        self.assertNotIn("chord extension", line)
        res["chord_extension_typed"] = True
        self.assertIn("blank it to stop the chords at the post faces", SY["problem_line"](res))
        self.assertEqual(SY["problem_line"]({}), u"")

    def test_typed_extension_from_the_design_alone(self):
        # no geometry to measure, but the design says the chords run 140 mm into the posts
        res = {"into_posts": [], "chord_ext_into": 0.14, "post_size": (0.3, 0.2, "geometry")}
        line = SY["problem_line"](res)
        self.assertIn("running the chords 140 mm into the posts", line)
        self.assertNotIn("INTO THE POSTS - steel", line)

    def test_unmeasured_is_said(self):
        res = {"post_size": (0.2, 0.2, "parameters")}
        self.assertIn("the steel at the posts was not checked", SY["problem_line"](res))
        self.assertEqual(SY["post_line"](res), u"posts 200 x 200 mm (from the type's size parameters)")
        res = {"post_size": (0.3, 0.2, "geometry"), "steel_unchecked": 2}
        self.assertIn("the steel of 2 member(s) at the posts could not be read", SY["problem_line"](res))
        self.assertEqual(SY["post_line"](res), u"posts 300 x 200 mm (measured from their geometry)")


if __name__ == "__main__":
    unittest.main()
