# -*- coding: utf-8 -*-
"""Bridge Builder - the pure part (no Revit): the design of a steel truss
footbridge between two pairs of posts, the members it makes, the frame
that puts them in the model, what an update must change, and the
tracking registry.

A bridge runs between a START pair and an END pair of posts - vertical
structural framing (or columns) already in the model, two at each end,
one each side. The posts give everything the frame needs: the span
(between the pair midpoints), the width (between the left and the right
post), the deck level at each end (the post bases) and the default truss
height (the post length). The deck is a sloped floor fitted inside the
four posts; two trusses carry the chords, verticals and diagonals in
planes set against the posts (outside faces by default); cross beams
join the trusses under the deck with their tops on the floor underside,
plan bracing crosses every bay, stiffeners run under the deck between
the cross beams, and railings follow the deck edges.

Local coordinates: s along the span from the start pair's midpoint
(metres), t across (left positive), z relative to the post BASE line at
that s - the base line follows the two post-base levels linearly.
to_world turns (s, t, z) into model coordinates.

Everything here is metres; the Revit side converts to feet.

Runs under IronPython 2.7 and CPython 3: .format() only, LF endings.
"""

import io
import json
import math
import os

TRUSSES = (("warren", u"Warren - top nodes at mid-panel, no verticals (as the drawing)"),
           ("warren_verticals", u"Warren with verticals at every node"),
           ("pratt", u"Pratt (diagonals lean to the centre)"),
           ("howe", u"Howe (diagonals lean to the ends)"),
           ("x", u"X braced (both diagonals)"),
           ("vierendeel", u"Vierendeel (verticals only)"))
BRACINGS = (("x", u"X in every bay (as the picture)"), ("zigzag", u"zigzag (Warren)"),
            ("none", u"none"))
PLANES = (("outside", u"the outside faces of the posts"),
          ("centre", u"the post centrelines"),
          ("inside", u"the inside faces of the posts"))
FRAMING_ROLES = ("top_chord", "bottom_chord", "end_post", "vertical", "diagonal",
                 "crossbeam", "bracing")
ROLES = FRAMING_ROLES + ("stiffener", "railing")
ROLE_LABELS = {"top_chord": u"Top chord", "bottom_chord": u"Bottom chord",
               "end_post": u"End post", "vertical": u"Vertical",
               "diagonal": u"Diagonal", "crossbeam": u"Cross beam", "bracing": u"Plan bracing",
               "stiffener": u"Deck stiffener", "railing": u"Railing"}
BRACE_ROLES = ("diagonal", "bracing")       # placed as braces, the rest as beams
TOP_ALIGNED = ("crossbeam", "bracing")      # the member's top sits on the line (floor underside)

DEFAULTS = {
    "truss": "warren",
    "panel_mode": "length",          # "length": aim for panel_length_m; "count": panels
    "panel_length_m": 2.5,
    "panels": 9,
    "height_m": None,                # top chord centreline above the post base; None = post height
    "level_start_m": None,           # deck datum at the start as a SHARED elevation; None = post base
    "level_end_m": None,             # same at the end
    "truss_plane": "outside",        # where the truss planes sit against the posts
    "bottom_chord_offset_m": 0.0,    # bottom chord centreline relative to the post base
    "top_chord_split_m": 0.0,        # longest top chord piece; 0 = one piece
    "bottom_chord_split_m": 0.0,     # longest bottom chord piece; 0 = one piece
    "chord_extension_m": None,       # chords run this far past the post centrelines; None = outer face
    "end_posts": False,              # the posts are already there
    "deck_top_offset_m": 0.0,        # deck top relative to the post base
    "deck_clearance_m": 0.01,        # gap between the deck edges and the posts / truss planes
    "floor_thickness_m": 0.15,       # of the chosen floor type (the Revit side fills it in)
    "plan_bays": None,               # cross beam bays along the deck; None = one per truss panel
    "bracing": "x",
    "split_x": False,                # X bracing as four pieces meeting at the crossing
    "stiffeners_across": 1,          # stiffener lines per bay across the deck; 0 = none
    "stiffener_gap_m": 0.1,          # stiffener end gap to the cross beams
    "railing_inset_m": 0.1,          # railing path inside the deck edge; railing type "none" = none
    "types": {},                     # role -> type id (text); "floor", "stiffener", "railing" too
    "level": None,                   # level id the floor and beams reference
}
TOL = 1e-6


