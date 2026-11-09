# -*- coding: utf-8 -*-
"""Bridge Builder - the pure part (no Revit): the design of a steel truss
footbridge, the members it makes between two points, the frame that
puts them in the model, what an update must change, and the tracking
registry.

A bridge runs between a START and an END point - two generic model
instances whose chosen parameter gives the deck level at that end. The
deck is a sloped floor between them; two trusses (left / right) carry
chords, verticals and diagonals; cross beams join the bottom chords
under the deck and optional plan bracing zigzags between them. Members
are structural framing beams placed on lines.

Local coordinates: s along the span from the start point (metres), t
across (left positive), z relative to the DECK TOP at that s - the deck
top itself follows the two level values linearly. frame_from_points
turns (s, t, z) into model coordinates.

Everything here is metres; the Revit side converts to feet.

Runs under IronPython 2.7 and CPython 3: .format() only, LF endings.
"""

import io
import json
import math
import os

TRUSSES = (("warren", u"Warren (zigzag diagonals, no verticals)"),
           ("warren_verticals", u"Warren with verticals"),
           ("pratt", u"Pratt (diagonals lean to the centre)"),
           ("howe", u"Howe (diagonals lean to the ends)"),
           ("x", u"X braced (both diagonals)"),
           ("vierendeel", u"Vierendeel (verticals only)"))
BRACINGS = (("none", u"none"), ("zigzag", u"zigzag (Warren)"), ("x", u"X in every bay"))
ROLES = ("top_chord", "bottom_chord", "end_post", "vertical", "diagonal",
         "crossbeam", "bracing")
ROLE_LABELS = {"top_chord": u"Top chord", "bottom_chord": u"Bottom chord",
               "end_post": u"End post", "vertical": u"Vertical",
               "diagonal": u"Diagonal", "crossbeam": u"Cross beam", "bracing": u"Plan bracing"}
BRACE_ROLES = ("diagonal", "bracing")       # placed as braces, the rest as beams

DEFAULTS = {
    "truss": "warren_verticals",
    "panel_mode": "length",          # "length": aim for panel_length_m; "count": panels
    "panel_length_m": 1.92,
    "panels": 14,
    "truss_height_m": 1.8,           # bottom chord centreline to top chord centreline
    "truss_spacing_m": 3.0,          # left truss centreline to right truss centreline
    "deck_width_m": 2.7,             # the floor's width, centred between the trusses
    "deck_top_offset_m": 0.0,        # deck top relative to the level value at each end
    "bottom_chord_offset_m": -0.35,  # bottom chord centreline relative to the deck top
    "crossbeam_offset_m": -0.25,     # cross beam centreline relative to the deck top
    "crossbeams_per_panel": 1,
    "bracing": "zigzag",
    "bracing_offset_m": -0.45,
    "end_posts": True,
    "start_extension_m": 0.0,        # trusses and deck run this far beyond the points
    "end_extension_m": 0.0,
    "types": {},                     # role -> framing type id (text); "floor" -> floor type id
    "level": None,                   # level id the floor and beams reference
}
TOL = 1e-6


def normalize_design(d):
    """A full design dict from a partial one: defaults filled, numbers as
    floats, counts as ints. ValueError when a value cannot work."""
    out = {}
    d = d or {}
    for k, v in DEFAULTS.items():
        out[k] = d.get(k, v) if d.get(k) is not None or k in ("level",) else v
    if out["truss"] not in [k for k, _l in TRUSSES]:
        raise ValueError(u"unknown truss type '{0}'".format(out["truss"]))
    if out["bracing"] not in [k for k, _l in BRACINGS]:
        raise ValueError(u"unknown bracing '{0}'".format(out["bracing"]))
    for k in ("panel_length_m", "truss_height_m", "truss_spacing_m", "deck_width_m",
              "deck_top_offset_m", "bottom_chord_offset_m", "crossbeam_offset_m",
              "bracing_offset_m", "start_extension_m", "end_extension_m"):
        try:
            out[k] = float(out[k])
        except (TypeError, ValueError):
            raise ValueError(u"'{0}' must be a number".format(k))
    for k in ("panels", "crossbeams_per_panel"):
        try:
            out[k] = int(round(float(out[k])))
        except (TypeError, ValueError):
            raise ValueError(u"'{0}' must be a whole number".format(k))
    if out["panel_mode"] not in ("length", "count"):
        raise ValueError(u"panel_mode must be 'length' or 'count'")
    if out["panel_length_m"] <= 0 or out["panels"] < 1:
        raise ValueError(u"panel length must be positive and panels at least 1")
    if out["truss_height_m"] <= 0 or out["truss_spacing_m"] <= 0 or out["deck_width_m"] <= 0:
        raise ValueError(u"truss height, truss spacing and deck width must be positive")
    if out["crossbeams_per_panel"] < 1:
        out["crossbeams_per_panel"] = 1
    out["end_posts"] = bool(out["end_posts"])
    out["types"] = dict(out["types"] or {})
    return out


