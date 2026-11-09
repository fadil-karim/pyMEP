#!/usr/bin/env python3
"""Unit tests for pymep_bridge - the design defaults, the truss layouts,
the frame between two points, what an update changes, the design file
and the registry.

Run:  python3 tests/test_bridge.py
"""

import json
import math
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "lib"))

import pymep_bridge as BR


def design(**kw):
    d = dict(DEFAULT_OVERRIDES)
    d.update(kw)
    return BR.normalize_design(d)


DEFAULT_OVERRIDES = {"panel_mode": "count", "panels": 4, "truss_spacing_m": 3.0,
                     "deck_width_m": 2.7, "truss_height_m": 1.8}


def count(mems, role, side=None):
    return len([m for m in mems if m["role"] == role and (side is None or m["side"] == side)])


class Design(unittest.TestCase):

    def test_defaults_and_types(self):
        d = BR.normalize_design({})
        self.assertEqual(d["truss"], "warren_verticals")
        self.assertEqual(d["panel_mode"], "length")
        self.assertIsInstance(d["panels"], int)
        self.assertEqual(d["types"], {})
        d2 = BR.normalize_design({"panels": "6", "truss_height_m": "2", "end_posts": 0})
        self.assertEqual((d2["panels"], d2["truss_height_m"], d2["end_posts"]), (6, 2.0, False))

    def test_refusals(self):
        for bad in ({"truss": "bowstring"}, {"bracing": "k"}, {"panel_length_m": 0},
                    {"truss_height_m": -1}, {"panels": "many"}, {"panel_mode": "auto"}):
            with self.assertRaises(ValueError):
                BR.normalize_design(bad)


class Layout(unittest.TestCase):

    def test_by_count_and_by_length(self):
        lay = BR.layout(design(), 27.5)
        self.assertEqual(lay["n"], 4)
        self.assertAlmostEqual(lay["L"], 6.875)
        self.assertEqual(len(lay["points"]), 5)
        lay2 = BR.layout(design(panel_mode="length", panel_length_m=1.92), 27.5)
        self.assertEqual(lay2["n"], 14)                       # 27.5 / 1.92 = 14.3
        self.assertAlmostEqual(lay2["L"], 27.5 / 14)
        self.assertAlmostEqual(lay2["points"][-1], 27.5)

    def test_extensions(self):
        lay = BR.layout(design(start_extension_m=0.5, end_extension_m=0.25), 10.0)
        self.assertAlmostEqual(lay["s0"], -0.5)
        self.assertAlmostEqual(lay["s1"], 10.25)
        self.assertAlmostEqual(lay["total"], 10.75)

    def test_zero_span_refused(self):
        with self.assertRaises(ValueError):
            BR.layout(design(), 0.0)