def normalize_design(d):
    """A full design dict from a partial one: defaults filled, numbers as
    floats, counts as ints. ValueError when a value cannot work."""
    out = {}
    d = d or {}
    for k, v in DEFAULTS.items():
        out[k] = d[k] if (k in d and d[k] is not None) else v
    if out["truss"] not in [k for k, _l in TRUSSES]:
        raise ValueError(u"unknown truss type '{0}'".format(out["truss"]))
    if out["bracing"] not in [k for k, _l in BRACINGS]:
        raise ValueError(u"unknown bracing '{0}'".format(out["bracing"]))
    if out["truss_plane"] not in [k for k, _l in PLANES]:
        raise ValueError(u"unknown truss plane '{0}'".format(out["truss_plane"]))
    for k in ("panel_length_m", "bottom_chord_offset_m", "top_chord_split_m",
              "bottom_chord_split_m", "deck_top_offset_m", "deck_clearance_m",
              "floor_thickness_m", "stiffener_gap_m", "railing_inset_m"):
        try:
            out[k] = float(out[k])
        except (TypeError, ValueError):
            raise ValueError(u"'{0}' must be a number".format(k))
    for k in ("height_m", "chord_extension_m", "level_start_m", "level_end_m"):
        if out[k] is not None:
            try:
                out[k] = float(out[k])
            except (TypeError, ValueError):
                raise ValueError(u"'{0}' must be a number or blank".format(k))
    for k in ("panels", "stiffeners_across"):
        try:
            out[k] = int(round(float(out[k])))
        except (TypeError, ValueError):
            raise ValueError(u"'{0}' must be a whole number".format(k))
    if out["plan_bays"] is not None:
        try:
            out["plan_bays"] = max(1, int(round(float(out["plan_bays"]))))
        except (TypeError, ValueError):
            raise ValueError(u"'plan_bays' must be a whole number or blank")
    if out["panel_mode"] not in ("length", "count"):
        raise ValueError(u"panel_mode must be 'length' or 'count'")
    if out["panel_length_m"] <= 0 or out["panels"] < 1:
        raise ValueError(u"panel length must be positive and panels at least 1")
    if out["height_m"] is not None and out["height_m"] <= 0:
        raise ValueError(u"the truss height must be positive")
    if out["floor_thickness_m"] < 0 or out["deck_clearance_m"] < 0:
        raise ValueError(u"floor thickness and deck clearance cannot be negative")
    if out["top_chord_split_m"] < 0 or out["bottom_chord_split_m"] < 0:
        raise ValueError(u"a chord split length cannot be negative")
    out["stiffeners_across"] = max(0, out["stiffeners_across"])
    out["end_posts"] = bool(out["end_posts"])
    out["split_x"] = bool(out["split_x"])
    out["types"] = dict(out["types"] or {})
    return out


# ---------------------------------------------------------------------------
# 1. The frame: four posts -> span, width, levels, height
# ---------------------------------------------------------------------------


def _mean(vals):
    vals = list(vals)
    return sum(vals) / float(len(vals))


