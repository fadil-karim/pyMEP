# -*- coding: utf-8 -*-
"""Copy CAD Lines - the pure part (no Revit): the layer list, the line
style choice, flattening points onto the target plane and the report.

The Revit side (pyMEP.tab/15_CADCoordination.panel/01_CopyCADLines)
reads the curves of a linked or imported CAD instance with their layer
names, shows the dialog and makes the detail or model lines.

Runs under IronPython 2.7 and CPython 3: .format() only, LF endings.
"""

import math

SETTINGS_LAYERS = "cad_lines_layers"
SETTINGS_STYLE = "cad_lines_style"
SETTINGS_MODE = "cad_lines_mode"            # "detail" or "model"
SETTINGS_LEVEL = "cad_lines_level"
NO_LAYER = u"(no layer)"

# line styles Revit lists under Lines that cannot be given to a detail or
# model line (sketch and boundary styles of other tools)
NOT_DRAWABLE = (u"<Sketch>", u"<Area Boundary>", u"<Room Separation>", u"<Space Separation>",
                u"<Beam Systems>", u"<Insulation Batting Lines>", u"<Path of Travel Lines>",
                u"<Zone Boundary>", u"<MEP Space Separation>")
DEFAULT_STYLES = (u"<Thin Lines>", u"Thin Lines", u"Lines", u"<Lines>")


def _key(name):
    return u"{0}".format(name).lower()


def layer_rows(counts, ticked=()):
    """[{"name", "count", "pick"}] for the layers, by name (case aside);
    the layers in ticked start ticked."""
    want = set(_key(t) for t in (ticked or ()))
    return [{"name": n, "count": int(c), "pick": _key(n) in want}
            for n, c in sorted(counts.items(), key=lambda kv: _key(kv[0]))]


def matches(name, text):
    """True when every word of text is in name (case aside)."""
    words = [w for w in _key(text or u"").split() if w]
    n = _key(name)
    return all(w in n for w in words)


def rowfilter(text):
    """A DataView RowFilter keeping the rows whose Layer holds every word
    of text - u"" for no filter. Quotes are doubled and the LIKE
    wildcards bracketed so any layer name can be searched for."""
    parts = []
    for w in (text or u"").split():
        w = w.replace(u"'", u"''")
        w = u"".join(u"[{0}]".format(ch) if ch in u"[]*%" else ch for ch in w)
        parts.append(u"Layer LIKE '%{0}%'".format(w))
    return u" AND ".join(parts)


def usable_styles(names):
    """The line style names a detail or model line can take, sorted with
    the <...> system styles first."""
    out = [n for n in set(names) if n not in NOT_DRAWABLE]
    return sorted(out, key=lambda n: (0 if n.startswith(u"<") else 1, _key(n)))


def default_style(names, remembered=None):
    """The style to offer: the one used last time when it is still
    there, else thin lines, else the first."""
    if remembered and remembered in names:
        return remembered
    for n in DEFAULT_STYLES:
        if n in names:
            return n
    return names[0] if names else None


def is_flat(zs, tol=1e-6):
    """True when the heights all agree (a curve drawn flat)."""
    zs = list(zs)
    return not zs or max(zs) - min(zs) <= tol


def segments(points, z, min_len):
    """Consecutive pairs of (x, y, z) points as line ends on the plane at
    height z, skipping any shorter than min_len (in plan)."""
    out = []
    prev = None
    for p in points:
        q = (float(p[0]), float(p[1]), float(z))
        if prev is not None and math.hypot(q[0] - prev[0], q[1] - prev[1]) >= min_len:
            out.append((prev, q))
            prev = q
        elif prev is None:
            prev = q
    return out


def total(rows):
    """Lines on the ticked layers."""
    return sum(r["count"] for r in rows if r["pick"])


def report_rows(made, failed, short):
    """[[layer, made, failed, too short]] for every layer that had a line,
    by name; and the totals row last."""
    names = sorted(set(made) | set(failed) | set(short), key=_key)
    rows = [[n, made.get(n, 0), failed.get(n, 0), short.get(n, 0)] for n in names]
    rows.append([u"Total", sum(made.values()), sum(failed.values()), sum(short.values())])
    return rows
