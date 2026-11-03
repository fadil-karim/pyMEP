# -*- coding: utf-8 -*-
"""Place at Coordinates - the pure part (no Revit): reading the typed
points, turning them into internal feet, and searching the family list.

The Revit side (pyMEP.tab/13_Site.panel/01_PlaceAtCoordinates) lists
the loaded family types and the levels, hands the typed text here, and
places one instance per point on the chosen level; Element Coordinates
(13_Site.panel/02_ElementCoordinates) uses the reverse maths and the
table formatting to read coordinates back out.

Runs under IronPython 2.7 and CPython 3: .format() only, LF endings.
"""

import math
import re

FT_PER_UNIT = {"m": 1.0 / 0.3048, "mm": 1.0 / 304.8, "ft": 1.0}
UNITS = (("m", u"metres"), ("mm", u"millimetres"), ("ft", u"feet"))
SYSTEMS = (("internal", u"internal origin (model coordinates)"),
           ("pbp", u"project base point"),
           ("shared", u"shared / survey point (easting, northing)"))

_NUM = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")


def _tokens(line):
    """Cells of a line: split on commas, semicolons, tabs, or runs of
    spaces; '=' and ':' labels such as x=12.5 or E: 523.1 are dropped."""
    s = line.strip()
    if u"," in s or u";" in s or u"\t" in s:
        parts = re.split(r"[,;\t]", s)
    else:
        parts = s.split()
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        m = re.match(r"^[A-Za-z_][A-Za-z_ ]*[=:]\s*(.*)$", p)
        if m:
            p = m.group(1).strip()
            if not p:
                continue
        out.append(p)
    return out