def frame_from_posts(start_pair, end_pair):
    """The frame from the two pairs of posts. Each post is a dict with x,
    y (metres, at its base), z_bottom, z_top and size_m (its plan size).

    origin is the start pair's midpoint, u runs to the end pair's
    midpoint, v is across (left positive); tL / tR are the centreline
    offsets of the left and right posts (averaged over both ends),
    post_size the mean plan size, z0 / z1 the post-base levels at the
    two ends, post_height the mean post length. ValueError when a pair
    is not one post each side, or the two pairs coincide."""
    if len(start_pair) != 2 or len(end_pair) != 2:
        raise ValueError(u"each end needs exactly two posts")
    sx, sy = _mean(p["x"] for p in start_pair), _mean(p["y"] for p in start_pair)
    ex, ey = _mean(p["x"] for p in end_pair), _mean(p["y"] for p in end_pair)
    dx, dy = ex - sx, ey - sy
    span = math.hypot(dx, dy)
    if span <= TOL:
        raise ValueError(u"the start posts and the end posts are at the same place")
    ux, uy = dx / span, dy / span
    vx, vy = -uy, ux

    def t_of(p):
        return (p["x"] - sx) * vx + (p["y"] - sy) * vy

    lefts, rights = [], []
    for pair in (start_pair, end_pair):
        ts = sorted((t_of(p) for p in pair))
        if ts[1] - ts[0] <= TOL:
            raise ValueError(u"the two posts of a pair sit on the same side")
        rights.append(ts[0])
        lefts.append(ts[1])
    return {"origin": (sx, sy), "u": (ux, uy), "v": (vx, vy), "span": span,
            "angle": math.atan2(uy, ux),
            "tL": _mean(lefts), "tR": _mean(rights),
            "post_size": _mean(p.get("size_m", 0.2) for p in list(start_pair) + list(end_pair)),
            "z0": _mean(p["z_bottom"] for p in start_pair),
            "z1": _mean(p["z_bottom"] for p in end_pair),
            "post_height": _mean(p["z_top"] - p["z_bottom"] for p in list(start_pair) + list(end_pair)),
            "gradient": (_mean(p["z_bottom"] for p in end_pair)
                         - _mean(p["z_bottom"] for p in start_pair)) / span}


def frame_from_points(start, end, width=3.0, post_size=0.2, height=1.8):
    """A frame from two plain points (x, y, z) - for tests and for
    designs that have no posts: posts are imagined width apart."""
    half = width / 2.0
    mk = lambda x, y, z, t: {"x": x - t * (end[1] - start[1]) / math.hypot(end[0] - start[0], end[1] - start[1]),
                             "y": y + t * (end[0] - start[0]) / math.hypot(end[0] - start[0], end[1] - start[1]),
                             "z_bottom": z, "z_top": z + height, "size_m": post_size}
    return frame_from_posts([mk(start[0], start[1], start[2], half), mk(start[0], start[1], start[2], -half)],
                            [mk(end[0], end[1], end[2], half), mk(end[0], end[1], end[2], -half)])


def effective_frame(design, frame, shared_elev_m=0.0):
    """The frame with the design's typed end levels applied: a typed
    level is a shared / survey elevation of the deck datum (the post
    base line) at that end, so z = level - shared_elev_m; a blank one
    keeps the post base. The other keys are untouched."""
    f = dict(frame)
    if design.get("level_start_m") is not None:
        f["z0"] = float(design["level_start_m"]) - float(shared_elev_m)
    if design.get("level_end_m") is not None:
        f["z1"] = float(design["level_end_m"]) - float(shared_elev_m)
    f["gradient"] = (f["z1"] - f["z0"]) / f["span"] if f["span"] else 0.0
    return f


def planes(design, frame):
    """(t of the left truss plane, t of the right truss plane)."""
    half = frame["post_size"] / 2.0
    if design["truss_plane"] == "outside":
        return frame["tL"] + half, frame["tR"] - half
    if design["truss_plane"] == "inside":
        return frame["tL"] - half, frame["tR"] + half
    return frame["tL"], frame["tR"]


def height(design, frame):
    return design["height_m"] if design["height_m"] is not None else frame["post_height"]


def base_z(frame, s):
    """Model z of the post-base line at station s (extrapolates)."""
    return frame["z0"] + frame["gradient"] * s


def to_world(frame, s, t, z_rel=0.0):
    """(x, y, z) in the model for local (s, t, z relative to the base
    line)."""
    ox, oy = frame["origin"]
    ux, uy = frame["u"]
    vx, vy = frame["v"]
    return (ox + ux * s + vx * t, oy + uy * s + vy * t, base_z(frame, s) + z_rel)


# ---------------------------------------------------------------------------
# 2. The layout along the span
# ---------------------------------------------------------------------------


