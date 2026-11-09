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
    d = {"panel_mode": "count", "panels": 4, "truss": "warren_verticals"}
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
        self.assertEqual(d["truss_plane"], "outside")
        self.assertEqual(d["bracing"], "zigzag")
        self.assertFalse(d["end_posts"])
        self.assertEqual(d["stiffeners_across"], 1)
        d2 = BR.normalize_design({"height_m": "2.5", "plan_bays": "6", "split_x": 1,
                                  "top_chord_split_m": "6"})
        self.assertEqual((d2["height_m"], d2["plan_bays"], d2["split_x"], d2["top_chord_split_m"]),
                         (2.5, 6, True, 6.0))

    def test_refusals(self):
        for bad in ({"truss": "bowstring"}, {"bracing": "k"}, {"truss_plane": "above"},
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
        self.assertEqual(BR.planes(d, FRAME), (1.6, -1.6))
        self.assertEqual(BR.planes(design(truss_plane="inside"), FRAME), (1.4, -1.4))
        self.assertEqual(BR.planes(design(truss_plane="centre"), FRAME), (1.5, -1.5))
        self.assertAlmostEqual(BR.height(d, FRAME), 2.3)
        self.assertAlmostEqual(BR.height(design(height_m=1.8), FRAME), 1.8)

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
        self.assertAlmostEqual(lay["d0"], 0.0)               # bays from post centreline to post centreline
        self.assertAlmostEqual(lay["d1"], 20.0)
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
        self.assertAlmostEqual(top["p1"][1], 1.6)
        self.assertAlmostEqual(top["p1"][2], 2.3)                # post height
        self.assertAlmostEqual(top["p1"][0], -0.1)
        bot = by_key(mems, u"bottom_chord:R:0:0")
        self.assertAlmostEqual(bot["p1"][1], -1.6)
        self.assertAlmostEqual(bot["p1"][2], 0.0)
        self.assertEqual(top["zjust"], "centre")
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
        self.assertAlmostEqual(cb["p1"][0], 0.0)                 # on the post centreline
        self.assertAlmostEqual(cb["p1"][1], 1.6)
        self.assertAlmostEqual(cb["p2"][1], -1.6)
        self.assertAlmostEqual(cb["p1"][2], -0.15)
        self.assertEqual(cb["zjust"], "top")
        self.assertEqual(cb["stype"], "beam")
        self.assertEqual(count(mems, "crossbeam"), 5)
        # the default: one diagonal at a time over two bays, side beam to side
        # beam, alternating - the drawing
        whole = BR.members(design(split_x=False, floor_thickness_m=0.15), FRAME)
        self.assertEqual(count(whole, "bracing"), 2)
        br = by_key(whole, u"bracing:C:0:0")
        self.assertAlmostEqual(br["p1"][0], 0.0)
        self.assertAlmostEqual(br["p1"][1], 1.6)
        self.assertAlmostEqual(br["p2"][0], 10.0)                 # two bays on
        self.assertAlmostEqual(br["p2"][1], -1.6)
        br2 = by_key(whole, u"bracing:C:1:0")
        self.assertAlmostEqual(br2["p1"][1], -1.6)                # alternates
        self.assertAlmostEqual(br2["p2"][1], 1.6)
        self.assertAlmostEqual(br["p1"][2], -0.15)
        self.assertEqual((br["zjust"], br["stype"]), ("top", "brace"))
        # split where it crosses the middle cross beam: a piece per bay, meeting mid-width
        sz = mems                                                 # the default splits at the cross beams
        self.assertEqual(count(sz, "bracing"), 4)
        meet = by_key(sz, u"bracing:C:0:0")["p2"]
        self.assertAlmostEqual(meet[0], 5.0)
        self.assertAlmostEqual(meet[1], 0.0)
        self.assertTrue(BR.normalize_design({})["split_x"])        # split at the cross beams by default
        # an odd number of bays: the last diagonal takes the bay that is left
        odd = BR.members(design(plan_bays=5, split_x=False), FRAME)
        self.assertEqual(count(odd, "bracing"), 3)
        self.assertAlmostEqual(by_key(odd, u"bracing:C:2:0")["p2"][0], 20.0)
        one = BR.members(design(brace_bays=1), FRAME)
        self.assertEqual(count(one, "bracing"), 4)
        self.assertAlmostEqual(by_key(one, u"bracing:C:1:0")["p1"][1], -1.6)
        x = BR.members(design(bracing="x", split_x=False), FRAME)
        self.assertEqual(count(x, "bracing"), 8)                  # X in 4 bays
        split = BR.members(design(bracing="x", split_x=True), FRAME)
        self.assertEqual(count(split, "bracing"), 16)
        mid = by_key(split, u"bracing:C:0:0")["p2"]
        self.assertAlmostEqual(mid[0], 2.5)
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
        self.assertAlmostEqual(st[0]["p1"][0], 0.1)                # gap from the cross beam on the post centreline
        self.assertAlmostEqual(st[0]["p1"][2], -0.15)
        d0, d1, t_r, t_l = BR.deck_extent(d, FRAME)
        self.assertAlmostEqual(t_l, 1.39)                          # inner face of the post less clearance
        self.assertAlmostEqual(t_r, -1.39)
        tl, tr = BR.planes(d, FRAME)                               # set out between the side beam centres
        self.assertAlmostEqual(st[0]["p1"][1], tl - (tl - tr) / 3.0)
        self.assertEqual(st[0]["stype"], "stiffener")
        rails = [m for m in mems if m["role"] == "railing"]
        self.assertEqual(len(rails), 2)
        self.assertAlmostEqual(rails[0]["p1"][1], 1.29)
        self.assertAlmostEqual(rails[0]["p1"][2], 0.0)
        self.assertEqual(count(BR.members(design(stiffener_mode="count", stiffeners_across=0), FRAME), "stiffener"), 0)
        self.assertEqual(count(BR.members(design(), FRAME), "railing"), 0)      # no railing type

    def test_stiffener_spacing(self):
        # 3.3 m between post centres, planes on the outside faces: 3.5 m side beam
        # centre to centre -> at 500 exactly six lines 0.5 m apart, the first and
        # last 0.5 m from the side beams (the drawing)
        s, e = posts(width=3.3)
        fr = BR.frame_from_posts(s, e)
        lines = BR.stiffener_positions(design(stiffener_spacing_m=0.5), fr)
        self.assertEqual(len(lines), 6)
        self.assertAlmostEqual(lines[0], 1.75 - 0.5)
        self.assertAlmostEqual(lines[-1], -1.75 + 0.5)
        for a, b in zip(lines, lines[1:]):
            self.assertAlmostEqual(a - b, 0.5)
        # a width that is not a multiple: equal spaces no wider than the spacing
        lines = BR.stiffener_positions(design(stiffener_spacing_m=0.5), FRAME)   # 3.2 m
        self.assertEqual(len(lines), 6)                                           # 7 spaces of 0.457
        self.assertAlmostEqual(lines[0], 1.6 - 3.2 / 7.0)
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
        # the end cross beams sit on the post centrelines
        mems = BR.members(design(), FRAME)
        self.assertAlmostEqual(by_key(mems, u"crossbeam:C:0:0")["p1"][0], 0.0)
        self.assertAlmostEqual(by_key(mems, u"crossbeam:C:4:0")["p1"][0], 20.0)

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
        tl, tr = cb0["p1"][1], cb0["p2"][1]
        self.assertAlmostEqual(a["p1"][1], tr + (tl - tr) / 3.0)  # spread between the truss planes
        self.assertAlmostEqual(b["p1"][1], tr + (tl - tr) * 2 / 3.0)
        self.assertAlmostEqual(a["p1"][2], -0.15)                 # top on the floor underside
        self.assertEqual((a["zjust"], a["stype"]), ("top", "beam"))
        self.assertIn("stringer", BR.FRAMING_ROLES)
        d = BR.normalize_design({"stringers_across": -2})
        self.assertEqual(d["stringers_across"], 0)

    def test_typed_end_levels(self):
        """Clicking an end and typing a survey level moves that end's
        datum; shared elevation offset is taken off."""
        d = design(level_start_m=14.0, level_end_m=16.5)
        ef = BR.effective_frame(d, FRAME, shared_elev_m=4.0)
        self.assertEqual((ef["z0"], ef["z1"]), (10.0, 12.5))
        self.assertAlmostEqual(ef["gradient"], 0.125)
        only_end = BR.effective_frame(design(level_end_m=16.5), FRAME, shared_elev_m=4.0)
        self.assertEqual((only_end["z0"], only_end["z1"]), (10.0, 12.5))
        wm = BR.world_members(d, FRAME, shared_elev_m=4.0)
        bot = by_key(wm, u"bottom_chord:L:0:0")
        self.assertAlmostEqual(bot["p1"][2], 10.0 - 0.0125)
        self.assertAlmostEqual(bot["p2"][2], 12.5 + 0.0125)
        deck = BR.world_deck(d, FRAME, shared_elev_m=4.0)
        self.assertAlmostEqual(deck["slope"], 0.125)
        self.assertAlmostEqual(deck["tail_top_z"], 10.0 - 0.125 * 0.1)   # deck tail at the post face

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
        self.assertEqual(s["members"], 2 + 2 + 6 + 8 + 5 + 4 + 6 * 4)   # two long diagonals in two pieces each; six stiffener lines at 500 per bay
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
        self.assertIsNone(BR.rigid_motion(a, BR.frame_from_posts(*posts(h=2.0))))
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