class Members(unittest.TestCase):

    def test_warren_with_verticals(self):
        mems = BR.members(design(), 20.0)
        self.assertEqual(count(mems, "top_chord"), 2)
        self.assertEqual(count(mems, "bottom_chord"), 2)
        self.assertEqual(count(mems, "end_post"), 4)
        self.assertEqual(count(mems, "vertical"), 2 * 3)          # interior panel points
        self.assertEqual(count(mems, "diagonal"), 2 * 4)
        self.assertEqual(count(mems, "crossbeam"), 5)
        self.assertEqual(count(mems, "bracing"), 4)
        keys = [m["key"] for m in mems]
        self.assertEqual(len(keys), len(set(keys)))                 # stable unique keys
        # a diagonal zigzags: panel 0 bottom->top, panel 1 top->bottom
        d = [m for m in mems if m["role"] == "diagonal" and m["side"] == "L"]
        self.assertAlmostEqual(d[0]["p1"][2], -0.35)
        self.assertAlmostEqual(d[0]["p2"][2], -0.35 + 1.8)
        self.assertAlmostEqual(d[1]["p1"][2], -0.35 + 1.8)
        # left truss at +1.5, right at -1.5
        self.assertAlmostEqual([m for m in mems if m["key"] == u"top_chord:L:0:0"][0]["p1"][1], 1.5)
        self.assertAlmostEqual([m for m in mems if m["key"] == u"top_chord:R:0:0"][0]["p1"][1], -1.5)

    def test_other_trusses(self):
        plain = BR.members(design(truss="warren"), 20.0)
        self.assertEqual(count(plain, "vertical"), 0)
        self.assertEqual(count(plain, "end_post"), 4)
        x = BR.members(design(truss="x"), 20.0)
        self.assertEqual(count(x, "diagonal"), 2 * 4 * 2)
        v = BR.members(design(truss="vierendeel"), 20.0)
        self.assertEqual(count(v, "diagonal"), 0)
        self.assertEqual(count(v, "vertical"), 6)
        pratt = [m for m in BR.members(design(truss="pratt"), 20.0)
                 if m["role"] == "diagonal" and m["side"] == "L"]
        # left half: top at s_a down to bottom at s_b; right half mirrored
        self.assertGreater(pratt[0]["p1"][2], pratt[0]["p2"][2])
        self.assertLess(pratt[3]["p1"][2], pratt[3]["p2"][2])
        howe = [m for m in BR.members(design(truss="howe"), 20.0)
                if m["role"] == "diagonal" and m["side"] == "L"]
        self.assertLess(howe[0]["p1"][2], howe[0]["p2"][2])

    def test_crossbeams_and_bracing(self):
        two = BR.members(design(crossbeams_per_panel=2, bracing="x"), 20.0)
        self.assertEqual(count(two, "crossbeam"), 9)
        self.assertEqual(count(two, "bracing"), 16)
        none = BR.members(design(bracing="none", end_posts=False), 20.0)
        self.assertEqual(count(none, "bracing"), 0)
        self.assertEqual(count(none, "end_post"), 0)
        cb = [m for m in two if m["role"] == "crossbeam"][0]
        self.assertAlmostEqual(cb["p1"][1], 1.5)
        self.assertAlmostEqual(cb["p2"][1], -1.5)
        self.assertAlmostEqual(cb["p1"][2], -0.25)
        self.assertEqual(cb["stype"], "beam")
        self.assertEqual([m for m in two if m["role"] == "bracing"][0]["stype"], "brace")

    def test_summary(self):
        s = BR.summary(design(), 20.0)
        self.assertEqual(s["panels"], 4)
        self.assertEqual(s["members"], 2 + 2 + 4 + 6 + 8 + 5 + 4)
        self.assertAlmostEqual(s["by_role"]["top_chord"]["length_m"], 40.0)
        self.assertAlmostEqual(s["deck_area_m2"], 54.0)


class Frame(unittest.TestCase):

    def test_frame_and_world(self):
        f = BR.frame_from_points((100.0, 200.0, 4.715), (100.0, 227.5, 4.863))
        self.assertAlmostEqual(f["span"], 27.5)
        self.assertAlmostEqual(f["gradient"], 0.148 / 27.5)
        self.assertAlmostEqual(f["angle"], math.pi / 2)
        x, y, z = BR.to_world(f, 10.0, 1.5, -0.35)
        self.assertAlmostEqual(x, 100.0 - 1.5)         # left of a northward span is west
        self.assertAlmostEqual(y, 210.0)
        self.assertAlmostEqual(z, 4.715 + 0.148 * 10 / 27.5 - 0.35)
        self.assertAlmostEqual(BR.deck_z(f, 27.5), 4.863)
        self.assertAlmostEqual(BR.deck_z(f, -1.0), 4.715 - 0.148 / 27.5)

    def test_world_members_follow_the_slope(self):
        f = BR.frame_from_points((0.0, 0.0, 10.0), (20.0, 0.0, 12.0))
        wm = BR.world_members(design(deck_top_offset_m=0.1), f)
        top = [m for m in wm if m["key"] == u"top_chord:L:0:0"][0]
        self.assertAlmostEqual(top["p1"][2], 10.0 + 0.1 - 0.35 + 1.8)
        self.assertAlmostEqual(top["p2"][2], 12.0 + 0.1 - 0.35 + 1.8)
        self.assertAlmostEqual(top["p1"][1], 1.5)
        cb = [m for m in wm if m["role"] == "crossbeam"][2]
        self.assertAlmostEqual(cb["p1"][0], 10.0)
        self.assertAlmostEqual(cb["p1"][2], 11.0 + 0.1 - 0.25)

    def test_world_deck(self):
        f = BR.frame_from_points((0.0, 0.0, 10.0), (20.0, 0.0, 12.0))
        deck = BR.world_deck(design(start_extension_m=0.5), f)
        self.assertEqual(len(deck["corners"]), 4)
        self.assertAlmostEqual(deck["corners"][0][0], -0.5)
        self.assertAlmostEqual(deck["corners"][0][1], 1.35)
        self.assertAlmostEqual(deck["tail"][0], -0.5)
        self.assertAlmostEqual(deck["head"][0], 20.0)
        self.assertAlmostEqual(deck["tail_top_z"], 10.0 - 0.05)
        self.assertAlmostEqual(deck["slope"], 0.1)
        self.assertAlmostEqual(deck["length_m"], 20.5)

    def test_same_point_refused(self):
        with self.assertRaises(ValueError):
            BR.frame_from_points((1.0, 1.0, 0.0), (1.0, 1.0, 5.0))