def layout(design, frame):
    """Stations along the span: the chords run from s0 to s1 (past the
    post centrelines by the chord extension, default the outer face) in
    n truss panels of length L; the deck and the plan bays run from
    d0 to d1 (inside the posts, less the clearance) in bays plan bays
    with stations at every bay line."""
    span = frame["span"]
    half = frame["post_size"] / 2.0
    ext = design["chord_extension_m"] if design["chord_extension_m"] is not None else half
    s0, s1 = -ext, span + ext
    total = s1 - s0
    if design["panel_mode"] == "count":
        n = max(1, int(design["panels"]))
    else:
        n = max(1, int(round(total / design["panel_length_m"])))
    L = total / n
    d0 = half + design["deck_clearance_m"]
    d1 = span - half - design["deck_clearance_m"]
    if d1 - d0 <= TOL:
        raise ValueError(u"the posts are too close together for a deck")
    bays = design["plan_bays"] or n
    B = (d1 - d0) / bays
    return {"s0": s0, "s1": s1, "n": n, "L": L, "total": total,
            "points": [s0 + L * i for i in range(n + 1)],
            "d0": d0, "d1": d1, "bays": bays, "B": B,
            "stations": [d0 + B * j for j in range(bays + 1)]}


def _diagonal(truss, i, n, s_a, s_b):
    """Pratt / Howe / X diagonals in panel i (shared-node layouts)."""
    if truss == "vierendeel":
        return []
    if truss == "x":
        return [(("bottom", s_a), ("top", s_b)), (("top", s_a), ("bottom", s_b))]
    left_half = (i + 0.5) < n / 2.0
    if truss == "pratt":
        return [(("top", s_a), ("bottom", s_b))] if left_half else [(("bottom", s_a), ("top", s_b))]
    if truss == "howe":
        return [(("bottom", s_a), ("top", s_b))] if left_half else [(("top", s_a), ("bottom", s_b))]
    return []


def pieces(s0, s1, max_len):
    """(s_a, s_b) of equal pieces no longer than max_len between s0 and
    s1; one piece when max_len is 0."""
    total = s1 - s0
    if max_len <= TOL or total <= max_len + TOL:
        return [(s0, s1)]
    n = int(math.ceil(total / max_len - 1e-9))
    L = total / n
    return [(s0 + L * k, s0 + L * (k + 1)) for k in range(n)]


def deck_extent(design, frame):
    """(d0, d1, t_right_edge, t_left_edge) of the deck - inside the
    posts, inside the truss planes when those are inside the posts,
    less the clearance."""
    lay = layout(design, frame)
    half = frame["post_size"] / 2.0
    tl, tr = planes(design, frame)
    left_limit = min(frame["tL"] - half, tl)
    right_limit = max(frame["tR"] + half, tr)
    c = design["deck_clearance_m"]
    return lay["d0"], lay["d1"], right_limit + c, left_limit - c