# ---------------------------------------------------------------------------
# 1. The layout along the span
# ---------------------------------------------------------------------------


def layout(design, span):
    """Where the panel points are: {"s0", "s1", "n", "L", "points"} - the
    truss runs from s0 (-start extension) to s1 (span + end extension)
    in n panels of length L; points are the n + 1 panel stations."""
    if span <= TOL:
        raise ValueError(u"the start and end points are at the same place")
    s0 = -design["start_extension_m"]
    s1 = span + design["end_extension_m"]
    total = s1 - s0
    if design["panel_mode"] == "count":
        n = max(1, int(design["panels"]))
    else:
        n = max(1, int(round(total / design["panel_length_m"])))
    L = total / n
    return {"s0": s0, "s1": s1, "n": n, "L": L,
            "points": [s0 + L * i for i in range(n + 1)], "total": total}


def _diagonal(truss, i, n, s_a, s_b):
    """(from, to) ends of the diagonal(s) in panel i as 'bottom'/'top'
    at s_a / s_b; [] for Vierendeel."""
    if truss == "vierendeel":
        return []
    if truss == "x":
        return [(("bottom", s_a), ("top", s_b)), (("top", s_a), ("bottom", s_b))]
    if truss in ("warren", "warren_verticals"):
        return [(("bottom", s_a), ("top", s_b))] if i % 2 == 0 else [(("top", s_a), ("bottom", s_b))]
    left_half = (i + 0.5) < n / 2.0
    if truss == "pratt":        # diagonals lean down towards the centre
        return [(("top", s_a), ("bottom", s_b))] if left_half else [(("bottom", s_a), ("top", s_b))]
    if truss == "howe":         # diagonals lean down towards the ends
        return [(("bottom", s_a), ("top", s_b))] if left_half else [(("top", s_a), ("bottom", s_b))]
    return []


def members(design, span):
    """Every member of the bridge in local coordinates: dicts with key
    (unique, stable across rebuilds), role, side ('L', 'R' or 'C'), i,
    p1, p2 as (s, t, z) with z relative to the deck top, and stype
    ('beam' or 'brace')."""
    lay = layout(design, span)
    half = design["truss_spacing_m"] / 2.0
    zb = design["bottom_chord_offset_m"]
    zt = zb + design["truss_height_m"]
    zc = design["crossbeam_offset_m"]
    zx = design["bracing_offset_m"]
    pts = lay["points"]
    n = lay["n"]
    out = []

    def add(role, side, i, p1, p2, sub=0):
        key = u"{0}:{1}:{2}:{3}".format(role, side, i, sub)
        out.append({"key": key, "role": role, "side": side, "i": i, "p1": p1, "p2": p2,
                    "stype": "brace" if role in BRACE_ROLES else "beam"})

    for side, t in (("L", half), ("R", -half)):
        add("top_chord", side, 0, (lay["s0"], t, zt), (lay["s1"], t, zt))
        add("bottom_chord", side, 0, (lay["s0"], t, zb), (lay["s1"], t, zb))
        if design["end_posts"]:
            add("end_post", side, 0, (lay["s0"], t, zb), (lay["s0"], t, zt))
            add("end_post", side, 1, (lay["s1"], t, zb), (lay["s1"], t, zt))
        if design["truss"] != "warren":
            for i in range(1, n):
                add("vertical", side, i, (pts[i], t, zb), (pts[i], t, zt))
        for i in range(n):
            for sub, (a, b) in enumerate(_diagonal(design["truss"], i, n, pts[i], pts[i + 1])):
                za = zb if a[0] == "bottom" else zt
                zb2 = zb if b[0] == "bottom" else zt
                add("diagonal", side, i, (a[1], t, za), (b[1], t, zb2), sub)

    # cross beams under the deck, between the bottom chords
    per = design["crossbeams_per_panel"]
    stations = []
    for i in range(n):
        for k in range(per):
            stations.append(pts[i] + lay["L"] * k / float(per))
    stations.append(pts[n])
    for j, s in enumerate(stations):
        add("crossbeam", "C", j, (s, half, zc), (s, -half, zc))

    # plan bracing between consecutive cross beams
    if design["bracing"] != "none":
        for j in range(len(stations) - 1):
            s_a, s_b = stations[j], stations[j + 1]
            if design["bracing"] == "x":
                add("bracing", "C", j, (s_a, half, zx), (s_b, -half, zx), 0)
                add("bracing", "C", j, (s_a, -half, zx), (s_b, half, zx), 1)
            else:
                if j % 2 == 0:
                    add("bracing", "C", j, (s_a, half, zx), (s_b, -half, zx), 0)
                else:
                    add("bracing", "C", j, (s_a, -half, zx), (s_b, half, zx), 0)
    return out


