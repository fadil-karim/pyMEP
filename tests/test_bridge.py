#!/usr/bin/env python3
"""Unit tests for pymep_bridge - the design defaults, the frame from four
posts, the layouts, the members, what an update changes, the design
file and the registry.

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


def post(x, y, zb, zt, size=0.2):
    return {"x": x, "y": y, "z_bottom": zb, "z_top": zt, "size_m": size}


def posts(span=20.0, width=3.0, z0=10.0, z1=12.0, h=2.3, size=0.2, x0=0.0, y0=0.0):
    """Two pairs along +x: start at x0, end at x0 + span; left is +y."""
    return ([post(x0, y0 + width / 2, z0, z0 + h, size), post(x0, y0 - width / 2, z0, z0 + h, size)],
            [post(x0 + span, y0 + width / 2, z1, z1 + h, size), post(x0 + span, y0 - width / 2, z1, z1 + h, size)])


def design(**kw):
    # the geometry tests set the trusses on the posts' outside faces; the
    # built-in default (centre) has its own test
    # the geometry tests run the chords to the posts' outer faces (as the
    # drawing measures); the trusses are always on the post centres
    d = {"panel_mode": "count", "panels": 4, "truss": "warren_verticals", "chord_extension_m": 0.1,
         "brace_end_offset_m": 0.0, "bottom_chord_offset_m": 0.0, "bottom_chord_cutback_m": 0.0,
         "top_chord_cutback_m": 0.0}
    d.update(kw)
    return BR.normalize_design(d)


def count(mems, role, side=None):
    return len([m for m in mems if m["role"] == role and (side is None or m["side"] == side)])


def by_key(mems, key):
    return [m for m in mems if m["key"] == key][0]


FRAME = BR.frame_from_posts(*posts())


class Design(unittest.TestCase):

    def test_defaults(self):
        d = BR.normalize_design({})
        self.assertEqual(d["truss"], "warren")
        self.assertEqual(d["panel_length_m"], 2.5)
        self.assertIsNone(d["level_start_m"])
        self.assertIsNone(d["height_m"])
        self.assertEqual(d["truss_plane"], "centre")
        self.assertEqual(d["bracing"], "zigzag")
        self.assertFalse(d["end_posts"])
        self.assertEqual(d["stiffeners_across"], 1)
        d2 = BR.normalize_design({"height_m": "2.5", "plan_bays": "6", "split_x": 1,
                                  "top_chord_split_m": "6"})
        self.assertEqual((d2["height_m"], d2["plan_bays"], d2["split_x"], d2["top_chord_split_m"]),
                         (2.5, 6, True, 6.0))

    def test_refusals(self):
        for bad in ({"truss": "bowstring"}, {"bracing": "k"}, {"brace_end_offset_m": -1},
                    {"panel_length_m": 0}, {"height_m": 0}, {"panels": "many"},
                    {"floor_thickness_m": -1}, {"bottom_chord_split_m": -2}):
            with self.assertRaises(ValueError):
                BR.normalize_design(bad)


class Frame(unittest.TestCase):

    def test_from_posts(self):
        f = FRAME
        self.assertAlmostEqual(f["span"], 20.0)
        self.assertAlmostEqual(f["tL"], 1.5)
        self.assertAlmostEqual(f["tR"], -1.5)
        self.assertAlmostEqual(f["post_size"], 0.2)
        self.assertAlmostEqual(f["post_height"], 2.3)
        self.assertEqual((f["z0"], f["z1"]), (10.0, 12.0))
        self.assertAlmostEqual(f["gradient"], 0.1)
        self.assertEqual(f["origin"], (0.0, 0.0))
        self.assertAlmostEqual(f["angle"], 0.0)

    def test_midpoints_give_centreline_and_direction(self):
        """The pair midpoints are the deck centreline; posts need not be
        symmetric about anything else."""
        s = [post(-1.0, 0.0, 10.0, 12.0), post(2.0, 0.0, 10.0, 12.0)]       # midpoint x = 0.5
        e = [post(-1.0, 20.0, 10.0, 12.0), post(2.0, 20.0, 10.0, 12.0)]     # midpoint x = 0.5
        f = BR.frame_from_posts(s, e)
        self.assertEqual(f["origin"], (0.5, 0.0))
        self.assertAlmostEqual(f["span"], 20.0)
        self.assertAlmostEqual(f["angle"], math.pi / 2)        # runs north
        self.assertAlmostEqual(f["tL"], 1.5)
        self.assertAlmostEqual(f["tR"], -1.5)
        x, y, z = BR.to_world(f, 10.0, 1.5, 0.0)
        self.assertAlmostEqual(x, -1.0)                         # left of north is west
        self.assertAlmostEqual(y, 10.0)
        self.assertAlmostEqual(z, 10.0)
        self.assertAlmostEqual(BR.to_world(f, 20.0, 0.0, 0.0)[2], 10.0)
        corners = BR.deck_profile(design(), f)
        ts = sorted(set(round(t, 6) for _s, t in corners))
        self.assertAlmostEqual(ts[0], -ts[1])                   # deck centred on the midpoints

    def test_refusals(self):
        with self.assertRaises(ValueError):
            BR.frame_from_posts([post(0, 1, 0, 2)], [post(5, 1, 0, 2), post(5, -1, 0, 2)])
        with self.assertRaises(ValueError):
            BR.frame_from_posts([post(0, 1, 0, 2), post(0, 1, 0, 2)], [post(5, 1, 0, 2), post(5, -1, 0, 2)])
        with self.assertRaises(ValueError):
            BR.frame_from_posts(*posts(span=0.0))

    def test_planes_and_height(self):
        d = design()
        self.assertEqual(BR.planes(d, FRAME), (1.5, -1.5))                        # always the post centres
        self.assertEqual(BR.planes(design(truss_plane="inside"), FRAME), (1.5, -1.5))
        self.assertEqual(BR.planes(design(truss_plane="outside"), FRAME), (1.5, -1.5))
        self.assertEqual(BR.normalize_design({"truss_plane": "outside"})["truss_plane"], "centre")
        self.assertAlmostEqual(BR.height(d, FRAME), 2.3)
        self.assertAlmostEqual(BR.height(design(height_m=1.8), FRAME), 1.8)
        # a typed height is the truss height from the bottom chord line, so the
        # frame moves with the chords; blank keeps the top chord at the post top
        self.assertAlmostEqual(BR.height(design(height_m=1.8, bottom_chord_offset_m=-0.1), FRAME), 1.7)
        self.assertAlmostEqual(BR.truss_height(design(height_m=1.8, bottom_chord_offset_m=-0.1), FRAME), 1.8)
        self.assertAlmostEqual(BR.height(design(bottom_chord_offset_m=-0.1), FRAME), 2.3)
        self.assertAlmostEqual(BR.truss_height(design(bottom_chord_offset_m=-0.1), FRAME), 2.4)

    def test_from_points_helper(self):
        f = BR.frame_from_points((0.0, 0.0, 5.0), (0.0, 10.0, 6.0), width=4.0)
        self.assertAlmostEqual(f["span"], 10.0)
        self.assertAlmostEqual(f["tL"], 2.0)
        self.assertAlmostEqual(f["angle"], math.pi / 2)


class Layout(unittest.TestCase):

    def test_chords_and_deck_extents(self):
        lay = BR.layout(design(), FRAME)
        self.assertAlmostEqual(lay["s0"], -0.1)              # outer face of the posts
        self.assertAlmostEqual(lay["s1"], 20.1)
        self.assertEqual(lay["n"], 4)
        self.assertAlmostEqual(lay["d0"], -0.1)              # bays between the side frames' end nodes
        self.assertAlmostEqual(lay["d1"], 20.1)
        self.assertEqual(lay["bays"], 4)
        self.assertEqual(len(lay["stations"]), 5)
        lay2 = BR.layout(design(chord_extension_m=0.0, plan_bays=10, deck_clearance_m=0.0), FRAME)
        self.assertAlmostEqual(lay2["s0"], 0.0)
        self.assertEqual(lay2["bays"], 10)
        self.assertAlmostEqual(lay2["d0"], 0.0)                 # bays always start at the post centreline
        lay3 = BR.layout(design(panel_mode="length", panel_length_m=1.92), FRAME)
        self.assertEqual(lay3["n"], 11)                      # 20.2 / 1.92 = 10.5 -> 11

    def test_pieces(self):
        self.assertEqual(BR.pieces(0.0, 20.0, 0.0), [(0.0, 20.0)])
        self.assertEqual(BR.pieces(0.0, 20.0, 25.0), [(0.0, 20.0)])
        ps = BR.pieces(0.0, 20.0, 6.0)
        self.assertEqual(len(ps), 4)
        self.assertAlmostEqual(ps[0][1], 5.0)
        self.assertAlmostEqual(ps[-1][1], 20.0)
        self.assertEqual(len(BR.pieces(0.0, 12.0, 6.0)), 2)


class Members(unittest.TestCase):

    def test_trusses_sit_on_the_planes(self):
        mems = BR.members(design(truss="pratt"), FRAME)
        self.assertEqual(count(mems, "top_chord"), 2)
        self.assertEqual(count(mems, "bottom_chord"), 2)
        self.assertEqual(count(mems, "end_post"), 0)           # the posts are there already
        self.assertEqual(count(mems, "vertical"), 6)
        self.assertEqual(count(mems, "diagonal"), 8)
        top = by_key(mems, u"top_chord:L:0:0")
        self.assertAlmostEqual(top["p1"][1], 1.5)                # on the post centre
        self.assertAlmostEqual(top["p1"][2], 2.3)                # post height
        self.assertAlmostEqual(top["p1"][0], -0.1)
        bot = by_key(mems, u"bottom_chord:R:0:0")
        self.assertAlmostEqual(bot["p1"][1], -1.5)
        self.assertAlmostEqual(bot["p1"][2], 0.0)
        self.assertEqual(top["zjust"], "top")                    # the truss height is to the top of steel
        with_posts = BR.members(design(end_posts=True, truss="warren"), FRAME)
        self.assertEqual(count(with_posts, "end_post"), 4)
        self.assertEqual(count(with_posts, "vertical"), 0)

    def test_warren_as_drawn(self):
        """22500 span, 2500 panels, top nodes at 1250 + k * 2500, 1800 high,
        diagonals at 55 degrees - the drawing."""
        f = BR.frame_from_posts(*posts(span=22.5, h=1.8, size=0.0))
        d = design(truss="warren", panel_mode="length", panel_length_m=2.5, chord_extension_m=0.0,
                   deck_clearance_m=0.0)
        mems = BR.members(d, f)
        lay = BR.layout(d, f)
        self.assertEqual(lay["n"], 9)
        self.assertEqual(count(mems, "vertical"), 0)
        diags = [m for m in mems if m["role"] == "diagonal" and m["side"] == "L"]
        self.assertEqual(len(diags), 18)
        self.assertEqual((diags[0]["p1"][0], diags[0]["p1"][2]), (0.0, 0.0))
        self.assertAlmostEqual(diags[0]["p2"][0], 1.25)
        self.assertAlmostEqual(diags[0]["p2"][2], 1.8)
        self.assertAlmostEqual(diags[1]["p2"][0], 2.5)
        self.assertAlmostEqual(diags[1]["p2"][2], 0.0)
        angle = math.degrees(math.atan2(1.8, 1.25))
        self.assertAlmostEqual(angle, 55.2, places=1)
        tops = sorted(set(round(m["p2"][0], 6) for m in diags if m["p2"][2] > 1.0))
        self.assertEqual(tops[:3], [1.25, 3.75, 6.25])
        withv = BR.members(design(truss="warren_verticals", panel_mode="length", panel_length_m=2.5,
                                  chord_extension_m=0.0), f)
        self.assertEqual(count(withv, "vertical", "L"), 8 + 9)        # bottom nodes + top nodes

    def test_piece_list(self):
        # typed lengths from the start; the None piece takes the rest
        self.assertEqual(BR.piece_spans(0.0, 20.0, [6.0, None]), [(0.0, 6.0), (6.0, 20.0)])
        sp = BR.piece_spans(0.0, 20.0, [6.0, None, 4.0])
        self.assertEqual(len(sp), 3)
        self.assertAlmostEqual(sp[1][0], 6.0)
        self.assertAlmostEqual(sp[1][1], 16.0)
        self.assertAlmostEqual(sp[2][1], 20.0)
        self.assertEqual(BR.piece_spans(0.0, 20.0, [None, 5.0]), [(0.0, 15.0), (15.0, 20.0)])
        self.assertEqual(BR.piece_spans(0.0, 20.0, [], 6.0), BR.pieces(0.0, 20.0, 6.0))   # legacy equal pieces
        with self.assertRaises(ValueError):
            BR.piece_spans(0.0, 20.0, [12.0, 8.0, None])
        # normalising: the last piece is the variable one unless another is marked
        d = BR.normalize_design({"top_chord_pieces": [6, 6], "bottom_chord_pieces": [None, 5.0]})
        self.assertEqual(d["top_chord_pieces"], [6.0, 6.0, None])
        self.assertEqual(d["bottom_chord_pieces"], [None, 5.0])
        self.assertEqual(BR.normalize_design({"top_chord_pieces": [None]})["top_chord_pieces"], [])
        with self.assertRaises(ValueError):
            BR.normalize_design({"top_chord_pieces": [None, None, 3.0]})
        with self.assertRaises(ValueError):
            BR.normalize_design({"top_chord_pieces": [0, 3.0]})

    def test_mirror_plan_bracing(self):
        def braces(**kw):
            # as undirected segments, so a mirrored piece compares equal to the original
            return sorted(tuple(sorted([(round(m["p1"][0], 6), round(m["p1"][1], 6)), (round(m["p2"][0], 6), round(m["p2"][1], 6))]))
                          for m in BR.members(design(**kw), FRAME) if m["role"] == "bracing")
        base = BR.members(design(plan_bays=5), FRAME)            # odd: a single-bay diagonal at the end
        st = BR.layout(design(plan_bays=5), FRAME)["stations"]
        first = by_key(base, u"bracing:C:0:0")
        self.assertAlmostEqual(first["p1"][0], st[0])
        self.assertAlmostEqual(first["p1"][1], 1.5)              # starts on the left side beam
        # across: the same stations, the sides swapped
        across = by_key(BR.members(design(plan_bays=5, brace_mirror_across=True), FRAME), u"bracing:C:0:0")
        self.assertAlmostEqual(across["p1"][0], st[0])
        self.assertAlmostEqual(across["p1"][1], -1.5)
        # along: end for end - the first diagonal now starts at the far end and
        # the single-bay piece moves to the start
        along = BR.members(design(plan_bays=5, brace_mirror_span=True), FRAME)
        self.assertAlmostEqual(by_key(along, u"bracing:C:0:0")["p1"][0], st[-1])
        single = by_key(along, u"bracing:C:2:0")
        self.assertEqual(sorted([round(single["p1"][0], 6), round(single["p2"][0], 6)]), [round(st[0], 6), round(st[1], 6)])
        # every brace still runs cross beam to cross beam
        for m in along:
            if m["role"] == "bracing":
                self.assertTrue(any(abs(m["p1"][0] - s) < 1e-9 for s in st))
                self.assertTrue(any(abs(m["p2"][0] - s) < 1e-9 for s in st))
        # both = a half turn; twice the same mirror = the original
        both = braces(plan_bays=5, brace_mirror_span=True, brace_mirror_across=True)
        self.assertNotEqual(both, braces(plan_bays=5))
        self.assertEqual(len(both), len(braces(plan_bays=5)))
        d = BR.normalize_design({})
        self.assertFalse(d["brace_mirror_span"])
        self.assertFalse(d["brace_mirror_across"])
        # the X is symmetric both ways: mirroring leaves it as it was
        self.assertEqual(braces(bracing="x", brace_mirror_span=True, brace_mirror_across=True), braces(bracing="x"))

    def test_bottom_chord_cutback(self):
        # the bottom chords stop the cutback short of the post faces at each
        # end; the top chords, the diagonals and the cross beams do not move
        d = design(bottom_chord_cutback_m=0.01)
        self.assertEqual(BR.chord_extent(d, FRAME, "bottom_chord"), (-0.1 + 0.01, 20.1 - 0.01))
        self.assertEqual(BR.chord_extent(d, FRAME, "top_chord"), (-0.1, 20.1))
        mems = BR.members(d, FRAME)
        bc = by_key(mems, u"bottom_chord:L:0:0")
        self.assertAlmostEqual(bc["p1"][0], -0.09)
        self.assertAlmostEqual(bc["p2"][0], 20.09)
        self.assertAlmostEqual(by_key(mems, u"top_chord:L:0:0")["p1"][0], -0.1)
        self.assertAlmostEqual(by_key(mems, u"crossbeam:C:0:0")["p1"][0], -0.1)
        # typed pieces are measured within the cut-back chord: the variable piece takes the rest
        p = BR.members(design(bottom_chord_cutback_m=0.01, bottom_chord_pieces=[6.0, None]), FRAME)
        self.assertAlmostEqual(by_key(p, u"bottom_chord:L:0:0")["p2"][0], -0.09 + 6.0)
        self.assertAlmostEqual(by_key(p, u"bottom_chord:L:1:0")["p2"][0], 20.09)
        self.assertAlmostEqual(BR.normalize_design({})["bottom_chord_cutback_m"], 0.01)   # 10 mm by default
        with self.assertRaises(ValueError):
            BR.normalize_design({"bottom_chord_cutback_m": -0.01})

    def test_top_chord_cutback_and_free_ends(self):
        d = design(top_chord_cutback_m=0.02, bottom_chord_cutback_m=0.01)
        self.assertEqual(BR.chord_extent(d, FRAME, "top_chord"), (-0.1 + 0.02, 20.1 - 0.02))
        mems = BR.members(d, FRAME)
        tc = by_key(mems, u"top_chord:L:0:0")
        self.assertAlmostEqual(tc["p1"][0], -0.08)
        self.assertAlmostEqual(tc["p2"][0], 20.08)
        self.assertAlmostEqual(by_key(mems, u"bottom_chord:L:0:0")["p1"][0], -0.09)
        self.assertAlmostEqual(BR.normalize_design({})["top_chord_cutback_m"], 0.01)
        # Revit is told not to join the chords' outer ends (at the posts); splices keep their joins
        self.assertEqual(tc["free_ends"], [0, 1])                 # one piece: both ends at posts
        split = BR.members(design(top_chord_pieces=[6.0, None, 6.0]), FRAME)
        self.assertEqual(by_key(split, u"top_chord:L:0:0")["free_ends"], [0])
        self.assertEqual(by_key(split, u"top_chord:L:1:0")["free_ends"], [])
        self.assertEqual(by_key(split, u"top_chord:R:2:0")["free_ends"], [1])
        self.assertNotIn("free_ends", by_key(split, u"crossbeam:C:1:0"))
        with self.assertRaises(ValueError):
            BR.normalize_design({"top_chord_cutback_m": -0.01})

    def test_resize_variable_piece(self):
        # the change is taken equally from the pieces either side
        self.assertEqual(BR.resize_variable([6.0, None, 4.0], 20.0, 11.0), [5.5, None, 3.5])
        # a longer variable gives back equally
        self.assertEqual(BR.resize_variable([6.0, None, 4.0], 20.0, 8.0), [7.0, None, 5.0])
        # one side short: cut to 500, the other side takes the rest
        r = BR.resize_variable([6.0, None, 0.8], 20.0, 14.2)       # variable 13.2 -> 14.2
        self.assertAlmostEqual(r[2], 0.5)
        self.assertAlmostEqual(r[0], 6.0 - 0.7)
        self.assertEqual(r[1], None)
        # at a chord end there is one neighbour, which takes it all
        self.assertEqual(BR.resize_variable([None, 5.0, 5.0], 20.0, 12.0), [None, 3.0, 5.0])
        self.assertEqual(BR.resize_variable([5.0, 5.0, None], 20.0, 9.0), [5.0, 6.0, None])
        # the variable comes out at exactly the typed length
        sp = BR.piece_spans(0.0, 20.0, BR.resize_variable([3.0, 2.0, None, 4.0, 1.5], 20.0, 10.0))
        self.assertAlmostEqual(sp[2][1] - sp[2][0], 10.0)
        self.assertAlmostEqual(sp[0][1] - sp[0][0], 3.0)           # pieces not next to it are left alone
        self.assertAlmostEqual(sp[4][1] - sp[4][0], 1.5)
        for bad in (([1.0, None, 1.0], 10.0, 9.5),                 # neighbours can give 1.0 at most
                    ([6.0, None, 4.0], 20.0, 0.3),                 # below the minimum
                    ([6.0, 4.0], 10.0, 5.0)):                      # no variable piece
            with self.assertRaises(ValueError):
                BR.resize_variable(*bad)

    def test_variable_piece_stretches(self):
        mems = BR.members(design(top_chord_pieces=[6.0, None], bottom_chord_pieces=[None, 4.0, 4.0]), FRAME)
        self.assertEqual(count(mems, "top_chord", "L"), 2)
        t0, t1 = by_key(mems, u"top_chord:L:0:0"), by_key(mems, u"top_chord:L:1:0")
        self.assertAlmostEqual(t0["p1"][0], -0.1)
        self.assertAlmostEqual(t0["p2"][0], 5.9)
        self.assertAlmostEqual(t1["p2"][0], 20.1)
        self.assertEqual(count(mems, "bottom_chord", "L"), 3)
        b0 = by_key(mems, u"bottom_chord:L:0:0")
        self.assertAlmostEqual(b0["p2"][0], 20.1 - 8.0)             # the first piece is the variable one here
        # the posts move apart: the typed pieces keep their lengths, the variable piece stretches
        s, e = posts(span=25.0)
        far = BR.members(design(top_chord_pieces=[6.0, None], bottom_chord_pieces=[None, 4.0, 4.0]), BR.frame_from_posts(s, e))
        self.assertAlmostEqual(by_key(far, u"top_chord:L:0:0")["p2"][0], 5.9)
        self.assertAlmostEqual(by_key(far, u"top_chord:L:1:0")["p2"][0], 25.1)
        self.assertAlmostEqual(by_key(far, u"bottom_chord:L:0:0")["p2"][0], 25.1 - 8.0)
        self.assertAlmostEqual(by_key(far, u"bottom_chord:L:2:0")["p1"][0], 25.1 - 4.0)
        with self.assertRaises(ValueError):
            BR.members(design(top_chord_pieces=[15.0, 10.0, None]), FRAME)

    def test_split_chords(self):
        mems = BR.members(design(top_chord_split_m=6.0, bottom_chord_split_m=12.0), FRAME)
        self.assertEqual(count(mems, "top_chord", "L"), 4)       # 20.2 / 6 -> 4 pieces of 5.05
        self.assertEqual(count(mems, "bottom_chord", "L"), 2)
        t1 = by_key(mems, u"top_chord:L:1:0")
        self.assertAlmostEqual(t1["p1"][0], -0.1 + 5.05)
        self.assertAlmostEqual(t1["p2"][0], -0.1 + 10.1)
        b1 = by_key(mems, u"bottom_chord:L:1:0")
        self.assertAlmostEqual(b1["p1"][0], -0.1 + 10.1)
        self.assertAlmostEqual(b1["p2"][0], 20.1)

    def test_plan_members_sit_under_the_floor(self):
        mems = BR.members(design(floor_thickness_m=0.15, deck_top_offset_m=0.0), FRAME)
        cb = by_key(mems, u"crossbeam:C:0:0")
        self.assertAlmostEqual(cb["p1"][0], -0.1)                # on the first frame node (chord end here)
        self.assertAlmostEqual(cb["p1"][1], 1.4)                 # within the post footprint: between the inner faces
        self.assertAlmostEqual(cb["p2"][1], -1.4)
        cb1 = by_key(mems, u"crossbeam:C:1:0")
        self.assertAlmostEqual(cb1["p1"][1], 1.5)                # the rest between the truss planes (post centres)
        self.assertAlmostEqual(cb1["p2"][1], -1.5)
        self.assertAlmostEqual(cb["p1"][2], -0.15)
        self.assertEqual(cb["zjust"], "top")
        self.assertEqual(cb["stype"], "beam")
        self.assertEqual(count(mems, "crossbeam"), 5)
        # the default: one diagonal at a time over two bays, side beam to side
        # beam, alternating - the drawing - always cut at the cross beam it
        # crosses, the two pieces meeting on it mid-width
        self.assertEqual(count(mems, "bracing"), 4)
        br = by_key(mems, u"bracing:C:0:0")
        self.assertAlmostEqual(br["p1"][0], -0.1)
        self.assertAlmostEqual(br["p1"][1], 1.5)
        self.assertAlmostEqual(br["p2"][0], -0.1 + 20.2 / 4.0)    # on the first cross beam
        self.assertAlmostEqual(br["p2"][1], 0.0)
        brb = by_key(mems, u"bracing:C:0:1")
        self.assertAlmostEqual(brb["p2"][0], -0.1 + 20.2 / 2.0)   # two bays on
        self.assertAlmostEqual(brb["p2"][1], -1.5)
        br2 = by_key(mems, u"bracing:C:1:0")
        self.assertAlmostEqual(br2["p1"][1], -1.5)                # alternates
        self.assertAlmostEqual(by_key(mems, u"bracing:C:1:1")["p2"][1], 1.5)
        self.assertAlmostEqual(br["p1"][2], 0.0)                 # at the bottom chords' centre (no depth known here)
        self.assertEqual((br["zjust"], br["stype"]), ("centre", "brace"))
        self.assertEqual(count(BR.members(design(split_x=False), FRAME), "bracing"), 4)   # the flag is for the X only
        # an odd number of bays: the last diagonal takes the bay that is left
        odd = BR.members(design(plan_bays=5), FRAME)
        self.assertEqual(count(odd, "bracing"), 5)                # 2 + 2 + 1 pieces
        self.assertAlmostEqual(by_key(odd, u"bracing:C:2:0")["p2"][0], 20.1)
        one = BR.members(design(brace_bays=1), FRAME)
        self.assertEqual(count(one, "bracing"), 4)
        self.assertAlmostEqual(by_key(one, u"bracing:C:1:0")["p1"][1], -1.5)
        x = BR.members(design(bracing="x", split_x=False), FRAME)
        self.assertEqual(count(x, "bracing"), 8)                  # X in 4 bays
        split = BR.members(design(bracing="x", split_x=True), FRAME)
        self.assertEqual(count(split, "bracing"), 16)
        mid = by_key(split, u"bracing:C:0:0")["p2"]
        self.assertAlmostEqual(mid[0], -0.1 + 20.2 / 8.0)       # the X centre of the first bay
        self.assertAlmostEqual(mid[1], 0.0)
        self.assertEqual(count(BR.members(design(bracing="none"), FRAME), "bracing"), 0)
        self.assertEqual(BR.normalize_design({"brace_bays": 0})["brace_bays"], 1)
        more = BR.members(design(plan_bays=8), FRAME)
        self.assertEqual(count(more, "crossbeam"), 9)

    def test_stiffeners_and_railings(self):
        d = design(stiffener_mode="count", stiffeners_across=2, stiffener_gap_m=0.1,
                   types={"railing": "77"}, railing_inset_m=0.1, floor_thickness_m=0.15)
        mems = BR.members(d, FRAME)
        st = [m for m in mems if m["role"] == "stiffener"]
        self.assertEqual(len(st), 8)                               # 2 per bay x 4 bays
        self.assertAlmostEqual(st[0]["p1"][0], 0.0)                # gap from the first cross beam at -0.1
        self.assertAlmostEqual(st[0]["p1"][2], -0.15)
        d0, d1, t_r, t_l = BR.deck_extent(d, FRAME)
        self.assertAlmostEqual(t_l, 1.39)                          # inner face of the post less clearance
        self.assertAlmostEqual(t_r, -1.39)
        self.assertAlmostEqual(st[0]["p1"][1], t_l - (t_l - t_r) / 3.0)   # count mode: spread across the deck
        self.assertEqual(st[0]["stype"], "stiffener")
        rails = [m for m in mems if m["role"] == "railing"]
        self.assertEqual(len(rails), 2)
        self.assertAlmostEqual(rails[0]["p1"][1], 1.29)
        self.assertAlmostEqual(rails[0]["p1"][2], 0.0)
        self.assertEqual(count(BR.members(design(stiffener_mode="count", stiffeners_across=0), FRAME), "stiffener"), 0)
        self.assertEqual(count(BR.members(design(), FRAME), "railing"), 0)      # no railing type

    def test_stiffener_spacing(self):
        # 3.3 m between post centres: a 3.08 m deck inside them -> at 500 six
        # lines exactly 0.5 m apart, centred on the floor, so 0.5 m in from the
        # side beam centres when those are on the outside faces (the drawing)
        s, e = posts(width=3.3)
        fr = BR.frame_from_posts(s, e)
        lines = BR.stiffener_positions(design(stiffener_spacing_m=0.5), fr)
        self.assertEqual(len(lines), 6)
        self.assertAlmostEqual(lines[0], 1.25)
        self.assertAlmostEqual(lines[-1], -1.25)
        for a, b in zip(lines, lines[1:]):
            self.assertAlmostEqual(a - b, 0.5)
        self.assertAlmostEqual(sum(lines), 0.0)                                   # centred
        # the 2.78 m deck of FRAME: five lines fit at 500 with at least half a
        # spacing to each edge, centred
        lines = BR.stiffener_positions(design(stiffener_spacing_m=0.5), FRAME)
        self.assertEqual(len(lines), 5)
        self.assertEqual([round(t, 6) for t in lines], [1.0, 0.5, 0.0, -0.5, -1.0])
        six = BR.stiffener_positions(design(stiffener_spacing_m=0.45), FRAME)    # 6.17 -> six
        self.assertEqual(len(six), 6)
        self.assertAlmostEqual(six[0], 2.5 * 0.45)
        self.assertAlmostEqual(six[0] - six[1], 0.45)
        one = BR.stiffener_positions(design(stiffener_spacing_m=2.0), FRAME)     # one line, on the centreline
        self.assertEqual([round(t, 6) for t in one], [0.0])
        self.assertEqual(BR.stiffener_positions(design(stiffener_spacing_m=5.0), FRAME), [])
        self.assertEqual(BR.stiffener_positions(design(stiffener_spacing_m=0.0), FRAME), [])
        mems = BR.members(design(stiffener_spacing_m=0.5), fr)
        self.assertEqual(count(mems, "stiffener"), 6 * 4)
        self.assertEqual(BR.normalize_design({})["stiffener_mode"], "spacing")
        with self.assertRaises(ValueError):
            BR.normalize_design({"stiffener_mode": "random"})

    def test_deck_to_the_post_faces(self):
        # along the span the deck reaches the outer faces of the posts;
        # across it sits inside them less the clearance
        corners = BR.deck_profile(design(), FRAME)
        self.assertAlmostEqual(corners[0][0], -0.1)
        self.assertAlmostEqual(corners[0][1], 1.39)
        self.assertAlmostEqual(corners[2][0], 20.1)
        self.assertAlmostEqual(corners[2][1], -1.39)
        inside = BR.deck_profile(design(truss_plane="inside"), FRAME)
        self.assertAlmostEqual(inside[0][1], 1.4 - 0.01)          # inside the inner truss plane too
        typed = BR.deck_profile(design(deck_end_m=-0.11), FRAME)  # typed: inside the posts
        self.assertAlmostEqual(typed[0][0], 0.11)
        self.assertAlmostEqual(typed[2][0], 19.89)
        self.assertAlmostEqual(BR.deck_profile(design(deck_end_m=0.0), FRAME)[0][0], 0.0)
        self.assertAlmostEqual(BR.normalize_design({})["floor_thickness_m"], 0.006)   # a plate deck by default
        deck = BR.world_deck(design(deck_top_offset_m=-0.05, floor_thickness_m=0.15), FRAME)
        self.assertAlmostEqual(deck["tail_top_z"], 10.0 - 0.1 * 0.1 - 0.05)
        self.assertAlmostEqual(deck["slope"], 0.1)
        self.assertAlmostEqual(deck["thickness_m"], 0.15)
        self.assertAlmostEqual(deck["tail"][0], -0.1)
        self.assertAlmostEqual(deck["head"][0], 20.1)
        # the end cross beams sit on the side frames' end nodes
        mems = BR.members(design(), FRAME)
        self.assertAlmostEqual(by_key(mems, u"crossbeam:C:0:0")["p1"][0], -0.1)
        self.assertAlmostEqual(by_key(mems, u"crossbeam:C:4:0")["p1"][0], 20.1)

    def test_side_frames_on_the_post_centres(self):
        # the built-in default: the trusses on the posts' centre points, the
        # chords butting against the posts' inner faces, the end cross beams
        # between the posts' inner faces, stiffeners set out from the centres
        d = BR.normalize_design({"panel_mode": "count", "panels": 4})
        self.assertEqual(d["truss_plane"], "centre")
        self.assertEqual(BR.planes(d, FRAME), (1.5, -1.5))
        lay = BR.layout(d, FRAME)
        self.assertAlmostEqual(lay["s0"], 0.1)                   # inner face of the start posts
        self.assertAlmostEqual(lay["s1"], 19.9)
        mems = BR.members(d, FRAME)
        self.assertAlmostEqual(by_key(mems, u"top_chord:L:0:0")["p1"][1], 1.5)
        self.assertAlmostEqual(by_key(mems, u"top_chord:L:0:0")["p1"][0], 0.1 + 0.01)   # the inner face less the 10 mm cutback
        cb0 = by_key(mems, u"crossbeam:C:0:0")
        self.assertAlmostEqual(cb0["p1"][0], 0.1 + 0.15)         # the first node: inner face plus clearance
        self.assertAlmostEqual(cb0["p1"][1], 1.5)                # clear of the posts, so the full width
        self.assertAlmostEqual(by_key(mems, u"crossbeam:C:1:0")["p1"][1], 1.5)
        lines = BR.stiffener_positions(d, FRAME)                 # the 2.78 m deck at 0.5: five lines, centred
        self.assertEqual(len(lines), 5)
        self.assertAlmostEqual(lines[0], 1.0)
        # typed: alongside the posts the chord runs to the outer face
        out = BR.layout(BR.normalize_design({"truss_plane": "outside"}), FRAME)
        self.assertAlmostEqual(out["s0"], 0.1)                    # an old "outside" design reads as the centres
        # the plane does not carry over from the last bridge; the default rules
        reg = {"bridges": {"B": {"design": {"truss_plane": "outside", "truss": "pratt"}, "updated": "x"}}}
        self.assertNotIn("truss_plane", BR.latest_design(reg))

    def test_braces_cut_at_every_cross_beam(self):
        # whatever split_x says, a diagonal over several bays is one piece per
        # bay, the pieces meeting on the cross beams it crosses
        for flag in (True, False):
            mems = BR.members(design(split_x=flag, brace_bays=3, plan_bays=6), FRAME)
            self.assertEqual(count(mems, "bracing"), 6)          # two diagonals x three bays
            a, b, c = (by_key(mems, u"bracing:C:0:{0}".format(i)) for i in range(3))
            self.assertAlmostEqual(a["p2"][0], b["p1"][0])
            self.assertAlmostEqual(b["p2"][0], c["p1"][0])
            self.assertAlmostEqual(a["p2"][0], -0.1 + 20.2 / 6.0) # on the first cross beam
            self.assertAlmostEqual(a["p2"][1], 1.5 - 3.0 / 3.0)   # a third of the way across
            self.assertAlmostEqual(c["p2"][1], -1.5)
        x = BR.members(design(bracing="x", split_x=False), FRAME)
        self.assertEqual(count(x, "bracing"), 8)                  # the X keeps its flag

    def test_diagonal_angle(self):
        # angle mode: the panel length follows the typed angle and the height,
        # rounded to whole panels; the laid-out angle is reported
        d = design(truss="warren", panel_mode="angle", brace_angle_deg=55.0)
        lay = BR.layout(d, FRAME)                                 # 20.2 m chord, 2.3 m high
        self.assertEqual(lay["n"], 6)                             # 2 x 2.3 / tan 55 = 3.22 -> 6.27 panels
        self.assertAlmostEqual(BR.brace_angle(d, FRAME), math.degrees(math.atan2(2.3, 20.2 / 6.0 / 2.0)))
        self.assertAlmostEqual(BR.summary(d, FRAME)["brace_angle_deg"], BR.brace_angle(d, FRAME))
        steep = design(truss="warren", panel_mode="angle", brace_angle_deg=70.0)
        self.assertEqual(BR.layout(steep, FRAME)["n"], 12)        # 2 x 2.3 / tan 70 = 1.67 -> 12.06
        pratt = design(truss="pratt", panel_mode="angle", brace_angle_deg=55.0)
        self.assertEqual(BR.layout(pratt, FRAME)["n"], 13)        # 2.3 / tan 55 = 1.61 -> 12.5 -> 13
        self.assertAlmostEqual(BR.brace_angle(design(truss="warren"), FRAME),
                               math.degrees(math.atan2(2.3, 20.2 / 4.0 / 2.0)))   # as drawn: from the panels
        self.assertEqual(BR.brace_angle(design(truss="vierendeel"), FRAME), 0.0)
        for bad in (4.0, 86.0):
            with self.assertRaises(ValueError):
                BR.normalize_design({"brace_angle_deg": bad})

    def test_railing_width(self):
        d = design(types={"railing": "77"}, railing_inset_m=0.1)
        self.assertEqual(BR.railing_positions(d, FRAME), (1.39 - 0.1, -1.39 + 0.1))   # deck edges less the inset
        w = design(types={"railing": "77"}, railing_width_m=2.0)
        self.assertEqual(BR.railing_positions(w, FRAME), (1.0, -1.0))                 # centred on the centreline
        rails = [m for m in BR.members(w, FRAME) if m["role"] == "railing"]
        self.assertEqual(sorted(r["p1"][1] for r in rails), [-1.0, 1.0])
        self.assertAlmostEqual(rails[0]["p1"][0], -0.1)                                # the deck ends
        self.assertAlmostEqual(rails[0]["p2"][0], 20.1)
        self.assertIsNone(BR.normalize_design({})["railing_width_m"])
        with self.assertRaises(ValueError):
            BR.normalize_design({"railing_width_m": 0})

    def test_end_diagonal_clearance(self):
        # the run of diagonals starts the clearance in from each chord end, so
        # the whole run shortens evenly and every diagonal keeps the same angle;
        # the chords still run the full length
        d = design(truss="warren", brace_end_offset_m=0.15)
        lay = BR.layout(d, FRAME)
        self.assertAlmostEqual(lay["r0"], -0.1 + 0.15)
        self.assertAlmostEqual(lay["r1"], 20.1 - 0.15)
        self.assertAlmostEqual(lay["L"], (20.2 - 0.3) / 4.0)
        mems = BR.members(d, FRAME)
        first = by_key(mems, u"diagonal:L:0:0")
        self.assertAlmostEqual(first["p1"][0], 0.05)
        self.assertAlmostEqual(first["p1"][2], 0.0)
        self.assertAlmostEqual(first["p2"][0], 0.05 + lay["L"] / 2.0)
        last = by_key(mems, u"diagonal:L:3:1")
        self.assertAlmostEqual(last["p2"][0], 19.95)
        inner = by_key(mems, u"diagonal:L:1:0")
        self.assertAlmostEqual(inner["p1"][0], 0.05 + lay["L"])
        runs = [abs(m["p2"][0] - m["p1"][0]) for m in mems if m["role"] == "diagonal"]
        self.assertTrue(all(abs(r - runs[0]) < 1e-9 for r in runs))            # all alike
        self.assertAlmostEqual(by_key(mems, u"top_chord:L:0:0")["p1"][0], -0.1)   # chords unchanged
        self.assertAlmostEqual(by_key(mems, u"top_chord:L:0:0")["p2"][0], 20.1)
        pratt = BR.members(design(truss="pratt", brace_end_offset_m=0.2), FRAME)
        feet = [m["p1"][0] for m in pratt if m["role"] == "diagonal" and m["side"] == "L" and abs(m["p1"][2]) < 1e-9]
        feet += [m["p2"][0] for m in pratt if m["role"] == "diagonal" and m["side"] == "L" and abs(m["p2"][2]) < 1e-9]
        self.assertTrue(all(f > -0.1 + 0.2 - 1e-9 and f < 20.1 - 0.2 + 1e-9 for f in feet))
        self.assertAlmostEqual(BR.normalize_design({})["brace_end_offset_m"], 0.15)
        with self.assertRaises(ValueError):
            BR.normalize_design({"brace_end_offset_m": -0.1})

    def test_side_beams_follow_the_cross_beams(self):
        # the default: the bottom chords sit so their centre is at the cross
        # beams' centre (underside - cross beam depth / 2 + chord depth / 2); the
        # floor and the cross beams stay where they are
        d = design(bottom_chord_offset_m=None, floor_thickness_m=0.006, crossbeam_depth_m=0.15,
                   bottom_chord_depth_m=0.1)
        self.assertAlmostEqual(BR.bottom_chord_level(d), -0.006 - 0.075 + 0.05)
        mems = BR.members(d, FRAME)
        bc = by_key(mems, u"bottom_chord:L:0:0")
        self.assertAlmostEqual(bc["p1"][2], -0.031)
        cb = by_key(mems, u"crossbeam:C:1:0")
        self.assertAlmostEqual(cb["p1"][2] - 0.075, bc["p1"][2] - 0.05)          # the two centres coincide
        br = [m for m in mems if m["role"] == "bracing"][0]
        self.assertAlmostEqual(br["p1"][2], -0.081)                                # the braces too
        self.assertAlmostEqual(BR.bottom_chord_level(design(bottom_chord_offset_m=0.2)), 0.2)   # typed wins
        self.assertIsNone(BR.normalize_design({})["bottom_chord_offset_m"])         # the default: centred on the cross beams
        # the whole frame builds up from the chords: a typed truss height keeps
        # its value and the top chord moves with the bottom chord
        full = design(bottom_chord_offset_m=None, floor_thickness_m=0.006, crossbeam_depth_m=0.15,
                      bottom_chord_depth_m=0.1, height_m=1.8)
        fm = BR.members(full, FRAME)
        self.assertAlmostEqual(by_key(fm, u"top_chord:L:0:0")["p1"][2], -0.031 + 1.8)
        self.assertAlmostEqual(by_key(fm, u"crossbeam:C:1:0")["p1"][2], -0.006)       # the cross beams do not move
        self.assertAlmostEqual(BR.summary(full, FRAME)["truss_height_m"], 1.8)

    def test_cross_beams_on_the_frame_nodes(self):
        # the cross beams sit on the side frames' bottom nodes, so the end
        # clearance moves them too; one within a post's footprint still runs
        # between the posts' inner faces
        d = design(truss="warren", brace_end_offset_m=0.25)
        lay = BR.layout(d, FRAME)
        self.assertAlmostEqual(lay["stations"][0], -0.1 + 0.25)
        self.assertAlmostEqual(lay["stations"][-1], 20.1 - 0.25)
        self.assertEqual([round(s, 6) for s in lay["stations"]], [round(p, 6) for p in lay["points"]])
        mems = BR.members(d, FRAME)
        cb0 = by_key(mems, u"crossbeam:C:0:0")
        self.assertAlmostEqual(cb0["p1"][0], 0.15)
        self.assertAlmostEqual(cb0["p1"][1], 1.5)                 # clear of the posts: full width between the planes
        feet = sorted(set(round(m["p1"][0], 6) for m in mems if m["role"] == "diagonal" and abs(m["p1"][2]) < 1e-9))
        self.assertTrue(all(any(abs(f - s) < 1e-6 for s in lay["stations"]) for f in feet))   # every brace foot on a cross beam
        counted = BR.layout(design(brace_end_offset_m=0.25, plan_bays=6), FRAME)
        self.assertAlmostEqual(counted["stations"][0], 0.15)
        self.assertAlmostEqual(counted["B"], (20.2 - 0.5) / 6.0)
        flush = BR.members(design(brace_end_offset_m=0.0), FRAME)   # a node at the post outer face
        self.assertAlmostEqual(by_key(flush, u"crossbeam:C:0:0")["p1"][1], 1.4)   # shortened to the inner faces

    def test_plan_braces_centre_on_the_bottom_chords(self):
        # the plan braces' centreline sits at the bottom chords' centre: the
        # chord line (its top of steel) less half the chord depth; the cross
        # beams stay top justified under the floor
        d = design(bottom_chord_depth_m=0.2, floor_thickness_m=0.15)
        mems = BR.members(d, FRAME)
        br = [m for m in mems if m["role"] == "bracing"]
        self.assertTrue(br)
        for m in br:
            self.assertAlmostEqual(m["p1"][2], -0.1)
            self.assertAlmostEqual(m["p2"][2], -0.1)
            self.assertEqual(m["zjust"], "centre")
        cb = by_key(mems, u"crossbeam:C:1:0")
        self.assertAlmostEqual(cb["p1"][2], -0.15)
        self.assertEqual(cb["zjust"], "top")
        self.assertEqual(by_key(mems, u"bottom_chord:L:0:0")["zjust"], "top")
        unknown = BR.members(design(bottom_chord_depth_m=0.0), FRAME)
        self.assertAlmostEqual([m for m in unknown if m["role"] == "bracing"][0]["p1"][2], 0.0)
        x = [m for m in BR.members(design(bracing="x", bottom_chord_depth_m=0.15), FRAME) if m["role"] == "bracing"]
        self.assertTrue(all(abs(m["p1"][2] + 0.075) < 1e-9 for m in x))

    def test_beams_between_cross_beams(self):
        self.assertEqual(count(BR.members(design(), FRAME), "stringer"), 0)
        mems = BR.members(design(stringers_across=2, floor_thickness_m=0.15), FRAME)
        self.assertEqual(count(mems, "stringer"), 8)              # 2 in each of 4 bays
        a = by_key(mems, u"stringer:C:0:0")
        b = by_key(mems, u"stringer:C:0:1")
        cb0 = by_key(mems, u"crossbeam:C:0:0")
        cb1 = by_key(mems, u"crossbeam:C:1:0")
        self.assertAlmostEqual(a["p1"][0], cb0["p1"][0])         # from one cross beam
        self.assertAlmostEqual(a["p2"][0], cb1["p1"][0])         # to the next
        tl, tr = cb1["p1"][1], cb1["p2"][1]                       # spread between the truss planes
        self.assertAlmostEqual(a["p1"][1], tr + (tl - tr) / 3.0)  # spread between the truss planes
        self.assertAlmostEqual(b["p1"][1], tr + (tl - tr) * 2 / 3.0)
        self.assertAlmostEqual(a["p1"][2], -0.15)                 # top on the floor underside
        self.assertEqual((a["zjust"], a["stype"]), ("top", "beam"))
        self.assertIn("stringer", BR.FRAMING_ROLES)
        d = BR.normalize_design({"stringers_across": -2})
        self.assertEqual(d["stringers_across"], 0)

    def test_typed_end_levels(self):
        """A typed level is the floor top at that end of the deck - its end
        edge at the post faces, where a level is taken - as a survey level;
        the shared elevation offset is taken off."""
        d = design(level_start_m=14.0, level_end_m=16.5)          # the deck runs -0.1 .. 20.1
        ef = BR.effective_frame(d, FRAME, shared_elev_m=4.0)
        g = 2.5 / 20.2
        self.assertAlmostEqual(ef["gradient"], g)
        self.assertAlmostEqual(ef["z0"] + g * -0.1, 10.0)        # floor top at the deck start = typed
        self.assertAlmostEqual(ef["z1"] + g * 0.1, 12.5)          # and at the deck end
        deck = BR.world_deck(d, FRAME, shared_elev_m=4.0)
        self.assertAlmostEqual(deck["slope"], g)
        self.assertAlmostEqual(deck["tail_top_z"], 10.0)         # exactly the typed figures at the floor ends
        self.assertAlmostEqual(deck["tail_top_z"] + deck["slope"] * 20.2, 12.5)
        only_end = BR.effective_frame(design(level_end_m=16.5), FRAME, shared_elev_m=4.0)
        self.assertEqual(only_end["z0"], 10.0)                    # the start keeps the post base
        self.assertAlmostEqual(only_end["z1"] + only_end["gradient"] * 0.1, 12.5)
        only_start = BR.effective_frame(design(level_start_m=14.0), FRAME, shared_elev_m=4.0)
        self.assertEqual(only_start["z1"], 12.0)
        self.assertAlmostEqual(only_start["z0"] - only_start["gradient"] * 0.1, 10.0)
        off = BR.effective_frame(design(level_start_m=14.0, level_end_m=16.5, deck_top_offset_m=0.05), FRAME, shared_elev_m=4.0)
        self.assertAlmostEqual(off["z0"] + off["gradient"] * -0.1 + 0.05, 10.0)   # a deck top offset: still the floor top
        # the model case: 5.105 and 4.920 typed at the floor ends come out as typed
        fr = BR.frame_from_posts(*posts(span=27.3, z0=5.1, z1=4.9))
        dk = BR.world_deck(design(level_start_m=5.105, level_end_m=4.920), fr)
        self.assertAlmostEqual(dk["tail_top_z"], 5.105)
        self.assertAlmostEqual(dk["tail_top_z"] + dk["slope"] * 27.5, 4.920)

    def test_cross_beams_roll_with_the_fall(self):
        # 2 m rise over 20 m: the cross beams' sections roll by the slope so
        # their tops lie on the floor underside; nothing else rolls
        w = BR.world_members(design(), FRAME)
        cbs = [m for m in w if m["role"] == "crossbeam"]
        self.assertTrue(cbs)
        for cb in cbs:
            self.assertAlmostEqual(cb["roll"], math.atan(0.1))
            ux, uy, uz = cb["up"]
            self.assertAlmostEqual(ux * ux + uy * uy + uz * uz, 1.0)
            self.assertAlmostEqual(ux, -0.1 / math.sqrt(1.01))     # leans back against the rise (+x)
            self.assertAlmostEqual(uz, 1.0 / math.sqrt(1.01))
        self.assertTrue(all("roll" not in m for m in w if m["role"] != "crossbeam"))
        s, e = posts(z0=10.0, z1=10.0)
        flat = BR.world_members(design(), BR.frame_from_posts(s, e))
        self.assertAlmostEqual([m for m in flat if m["role"] == "crossbeam"][0]["roll"], 0.0)

    def test_world_follows_the_fall(self):
        wm = BR.world_members(design(floor_thickness_m=0.15), FRAME)
        top = by_key(wm, u"top_chord:L:0:0")
        self.assertAlmostEqual(top["p1"][2], 10.0 - 0.01 + 2.3)
        self.assertAlmostEqual(top["p2"][2], 12.0 + 0.01 + 2.3)
        cb = by_key(wm, u"crossbeam:C:2:0")
        self.assertAlmostEqual(cb["p1"][0], 10.0)
        self.assertAlmostEqual(cb["p1"][2], 11.0 - 0.15)

    def test_summary(self):
        s = BR.summary(design(truss="pratt"), FRAME)
        self.assertEqual(s["panels"], 4)
        self.assertEqual(s["bays"], 4)
        self.assertAlmostEqual(s["height_m"], 2.3)
        self.assertAlmostEqual(s["deck_width_m"], 2.78)
        self.assertAlmostEqual(s["by_role"]["top_chord"]["length_m"], 40.4)
        self.assertEqual(s["members"], 2 + 2 + 6 + 8 + 5 + 4 + 5 * 4)   # two long diagonals in two pieces each; five stiffener lines at 500 per bay
        w = BR.summary(design(truss="warren"), FRAME)
        self.assertEqual(w["by_role"]["diagonal"]["count"], 16)
        self.assertNotIn("vertical", w["by_role"])


class Updates(unittest.TestCase):

    def test_diff(self):
        old = [m["key"] for m in BR.members(design(truss="pratt"), FRAME)]
        new = BR.members(design(truss="pratt", panels=5), FRAME)
        diff = BR.diff_members(old, new)
        self.assertTrue(any(m["key"] == u"vertical:L:4:0" for m in diff["create"]))
        self.assertIn(u"crossbeam:C:4:0", [k for k, _m in diff["update"]])
        same = BR.diff_members(old, BR.members(design(truss="pratt"), BR.frame_from_posts(*posts(span=25.0))))
        self.assertEqual((len(same["create"]), len(same["delete"])), (0, 0))

    def test_rigid_motion(self):
        a = FRAME
        b = BR.frame_from_posts(*posts(x0=5.0, y0=5.0, z0=11.0, z1=13.0))
        dx, dy, dz, da = BR.rigid_motion(a, b)
        self.assertEqual((dx, dy, dz, da), (5.0, 5.0, 1.0, 0.0))
        self.assertIsNone(BR.rigid_motion(a, BR.frame_from_posts(*posts(span=21.0))))
        self.assertIsNone(BR.rigid_motion(a, BR.frame_from_posts(*posts(width=3.5))))
        self.assertIsNone(BR.rigid_motion(a, BR.frame_from_posts(*posts(z1=12.5))))
        # a post height change leaves the deck alone (the posts are set to the
        # truss top on every build), so it is a rigid motion of nothing
        self.assertEqual(BR.rigid_motion(a, BR.frame_from_posts(*posts(h=2.0))), (0.0, 0.0, 0.0, 0.0))
        self.assertTrue(BR.frame_changed(a, BR.frame_from_posts(*posts(h=2.0))))
        self.assertFalse(BR.frame_changed(a, BR.frame_from_posts(*posts())))
        self.assertTrue(BR.frame_changed(a, b))
        self.assertTrue(BR.frame_changed(a, BR.frame_from_posts(*posts(h=2.0))))


class Files(unittest.TestCase):

    def test_design_file_round_trip(self):
        d = design(truss="pratt")
        src = {"start_ids": [11, 12], "end_ids": [13, 14]}
        back = BR.parse_design_file(BR.design_file_text(u"Bridge 1", d, src))
        self.assertEqual(back["name"], u"Bridge 1")
        self.assertEqual(back["design"]["truss"], "pratt")
        self.assertEqual(back["source"]["end_ids"], [13, 14])
        with self.assertRaises(ValueError):
            BR.parse_design_file(u"{not json")
        with self.assertRaises(ValueError):
            BR.parse_design_file(json.dumps({"kind": "pymep-bridge", "name": "x", "design": {},
                                             "source": {"start_ids": [1]}}))

    def test_bridges_on_posts(self):
        reg = {"bridges": {
            "Bridge 1": {"source": {"start_ids": [1, 2], "end_ids": [3, 4]}},
            "Bridge 2": {"source": {"start_ids": [11, 12], "end_ids": [13, 14]}}}}
        self.assertEqual(BR.bridge_on_posts(reg, [20, 13, 21, 22]), ("Bridge 2", 13))   # one shared post is enough
        self.assertEqual(BR.bridge_on_posts(reg, ["2", 30]), ("Bridge 1", 2))
        self.assertIsNone(BR.bridge_on_posts(reg, [20, 21, 22, 23]))
        self.assertIsNone(BR.bridge_on_posts({"bridges": {}}, [1]))
        self.assertEqual(BR.bridge_with_source(reg, {"start_ids": [4, 3], "end_ids": [2, 1]}), "Bridge 1")   # either way round
        self.assertEqual(BR.bridge_with_source(reg, {"start_ids": [2, 1], "end_ids": [3, 4]}), "Bridge 1")
        self.assertIsNone(BR.bridge_with_source(reg, {"start_ids": [1, 2], "end_ids": [3, 5]}))

    def test_design_differs(self):
        a = {"truss": "warren", "types": {"top_chord": "1"}, "floor_thickness_m": 0.15}
        self.assertFalse(BR.design_differs(a, dict(a, floor_thickness_m=0.006)))   # read from the type: ignored
        self.assertFalse(BR.design_differs(a, dict(a, bottom_chord_depth_m=0.2)))
        self.assertTrue(BR.design_differs(a, dict(a, truss="pratt")))
        self.assertTrue(BR.design_differs(a, dict(a, types={"top_chord": "2"})))
        self.assertFalse(BR.design_differs(a, BR.normalize_design(a)))           # defaults filled in: the same

    def test_pick_design_file(self):
        src = {"start_ids": [1, 2], "end_ids": [3, 4]}
        cands = [("a.json", 100.0, {"source": {"start_ids": [2, 1], "end_ids": [4, 3]}}),
                 ("b.json", 200.0, {"source": {"start_ids": [3, 4], "end_ids": [1, 2]}}),   # the pairs the other way
                 ("c.json", 300.0, {"source": {"start_ids": [7, 8], "end_ids": [3, 4]}})]   # another bridge
        self.assertEqual(BR.pick_design_file(cands, src)[0], "b.json")             # the newest for these posts
        self.assertEqual(BR.pick_design_file(cands, src, after_mtime=150.0)[0], "b.json")
        self.assertIsNone(BR.pick_design_file(cands, src, after_mtime=200.0))       # nothing saved since

    def test_deck_motion(self):
        d = design()
        old = BR.world_deck(d, FRAME)
        self.assertEqual(BR.deck_motion(old, BR.world_deck(d, FRAME)), "same")
        s, e = posts(x0=5.0, y0=2.0, z0=10.5, z1=12.5)                             # slid and risen, same fall
        mv = BR.deck_motion(old, BR.world_deck(d, BR.frame_from_posts(s, e)))
        self.assertAlmostEqual(mv[0], 5.0)
        self.assertAlmostEqual(mv[1], 2.0)
        self.assertAlmostEqual(mv[2], 0.5)
        self.assertAlmostEqual(mv[3], 0.0)
        s, e = posts(z1=12.5)                                                       # the fall changed
        self.assertIsNone(BR.deck_motion(old, BR.world_deck(d, BR.frame_from_posts(s, e))))
        self.assertIsNone(BR.deck_motion(old, BR.world_deck(design(deck_end_m=0.0), FRAME)))   # the outline changed
        self.assertEqual(BR.deck_motion(old, BR.world_deck(design(floor_thickness_m=0.3), FRAME)), "same")   # thickness: the type's job
        rec = BR.record("B", d, {}, FRAME, {}, 9, "now", extra={"design_file": "x.json", "design_mtime": 5.0})
        self.assertEqual((rec["design_file"], rec["design_mtime"]), ("x.json", 5.0))

    def test_latest_design(self):
        self.assertIsNone(BR.latest_design({"bridges": {}}))
        reg = {"bridges": {
            "Bridge 1": {"design": {"truss": "pratt", "level_end_m": 5.0, "types": {"floor": "1"}}, "stamp": "2026-01-01T10:00:00"},
            "Bridge 2": {"design": {"truss": "howe", "level_start_m": 4.0, "types": {"floor": "2"}}, "stamp": "2026-02-01T10:00:00"}}}
        d = BR.latest_design(reg)
        self.assertEqual(d["truss"], "howe")                     # the newest
        self.assertEqual(d["types"]["floor"], "2")
        self.assertNotIn("level_start_m", d)                      # survey levels stay with their bridge

    def test_registry(self):
        folder = tempfile.mkdtemp()
        try:
            path = os.path.join(folder, "sub", "bridges.json")
            reg = BR.load_registry(path)
            self.assertEqual(BR.next_name(reg), u"Bridge 1")
            rec = BR.record(u"Bridge 1", design(), {"start_ids": [1, 2], "end_ids": [3, 4]},
                            FRAME, {u"top_chord:L:0:0": 501}, 600, u"2026-11-09 10:00")
            reg["bridges"][u"Bridge 1"] = rec
            BR.save_registry(path, reg)
            back = BR.load_registry(path)
            f2 = BR.frame_from_record(back["bridges"][u"Bridge 1"])
            for k in BR.FRAME_KEYS:
                self.assertAlmostEqual(f2[k], FRAME[k])
            self.assertFalse(BR.frame_changed(FRAME, f2))
            self.assertEqual(BR.next_name(back), u"Bridge 2")
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_fill_page(self):
        out = BR.fill_page(u"<script>var D = /*__BRIDGE_DATA__*/null;</script>", {"a": u"</script>"})
        self.assertIn(u"<\\/script>", out)
        with self.assertRaises(ValueError):
            BR.fill_page(u"<html></html>", {})


if __name__ == "__main__":
    unittest.main()