def members(design, frame):
    """Every member in local coordinates: dicts with key (unique, stable
    across rebuilds), role, side ('L', 'R' or 'C'), i, p1, p2 as
    (s, t, z) with z relative to the post-base line, stype ('beam',
    'brace', 'stiffener', 'railing') and zjust ('centre' or 'top' - the
    line is the member's top for cross beams and bracing, which sit
    under the floor)."""
    lay = layout(design, frame)
    tl, tr = planes(design, frame)
    zb = design["bottom_chord_offset_m"]
    zt = height(design, frame)
    zd = design["deck_top_offset_m"]
    zu = zd - design["floor_thickness_m"]        # floor underside
    pts = lay["points"]
    n = lay["n"]
    out = []

    def add(role, side, i, p1, p2, sub=0):
        key = u"{0}:{1}:{2}:{3}".format(role, side, i, sub)
        stype = "brace" if role in BRACE_ROLES else ("beam" if role in FRAMING_ROLES else role)
        out.append({"key": key, "role": role, "side": side, "i": i, "p1": p1, "p2": p2,
                    "stype": stype, "zjust": "top" if role in TOP_ALIGNED else "centre"})

    warren = design["truss"] in ("warren", "warren_verticals")
    for side, t in (("L", tl), ("R", tr)):
        for k, (a, b) in enumerate(pieces(lay["s0"], lay["s1"], design["top_chord_split_m"])):
            add("top_chord", side, k, (a, t, zt), (b, t, zt))
        for k, (a, b) in enumerate(pieces(lay["s0"], lay["s1"], design["bottom_chord_split_m"])):
            add("bottom_chord", side, k, (a, t, zb), (b, t, zb))
        if design["end_posts"]:
            add("end_post", side, 0, (lay["s0"], t, zb), (lay["s0"], t, zt))
            add("end_post", side, 1, (lay["s1"], t, zb), (lay["s1"], t, zt))
        if warren:
            # the drawing: bottom nodes at the panel points, top nodes at
            # mid-panel, diagonals zigzag between them - no verticals, or
            # one at every node
            for i in range(n):
                mid = (pts[i] + pts[i + 1]) / 2.0
                add("diagonal", side, i, (pts[i], t, zb), (mid, t, zt), 0)
                add("diagonal", side, i, (mid, t, zt), (pts[i + 1], t, zb), 1)
            if design["truss"] == "warren_verticals":
                for i in range(1, n):
                    add("vertical", side, i, (pts[i], t, zb), (pts[i], t, zt), 0)
                for i in range(n):
                    mid = (pts[i] + pts[i + 1]) / 2.0
                    add("vertical", side, i, (mid, t, zb), (mid, t, zt), 1)
        else:
            for i in range(1, n):
                add("vertical", side, i, (pts[i], t, zb), (pts[i], t, zt))
            for i in range(n):
                for sub, (a, b) in enumerate(_diagonal(design["truss"], i, n, pts[i], pts[i + 1])):
                    za = zb if a[0] == "bottom" else zt
                    zb2 = zb if b[0] == "bottom" else zt
                    add("diagonal", side, i, (a[1], t, za), (b[1], t, zb2), sub)

    # cross beams at every bay line, between the truss planes, tops on the floor underside
    st = lay["stations"]
    for j, s in enumerate(st):
        add("crossbeam", "C", j, (s, tl, zu), (s, tr, zu))
    # plan bracing in every bay
    for j in range(len(st) - 1):
        s_a, s_b = st[j], st[j + 1]
        if design["bracing"] == "x":
            if design["split_x"]:
                sm, tm = (s_a + s_b) / 2.0, (tl + tr) / 2.0
                add("bracing", "C", j, (s_a, tl, zu), (sm, tm, zu), 0)
                add("bracing", "C", j, (sm, tm, zu), (s_b, tr, zu), 1)
                add("bracing", "C", j, (s_a, tr, zu), (sm, tm, zu), 2)
                add("bracing", "C", j, (sm, tm, zu), (s_b, tl, zu), 3)
            else:
                add("bracing", "C", j, (s_a, tl, zu), (s_b, tr, zu), 0)
                add("bracing", "C", j, (s_a, tr, zu), (s_b, tl, zu), 1)
        elif design["bracing"] == "zigzag":
            if j % 2 == 0:
                add("bracing", "C", j, (s_a, tl, zu), (s_b, tr, zu), 0)
            else:
                add("bracing", "C", j, (s_a, tr, zu), (s_b, tl, zu), 0)
    # stiffeners under the deck, along the span, between the cross beams
    d0, d1, t_r, t_l = deck_extent(design, frame)
    k = design["stiffeners_across"]
    if k > 0:
        gap = design["stiffener_gap_m"]
        for j in range(len(st) - 1):
            s_a, s_b = st[j] + gap, st[j + 1] - gap
            if s_b - s_a <= TOL:
                continue
            for m in range(k):
                t = t_r + (t_l - t_r) * (m + 1) / float(k + 1)
                add("stiffener", "C", j, (s_a, t, zu), (s_b, t, zu), m)
    # railings along the deck edges
    if (design["types"].get("railing") or u"") not in (u"", u"none"):
        ins = design["railing_inset_m"]
        add("railing", "L", 0, (d0, t_l - ins, zd), (d1, t_l - ins, zd))
        add("railing", "R", 0, (d0, t_r + ins, zd), (d1, t_r + ins, zd))
    return out


def deck_profile(design, frame):
    """The four corners of the deck in local (s, t) - start left, end
    left, end right, start right."""
    d0, d1, t_r, t_l = deck_extent(design, frame)
    return [(d0, t_l), (d1, t_l), (d1, t_r), (d0, t_r)]


