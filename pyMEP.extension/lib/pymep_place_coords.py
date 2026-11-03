# -*- coding: utf-8 -*-
"""Place at Coordinates - the pure part (no Revit): reading the typed
points, turning them into internal feet, and searching the family list.

The Revit side (pyMEP.tab/12_Struct.panel/02_PlaceAtCoordinates) lists
the loaded family types and the levels, hands the typed text here, and
places one instance per point on the chosen level.

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
    is default_z when left out. Commas, semicolons, tabs or spaces
    separate the values. One extra word on the line, before or after the
    numbers, is kept as the point's name (written to Mark). Blank lines
    and lines starting with # are skipped. errors: [(line number, text,
    why)]."""
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
        if len(nums) > 3:
            errors.append((n, raw, u"more than three numbers"))
            continue
        x, y = float(nums[0]), float(nums[1])
        has_z = len(nums) == 3
        z = float(nums[2]) if has_z else float(default_z)
        points.append({"line": n, "text": raw.strip(), "x": x, "y": y, "z": z,
                       "has_z": has_z, "name": words[0] if words else u""})
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
        out.append({"line": p["line"], "name": p["name"], "typed": p["text"],
                    "x": ix, "y": iy, "z": float(level_elevation_ft) + off,
                    "offset": off, "typed_x": p["x"], "typed_y": p["y"],
                    "typed_z": p["z"]})
    return out


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


__all__ = ["FT_PER_UNIT", "UNITS", "SYSTEMS", "parse_points", "to_feet",
           "internal_xy", "shared_xy", "prepare", "search", "type_label",
           "summary_text"]