def parse_points(text, default_z=0.0):
    """(points, errors) from text with one point per line.

    A line is x, y or x, y, z - z is the offset above the level and
    is default_z when left out. Four numbers are two points, x1, y1,
    x2, y2 (the start and end of a line based family, or the length
    and direction of an adaptive one), five add a common z. Commas,
    semicolons, tabs or spaces separate the values. One extra word on
    the line, before or after the numbers, is kept as the point's name
    (written to Mark). Blank lines and lines starting with # are
    skipped. errors: [(line number, text, why)]."""
    points = []
    errors = []
    for n, raw in enumerate(u"{0}".format(text or u"").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(u"#"):
            continue
        toks = _tokens(line)
        nums = [t for t in toks if _NUM.match(t)]
        words = [t for t in toks if not _NUM.match(t)]
        if len(words) > 1:
            errors.append((n, raw, u"more than one word on the line"))
            continue
        if len(nums) < 2:
            errors.append((n, raw, u"needs at least x and y"))
            continue
        if len(nums) > 5:
            errors.append((n, raw, u"more than five numbers"))
            continue
        x, y = float(nums[0]), float(nums[1])
        has_end = len(nums) >= 4
        x2 = float(nums[2]) if has_end else None
        y2 = float(nums[3]) if has_end else None
        has_z = len(nums) in (3, 5)
        z = float(nums[-1]) if has_z else float(default_z)
        points.append({"line": n, "text": raw.strip(), "x": x, "y": y, "z": z,
                       "has_z": has_z, "x2": x2, "y2": y2, "has_end": has_end,
                       "name": words[0] if words else u""})
    return points, errors


def to_feet(value, unit):
    """A length typed in unit ('m', 'mm', 'ft') as internal feet."""
    try:
        return float(value) * FT_PER_UNIT[unit]
    except KeyError:
        raise ValueError(u"unknown unit '{0}'".format(unit))


def internal_xy(x, y, system="internal", east=0.0, north=0.0, angle=0.0,
                pbp_x=0.0, pbp_y=0.0):
    """Internal (model) x, y in feet for a point typed in a coordinate
    system - everything in feet, angle in radians.

    internal: as typed.
    pbp:      relative to the project base point, whose internal
              position is (pbp_x, pbp_y); axes are project north, so no
              rotation.
    shared:   easting / northing. The internal origin sits at (east,
              north) in shared coordinates and the model is rotated by
              angle (the project position's Angle, anticlockwise from
              shared north to project north), so shared = R(angle) *
              internal + (east, north) and this is that inverse."""
    if system == "internal":
        return float(x), float(y)
    if system == "pbp":
        return float(x) + float(pbp_x), float(y) + float(pbp_y)
    if system == "shared":
        dx = float(x) - float(east)
        dy = float(y) - float(north)
        c = math.cos(float(angle))
        s = math.sin(float(angle))
        return dx * c + dy * s, -dx * s + dy * c
    raise ValueError(u"unknown coordinate system '{0}'".format(system))


def shared_xy(x, y, east=0.0, north=0.0, angle=0.0):
    """The other way: internal feet -> shared easting / northing (feet),
    for reporting what was placed."""
    c = math.cos(float(angle))
    s = math.sin(float(angle))
    return (float(x) * c - float(y) * s + float(east),
            float(x) * s + float(y) * c + float(north))


def from_feet(value, unit):
    """Internal feet shown in unit ('m', 'mm', 'ft')."""
    try:
        return float(value) / FT_PER_UNIT[unit]
    except KeyError:
        raise ValueError(u"unknown unit '{0}'".format(unit))


def coords_in(system, x, y, z, east=0.0, north=0.0, elev=0.0, angle=0.0,
              pbp_x=0.0, pbp_y=0.0, pbp_z=0.0):
    """An internal point (feet) as (X, Y, Z) feet in a coordinate system
    - the reverse of internal_xy, with the elevation carried too:
    pbp subtracts the base point's internal position, shared rotates
    and shifts by the project position (elev is its Elevation)."""
    x, y, z = float(x), float(y), float(z)
    if system == "internal":
        return x, y, z
    if system == "pbp":
        return x - float(pbp_x), y - float(pbp_y), z - float(pbp_z)
    if system == "shared":
        sx, sy = shared_xy(x, y, east, north, angle)
        return sx, sy, z + float(elev)
    raise ValueError(u"unknown coordinate system '{0}'".format(system))


AXES = {"internal": (u"X", u"Y", u"Z"), "pbp": (u"X", u"Y", u"Z"),
        "shared": (u"Easting", u"Northing", u"Elevation")}


def _fmt(value, unit, decimals=None):
    if decimals is None:
        decimals = 0 if unit == "mm" else 3
    return u"{0:.{1}f}".format(from_feet(value, unit), decimals)


def coords_table(rows, system, unit, decimals=None, sep=u"\t", **position):
    """Tab-separated text for a spreadsheet: a header, then one line per
    row - name, point, X, Y, Z in the system and unit, level, element.
    rows: dicts with name, point, x, y, z (internal feet), level, label,
    id."""
    ax = AXES.get(system, AXES["internal"])
    u = dict(UNITS).get(unit, unit)
    lines = [sep.join([u"Name", u"Point", u"{0} ({1})".format(ax[0], u),
                       u"{0} ({1})".format(ax[1], u), u"{0} ({1})".format(ax[2], u),
                       u"Level", u"Element", u"Id"])]
    for r in rows:
        X, Y, Z = coords_in(system, r["x"], r["y"], r["z"], **position)
        lines.append(sep.join([
            u"{0}".format(r.get("name") or u""), u"{0}".format(r.get("point") or u""),
            _fmt(X, unit, decimals), _fmt(Y, unit, decimals), _fmt(Z, unit, decimals),
            u"{0}".format(r.get("level") or u""), u"{0}".format(r.get("label") or u""),
            u"{0}".format(r.get("id") if r.get("id") is not None else u"")]))
    return u"\n".join(lines)


def place_lines(rows, system, unit, decimals=None, **position):
    """The rows as lines Place at Coordinates reads back - name, X, Y -
    one per row, in the same system and unit (no z: there it would be
    an offset above a level, not an elevation)."""
    out = []
    for r in rows:
        X, Y, _Z = coords_in(system, r["x"], r["y"], r["z"], **position)
        name = u"{0}".format(r.get("name") or u"").strip()
        name = name.split()[0] if name else u""
        cells = ([name] if name else []) + [_fmt(X, unit, decimals), _fmt(Y, unit, decimals)]
        out.append(u", ".join(cells))
    return u"\n".join(out)


def prepare(points, unit, level_elevation_ft, system="internal", **position):
    """The placement list: every parsed point as internal feet -
    x, y through the coordinate system and unit, z = level elevation +
    the offset (offset converted from unit). Each entry keeps the typed
    values for the report."""
    out = []
    for p in points:
        x_ft = to_feet(p["x"], unit)
        y_ft = to_feet(p["y"], unit)
        ix, iy = internal_xy(x_ft, y_ft, system, **position)
        off = to_feet(p["z"], unit)
        ex = ey = None
        if p.get("has_end"):
            ex, ey = internal_xy(to_feet(p["x2"], unit), to_feet(p["y2"], unit),
                                 system, **position)
        out.append({"line": p["line"], "name": p["name"], "typed": p["text"],
                    "x": ix, "y": iy, "z": float(level_elevation_ft) + off,
                    "x2": ex, "y2": ey, "has_end": bool(p.get("has_end")),
                    "has_z": bool(p.get("has_z")),
                    "offset": off, "typed_x": p["x"], "typed_y": p["y"],
                    "typed_z": p["z"]})
    return out


DEFAULT_LINE_FT = 1.0 / 0.3048      # a line based family with one point: 1 m east


def line_end(pt, length_ft=DEFAULT_LINE_FT):
    """(x2, y2) internal feet for a prepared point: the typed end when
    there is one, else length_ft due east of the point."""
    if pt.get("has_end") and pt.get("x2") is not None:
        return float(pt["x2"]), float(pt["y2"])
    return float(pt["x"]) + float(length_ft), float(pt["y"])


def spread_points(pt, count, spacing_ft=DEFAULT_LINE_FT):
    """count (x, y) internal points for an adaptive family: the typed
    point, the typed end when there is one, and the rest carrying on in
    that direction at spacing_ft (east when no end was typed)."""
    x, y = float(pt["x"]), float(pt["y"])
    if count <= 1:
        return [(x, y)]
    if pt.get("has_end") and pt.get("x2") is not None:
        ex, ey = float(pt["x2"]), float(pt["y2"])
        dx, dy = ex - x, ey - y
        length = math.hypot(dx, dy)
        ux, uy = (dx / length, dy / length) if length > 1e-9 else (1.0, 0.0)
        step = length if length > 1e-9 else spacing_ft
        pts = [(x, y), (ex, ey)]
        for i in range(2, count):
            pts.append((ex + ux * step * (i - 1), ey + uy * step * (i - 1)))
        return pts
    return [(x + spacing_ft * i, y) for i in range(count)]


# ---------------------------------------------------------------------------
# Moving elements to typed coordinates
# ---------------------------------------------------------------------------


def plan_moves(elements, targets, reference=0):
    """Which element goes where.

    elements: dicts with id, name (Mark), x, y, z (internal feet of the
              element's reference point), level_z (internal z of its
              level, or None) and flat (True for view-specific 2D
              elements - regions, annotations - whose z never moves).
    targets:  prepared points (see prepare) - x, y internal feet, z the
              level elevation + offset, has_z, name.

    Three ways to pair them: every target named -> matched to the
    element with that Mark (case aside); one target -> the reference
    element (index) lands on it and the others move by the same vector;
    as many targets as elements -> paired in order. Anything else is an
    error. Returns {"mode", "moves": [...], "warnings": [...], "stay":
    [elements left alone]}; each move has dx, dy, dz, dist (feet)."""
    if not elements:
        raise ValueError(u"nothing selected")
    if not targets:
        raise ValueError(u"type where to move to - x, y  or  x, y, z")
    named = [t for t in targets if t.get("name")]
    moves = []
    warnings = []
    pairs = []
    if named and len(named) == len(targets):
        mode = "named"
        by_mark = {}
        for e in elements:
            by_mark.setdefault(norm_name(e.get("name")), []).append(e)
        for t in targets:
            key = norm_name(t["name"])
            hits = by_mark.get(key, [])
            if not hits:
                warnings.append(u"no selected element has the Mark '{0}' (line {1})".format(
                    t["name"], t["line"]))
                continue
            if len(hits) > 1:
                warnings.append(u"{0} selected elements share the Mark '{1}' - all move".format(
                    len(hits), t["name"]))
            for e in hits:
                pairs.append((e, t, None))
    elif named:
        raise ValueError(u"either name every line with a Mark or none of them")
    elif len(targets) == 1:
        mode = "group"
        ref = elements[reference] if 0 <= reference < len(elements) else elements[0]
        t = targets[0]
        dx = float(t["x"]) - float(ref["x"])
        dy = float(t["y"]) - float(ref["y"])
        dz = 0.0
        if t.get("has_z") and ref.get("level_z") is not None and not ref.get("flat"):
            dz = (float(ref["level_z"]) + float(t["offset"])) - float(ref["z"])
        for e in elements:
            pairs.append((e, t, (dx, dy, dz)))
    elif len(targets) == len(elements):
        mode = "ordered"
        for e, t in zip(elements, targets):
            pairs.append((e, t, None))
    else:
        raise ValueError(u"{0} element(s) but {1} line(s) - give one line to move them "
                         u"together, one line per element, or name each line with a "
                         u"Mark".format(len(elements), len(targets)))
    moved_ids = set()
    for e, t, vec in pairs:
        if vec is None:
            dx = float(t["x"]) - float(e["x"])
            dy = float(t["y"]) - float(e["y"])
            dz = 0.0
            if t.get("has_z") and not e.get("flat"):
                if e.get("level_z") is not None:
                    dz = (float(e["level_z"]) + float(t["offset"])) - float(e["z"])
                else:
                    warnings.append(u"'{0}' has no level - its z is left alone".format(
                        e.get("name") or e.get("label") or e.get("id")))
        else:
            dx, dy, dz = vec
        if e.get("flat"):
            dz = 0.0
        moves.append({"id": e["id"], "name": e.get("name") or u"", "label": e.get("label") or u"",
                      "flat": bool(e.get("flat")),
                      "from": (float(e["x"]), float(e["y"]), float(e["z"])),
                      "to": (float(e["x"]) + dx, float(e["y"]) + dy, float(e["z"]) + dz),
                      "dx": dx, "dy": dy, "dz": dz,
                      "dist": math.sqrt(dx * dx + dy * dy + dz * dz), "line": t["line"]})
        moved_ids.add(e["id"])
    stay = [e for e in elements if e["id"] not in moved_ids]
    return {"mode": mode, "moves": moves, "warnings": warnings, "stay": stay}


def norm_name(text):
    return u" ".join(u"{0}".format(text or u"").split()).lower()


def moves_text(plan, system, unit, decimals=None, **position):
    """The move plan as lines for the dialog: each element, where it is
    and where it goes in the chosen system and unit, and how far."""
    mode = {"named": u"matched by Mark", "group": u"moved together - the reference "
            u"element lands on the point", "ordered": u"paired in list order"}.get(
                plan["mode"], plan["mode"])
    lines = [u"{0} element(s) move ({1}).".format(len(plan["moves"]), mode)]
    for m in plan["moves"]:
        fx, fy, fz = coords_in(system, m["from"][0], m["from"][1], m["from"][2], **position)
        tx, ty, tz = coords_in(system, m["to"][0], m["to"][1], m["to"][2], **position)
        who = m["name"] or m["label"] or u"{0}".format(m["id"])
        if abs(m["dz"]) > 1e-9:
            lines.append(u"  {0}: {1}, {2}, {3}  ->  {4}, {5}, {6}   ({7} {8})".format(
                who, _fmt(fx, unit, decimals), _fmt(fy, unit, decimals), _fmt(fz, unit, decimals),
                _fmt(tx, unit, decimals), _fmt(ty, unit, decimals), _fmt(tz, unit, decimals),
                _fmt(m["dist"], unit, decimals), unit))
        else:
            lines.append(u"  {0}: {1}, {2}  ->  {3}, {4}   ({5} {6})".format(
                who, _fmt(fx, unit, decimals), _fmt(fy, unit, decimals),
                _fmt(tx, unit, decimals), _fmt(ty, unit, decimals),
                _fmt(m["dist"], unit, decimals), unit))
    for e in plan["stay"]:
        lines.append(u"  {0}: stays (no line for it)".format(
            e.get("name") or e.get("label") or e.get("id")))
    for w in plan["warnings"]:
        lines.append(u"  ! {0}".format(w))
    return u"\n".join(lines)


def search(entries, text, category=None):
    """The family types whose 'Family : Type' label (or category) holds
    every word typed, case aside, within one category when given.
    entries: dicts with label and category (plus whatever the caller
    needs); the result keeps the entries' order."""
    words = [w for w in u"{0}".format(text or u"").lower().split() if w]
    out = []
    for e in entries:
        if category and e.get("category") != category:
            continue
        hay = u"{0} {1}".format(e.get("label", u""), e.get("category", u"")).lower()
        if all(w in hay for w in words):
            out.append(e)
    return out


def type_label(family, type_name):
    return u"{0} : {1}".format(family, type_name)


def summary_text(points, errors, unit):
    """One line for the dialog: how many points read, and the first
    error when there is one."""
    bits = []
    if points:
        with_z = len([p for p in points if p["has_z"]])
        bits.append(u"{0} point(s) read in {1}".format(len(points), dict(UNITS).get(unit, unit)))
        with_end = len([p for p in points if p.get("has_end")])
        if with_end:
            bits.append(u"{0} with an end point (line based families)".format(with_end))
        if with_z and with_z < len(points):
            bits.append(u"{0} with an offset, the rest at the level".format(with_z))
        elif with_z:
            bits.append(u"all with an offset above the level")
        else:
            bits.append(u"all at the level (no z typed)")
        named = len([p for p in points if p["name"]])
        if named:
            bits.append(u"{0} named (goes to Mark)".format(named))
    if errors:
        n, raw, why = errors[0]
        bits.append(u"line {0} skipped - {1}: '{2}'{3}".format(
            n, why, raw.strip(), u" (+{0} more)".format(len(errors) - 1)
            if len(errors) > 1 else u""))
    if not points and not errors:
        return u"Type one point per line: x, y  or  x, y, z."
    return u"; ".join(bits) + u"."


__all__ = ["FT_PER_UNIT", "UNITS", "SYSTEMS", "AXES", "DEFAULT_LINE_FT", "parse_points",
           "to_feet", "from_feet", "internal_xy", "shared_xy", "coords_in", "coords_table",
           "place_lines", "prepare", "line_end", "spread_points", "plan_moves", "norm_name",
           "moves_text", "search", "type_label", "summary_text"]