def summary(design, frame, mems=None):
    """Counts and lengths for the page and the report."""
    mems = mems if mems is not None else members(design, frame)
    lay = layout(design, frame)
    by_role = {}
    steel = 0.0
    for m in mems:
        d = math.sqrt(sum((a - b) ** 2 for a, b in zip(m["p1"], m["p2"])))
        r = by_role.setdefault(m["role"], {"count": 0, "length_m": 0.0})
        r["count"] += 1
        r["length_m"] += d
        if m["role"] in FRAMING_ROLES:
            steel += d
    d0, d1, t_r, t_l = deck_extent(design, frame)
    return {"span_m": frame["span"], "panels": lay["n"], "panel_length_m": lay["L"],
            "truss_length_m": lay["total"], "bays": lay["bays"], "bay_length_m": lay["B"],
            "members": len(mems), "steel_length_m": steel, "by_role": by_role,
            "deck_length_m": d1 - d0, "deck_width_m": t_l - t_r,
            "deck_area_m2": (d1 - d0) * (t_l - t_r), "height_m": height(design, frame)}


def world_members(design, frame, shared_elev_m=0.0):
    """The members with p1 / p2 in model coordinates (metres), the typed
    end levels applied."""
    f = effective_frame(design, frame, shared_elev_m)
    out = []
    for m in members(design, f):
        w = dict(m)
        w["p1"] = to_world(f, m["p1"][0], m["p1"][1], m["p1"][2])
        w["p2"] = to_world(f, m["p2"][0], m["p2"][1], m["p2"][2])
        out.append(w)
    return out


def world_deck(design, frame, shared_elev_m=0.0):
    """The deck for the Revit side: corners (x, y), the slope arrow from
    the deck start to the deck end (x, y), the deck top z at the arrow's
    tail, the slope ratio, the thickness and the length."""
    f = effective_frame(design, frame, shared_elev_m)
    corners = [to_world(f, s, t)[:2] for s, t in deck_profile(design, f)]
    d0, d1, _tr, _tl = deck_extent(design, f)
    return {"corners": corners, "tail": to_world(f, d0, 0.0)[:2],
            "head": to_world(f, d1, 0.0)[:2],
            "tail_top_z": base_z(f, d0) + design["deck_top_offset_m"],
            "slope": f["gradient"], "thickness_m": design["floor_thickness_m"],
            "length_m": d1 - d0}


# ---------------------------------------------------------------------------
# 3. Updating a built bridge
# ---------------------------------------------------------------------------


def diff_members(old_keys, new_members):
    """What an update does with the tracked members: {"update": [(key,
    member)] for keys that still exist, "create": [member] for new
    keys, "delete": [key] for keys that vanished}."""
    new_by_key = dict((m["key"], m) for m in new_members)
    old = set(old_keys or [])
    return {"update": [(k, new_by_key[k]) for k in new_by_key if k in old],
            "create": [m for k, m in new_by_key.items() if k not in old],
            "delete": [k for k in old if k not in new_by_key]}


_SHAPE_KEYS = ("span", "tL", "tR", "post_size", "post_height")


def rigid_motion(old_frame, new_frame, tol=0.001):
    """When the bridge keeps its shape - span, width, post size and
    height, and the level difference - within tol metres, the whole
    thing just moves: (dx, dy, dz, dangle) from the old frame to the
    new, else None (the deck must be rebuilt)."""
    for k in _SHAPE_KEYS:
        if abs(float(old_frame.get(k, 0.0)) - float(new_frame.get(k, 0.0))) > tol:
            return None
    if abs((old_frame["z1"] - old_frame["z0"]) - (new_frame["z1"] - new_frame["z0"])) > tol:
        return None
    dx = new_frame["origin"][0] - old_frame["origin"][0]
    dy = new_frame["origin"][1] - old_frame["origin"][1]
    dz = new_frame["z0"] - old_frame["z0"]
    da = new_frame["angle"] - old_frame["angle"]
    while da > math.pi:
        da -= 2 * math.pi
    while da < -math.pi:
        da += 2 * math.pi
    return (dx, dy, dz, da)


def frame_changed(old_frame, new_frame, tol=0.0005):
    """False when the posts have not moved (within tol metres)."""
    a, b = old_frame["origin"], new_frame["origin"]
    if abs(a[0] - b[0]) > tol or abs(a[1] - b[1]) > tol:
        return True
    for k in _SHAPE_KEYS + ("z0", "z1"):
        if abs(float(old_frame.get(k, 0.0)) - float(new_frame.get(k, 0.0))) > tol:
            return True
    return abs(old_frame["angle"] - new_frame["angle"]) > 1e-7