def deck_profile(design, span):
    """The four corners of the deck in local (s, t) - start left, end
    left, end right, start right."""
    lay = layout(design, span)
    w = design["deck_width_m"] / 2.0
    return [(lay["s0"], w), (lay["s1"], w), (lay["s1"], -w), (lay["s0"], -w)]


def summary(design, span, mems=None):
    """Counts and lengths for the dialog and the report."""
    mems = mems if mems is not None else members(design, span)
    lay = layout(design, span)
    by_role = {}
    total = 0.0
    for m in mems:
        d = math.sqrt(sum((a - b) ** 2 for a, b in zip(m["p1"], m["p2"])))
        r = by_role.setdefault(m["role"], {"count": 0, "length_m": 0.0})
        r["count"] += 1
        r["length_m"] += d
        total += d
    return {"span_m": span, "panels": lay["n"], "panel_length_m": lay["L"],
            "truss_length_m": lay["total"], "members": len(mems), "steel_length_m": total,
            "by_role": by_role, "deck_area_m2": lay["total"] * design["deck_width_m"]}


# ---------------------------------------------------------------------------
# 2. The frame: local (s, t, z) to model x, y, z
# ---------------------------------------------------------------------------


def frame_from_points(start, end):
    """start, end: (x, y, z_deck) in metres - the points' positions and
    the deck top level at each. Returns the frame dict used by
    to_world: origin, unit vectors along and across, span, the two deck
    levels and the gradient."""
    dx = float(end[0]) - float(start[0])
    dy = float(end[1]) - float(start[1])
    span = math.hypot(dx, dy)
    if span <= TOL:
        raise ValueError(u"the start and end points are at the same place")
    ux, uy = dx / span, dy / span
    return {"origin": (float(start[0]), float(start[1])), "u": (ux, uy), "v": (-uy, ux),
            "span": span, "z0": float(start[2]), "z1": float(end[2]),
            "gradient": (float(end[2]) - float(start[2])) / span,
            "angle": math.atan2(uy, ux)}


def deck_z(frame, s, deck_top_offset=0.0):
    """Model z of the deck top at station s (extrapolates beyond the
    points)."""
    return frame["z0"] + frame["gradient"] * s + deck_top_offset


def to_world(frame, s, t, z_rel=0.0, deck_top_offset=0.0):
    """(x, y, z) in the model for local (s, t, z relative to the deck
    top)."""
    ox, oy = frame["origin"]
    ux, uy = frame["u"]
    vx, vy = frame["v"]
    return (ox + ux * s + vx * t, oy + uy * s + vy * t,
            deck_z(frame, s, deck_top_offset) + z_rel)