class Updates(unittest.TestCase):

    def test_diff(self):
        d = design()
        old = [m["key"] for m in BR.members(d, 20.0)]
        new = BR.members(design(panels=5), 20.0)
        diff = BR.diff_members(old, new)
        self.assertTrue(all(k in old for k, _m in diff["update"]))
        self.assertTrue(any(m["key"] == u"vertical:L:4:0" for m in diff["create"]))
        self.assertIn(u"crossbeam:C:4:0", [k for k, _m in diff["update"]])
        same = BR.diff_members(old, BR.members(d, 25.0))
        self.assertEqual((len(same["create"]), len(same["delete"])), (0, 0))
        self.assertEqual(len(same["update"]), len(old))

    def test_rigid_motion(self):
        a = BR.frame_from_points((0.0, 0.0, 10.0), (20.0, 0.0, 12.0))
        b = BR.frame_from_points((5.0, 5.0, 11.0), (5.0, 25.0, 13.0))
        dx, dy, dz, da = BR.rigid_motion(a, b)
        self.assertEqual((dx, dy, dz), (5.0, 5.0, 1.0))
        self.assertAlmostEqual(da, math.pi / 2)
        c = BR.frame_from_points((0.0, 0.0, 10.0), (21.0, 0.0, 12.0))
        self.assertIsNone(BR.rigid_motion(a, c))
        d = BR.frame_from_points((0.0, 0.0, 10.0), (20.0, 0.0, 12.5))
        self.assertIsNone(BR.rigid_motion(a, d))
        self.assertFalse(BR.frame_changed(a, BR.frame_from_points((0.0, 0.0, 10.0), (20.0, 0.0, 12.0))))
        self.assertTrue(BR.frame_changed(a, d))
        self.assertTrue(BR.frame_changed(a, b))


class Files(unittest.TestCase):

    def test_design_file_round_trip(self):
        d = design(truss="pratt")
        src = {"start_id": 11, "end_id": 12, "level_param": u"Deck Level", "level_mode": "shared"}
        text = BR.design_file_text(u"Bridge 1", d, src, extra={"saved": "now"})
        back = BR.parse_design_file(text)
        self.assertEqual(back["name"], u"Bridge 1")
        self.assertEqual(back["design"]["truss"], "pratt")
        self.assertEqual(back["source"]["level_param"], u"Deck Level")
        with self.assertRaises(ValueError):
            BR.parse_design_file(u"{not json")
        with self.assertRaises(ValueError):
            BR.parse_design_file(json.dumps({"name": "x"}))
        with self.assertRaises(ValueError):
            BR.parse_design_file(json.dumps({"kind": "pymep-bridge", "name": "x", "design": {},
                                             "source": {"start_id": 1}}))

    def test_registry(self):
        folder = tempfile.mkdtemp()
        try:
            path = os.path.join(folder, "sub", "bridges.json")
            reg = BR.load_registry(path)
            self.assertEqual(reg, {"bridges": {}})
            self.assertEqual(BR.next_name(reg), u"Bridge 1")
            f = BR.frame_from_points((0.0, 0.0, 10.0), (20.0, 0.0, 12.0))
            rec = BR.record(u"Bridge 1", design(), {"start_id": 1, "end_id": 2},
                            f, {u"top_chord:L:0:0": 501}, 600, u"2026-11-09 10:00")
            reg["bridges"][u"Bridge 1"] = rec
            BR.save_registry(path, reg)
            back = BR.load_registry(path)
            self.assertEqual(back["bridges"][u"Bridge 1"]["floor"], 600)
            self.assertEqual(BR.next_name(back), u"Bridge 2")
            f2 = BR.frame_from_record(back["bridges"][u"Bridge 1"])
            self.assertAlmostEqual(f2["span"], 20.0)
            self.assertAlmostEqual(f2["gradient"], 0.1)
            self.assertAlmostEqual(f2["u"][0], 1.0)
            self.assertFalse(BR.frame_changed(f, f2))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_fill_page(self):
        page = u"<script>var D = /*__BRIDGE_DATA__*/null;</script>"
        out = BR.fill_page(page, {"a": u"</script>", "b": 1})
        self.assertIn(u'"b": 1', out)
        self.assertNotIn(u"</script>\"", out)              # closing tag escaped
        self.assertIn(u"<\\/script>", out)
        with self.assertRaises(ValueError):
            BR.fill_page(u"<html></html>", {})


if __name__ == "__main__":
    unittest.main()