# ---------------------------------------------------------------------------
# 4. The design file the HTML saves, and the tracking registry
# ---------------------------------------------------------------------------

def parse_design_file(text):
    """The design the HTML saved: {"name", "design", "source"} with the
    design normalised. ValueError when it is not a bridge design."""
    try:
        data = json.loads(text)
    except ValueError as ex:
        raise ValueError(u"not a JSON file ({0})".format(ex))
    if not isinstance(data, dict) or data.get("kind") != "pymep-bridge":
        raise ValueError(u"not a pyMEP bridge design (kind missing)")
    name = u"{0}".format(data.get("name") or u"").strip()
    if not name:
        raise ValueError(u"the design has no name")
    design = normalize_design(data.get("design") or {})
    source = data.get("source") or {}
    for k in ("start_ids", "end_ids"):
        ids = source.get(k)
        if not isinstance(ids, list) or len(ids) != 2:
            raise ValueError(u"the design does not say which posts it is for ({0} missing)".format(k))
    return {"name": name, "design": design, "source": source}


def design_file_text(name, design, source, extra=None):
    """What the HTML writes - also used by tests."""
    data = {"kind": "pymep-bridge", "name": name, "design": design, "source": source}
    if extra:
        data.update(extra)
    return json.dumps(data, indent=2, sort_keys=True)


def load_registry(path):
    """{"bridges": {name: record}} from the tracking file, empty when
    there is none or it cannot be read."""
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("bridges"), dict):
            return data
    except Exception:
        pass
    return {"bridges": {}}


def save_registry(path, registry):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(u"{0}".format(json.dumps(registry, indent=2, sort_keys=True)))


def next_name(registry, stem=u"Bridge"):
    names = set(registry.get("bridges", {}))
    i = 1
    while u"{0} {1}".format(stem, i) in names:
        i += 1
    return u"{0} {1}".format(stem, i)


FRAME_KEYS = ("z0", "z1", "span", "angle", "tL", "tR", "post_size", "post_height")


def record(name, design, source, frame, member_ids, floor_id, stamp):
    """A registry record for a built bridge. member_ids: {key: element
    id}."""
    fr = {"origin": list(frame["origin"])}
    for k in FRAME_KEYS:
        fr[k] = float(frame.get(k, 0.0))
    return {"name": name, "design": design, "source": source, "frame": fr,
            "members": dict((k, v) for k, v in member_ids.items()),
            "floor": floor_id, "updated": stamp}


def frame_from_record(rec):
    f = rec.get("frame") or {}
    ang = float(f.get("angle", 0.0))
    out = {"origin": tuple(f.get("origin", (0.0, 0.0))), "u": (math.cos(ang), math.sin(ang)),
           "v": (-math.sin(ang), math.cos(ang))}
    for k in FRAME_KEYS:
        out[k] = float(f.get(k, 0.0))
    out["gradient"] = (out["z1"] - out["z0"]) / out["span"] if out["span"] else 0.0
    return out


# ---------------------------------------------------------------------------
# 5. The HTML page
# ---------------------------------------------------------------------------

PLACEHOLDER = u"/*__BRIDGE_DATA__*/null"


def fill_page(template, data):
    """The builder page with its data written in: the template's
    placeholder becomes the JSON of data."""
    if PLACEHOLDER not in template:
        raise ValueError(u"the page template has no data placeholder")
    text = json.dumps(data, sort_keys=True).replace(u"</", u"<\\/")
    return template.replace(PLACEHOLDER, text, 1)


__all__ = ["TRUSSES", "BRACINGS", "PLANES", "ROLES", "FRAMING_ROLES", "ROLE_LABELS",
           "BRACE_ROLES", "TOP_ALIGNED", "DEFAULTS", "normalize_design", "frame_from_posts",
           "frame_from_points", "effective_frame", "planes", "height", "base_z", "to_world",
           "layout", "pieces",
           "deck_extent", "members", "deck_profile", "summary", "world_members", "world_deck",
           "diff_members", "rigid_motion", "frame_changed", "parse_design_file",
           "design_file_text", "load_registry", "save_registry", "next_name", "record",
           "frame_from_record", "FRAME_KEYS", "fill_page", "PLACEHOLDER"]