def world_members(design, frame):
    """The members with p1 / p2 in model coordinates (metres), ready to
    become beams."""
    out = []
    for m in members(design, frame["span"]):
        w = dict(m)
        w["p1"] = to_world(frame, m["p1"][0], m["p1"][1], m["p1"][2], design["deck_top_offset_m"])
        w["p2"] = to_world(frame, m["p2"][0], m["p2"][1], m["p2"][2], design["deck_top_offset_m"])
        out.append(w)
    return out


def world_deck(design, frame):
    """The deck for the Revit side: corners (x, y) in model coordinates,
    the slope arrow from the start station to the end station (x, y),
    the deck top z at the arrow's tail, and the slope ratio (rise per
    run along the arrow)."""
    corners = [to_world(frame, s, t)[:2] for s, t in deck_profile(design, frame["span"])]
    lay = layout(design, frame["span"])
    tail = to_world(frame, lay["s0"], 0.0)[:2]
    head = to_world(frame, lay["s1"], 0.0)[:2]
    return {"corners": corners, "tail": tail, "head": head,
            "tail_top_z": deck_z(frame, lay["s0"], design["deck_top_offset_m"]),
            "slope": frame["gradient"], "length_m": lay["total"]}


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


def rigid_motion(old_frame, new_frame, tol=0.001):
    """When the bridge keeps its span and its level difference (within
    tol metres), the whole thing just moves: returns (dx, dy, dz, dangle)
    taking the old frame onto the new one, else None - the deck must be
    rebuilt then."""
    if abs(old_frame["span"] - new_frame["span"]) > tol:
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
    """False when the points have not moved (within tol metres) - nothing
    to update."""
    for a, b in ((old_frame["origin"], new_frame["origin"]),):
        if abs(a[0] - b[0]) > tol or abs(a[1] - b[1]) > tol:
            return True
    return (abs(old_frame["span"] - new_frame["span"]) > tol
            or abs(old_frame["angle"] - new_frame["angle"]) > 1e-7
            or abs(old_frame["z0"] - new_frame["z0"]) > tol
            or abs(old_frame["z1"] - new_frame["z1"]) > tol)


# ---------------------------------------------------------------------------
# 4. The design file the HTML saves, and the tracking registry
# ---------------------------------------------------------------------------

LEVEL_MODES = (("shared", u"absolute elevation in shared / survey coordinates"),
               ("internal", u"absolute elevation in the model's internal coordinates"),
               ("offset", u"offset above the element's own level"))


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
    for k in ("start_id", "end_id", "level_param", "level_mode"):
        if k not in source:
            raise ValueError(u"the design does not say which points it is for ({0} missing)".format(k))
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


def record(name, design, source, frame, member_ids, floor_id, stamp):
    """A registry record for a built bridge. member_ids: {key: element
    id}."""
    return {"name": name, "design": design, "source": source,
            "frame": {"origin": list(frame["origin"]), "z0": frame["z0"], "z1": frame["z1"],
                      "span": frame["span"], "angle": frame["angle"]},
            "members": dict((k, v) for k, v in member_ids.items()),
            "floor": floor_id, "updated": stamp}


def frame_from_record(rec):
    f = rec.get("frame") or {}
    ang = float(f.get("angle", 0.0))
    return {"origin": tuple(f.get("origin", (0.0, 0.0))), "u": (math.cos(ang), math.sin(ang)),
            "v": (-math.sin(ang), math.cos(ang)), "span": float(f.get("span", 0.0)),
            "z0": float(f.get("z0", 0.0)), "z1": float(f.get("z1", 0.0)),
            "gradient": ((float(f.get("z1", 0.0)) - float(f.get("z0", 0.0))) / float(f["span"])
                         if f.get("span") else 0.0),
            "angle": ang}


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


__all__ = ["TRUSSES", "BRACINGS", "ROLES", "ROLE_LABELS", "BRACE_ROLES", "DEFAULTS",
           "LEVEL_MODES", "normalize_design", "layout", "members", "deck_profile", "summary",
           "frame_from_points", "deck_z", "to_world", "world_members", "world_deck",
           "diff_members", "rigid_motion", "frame_changed", "parse_design_file",
           "design_file_text", "load_registry", "save_registry", "next_name", "record",
           "frame_from_record", "fill_page", "PLACEHOLDER"]
