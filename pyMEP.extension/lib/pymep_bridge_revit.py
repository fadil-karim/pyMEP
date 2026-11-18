# -*- coding: utf-8 -*-
"""Bridge Builder - the Revit side shared by the three Bridges buttons:
reading the four posts, the model's types and levels, and making,
moving and re-curving the deck floor, the framing members, the deck
stiffeners and the railings.

IronPython 2.7 only (needs the Revit API): .format() only, LF endings.
"""

import math
import os

import clr
clr.AddReference("RevitAPI")
import System
from Autodesk.Revit.DB import (BuiltInCategory, BuiltInParameter, CurveArray,
                               CurveLoop, ElementId, ElementTransformUtils, FamilySymbol,
                               FilteredElementCollector, FloorType, HostObjectUtils,
                               Level, Line, XYZ)
from Autodesk.Revit.DB.Architecture import Railing, RailingType
from Autodesk.Revit.DB.Structure import StructuralType
from System.Collections.Generic import List

import pymep_bridge as BR
import pymep_csv_params as CP
from pymep_config import get_export_folder
from pymep_revit import id_value, make_id, type_name

FT = 0.3048


def m_to_ft(x):
    return float(x) / FT


def ft_to_m(x):
    return float(x) * FT


def _ids_text(ids):
    return u", ".join(u"{0}".format(i) for i in ids)


def read_posts(doc, rec):
    """The bridge's posts as they are now: {"start", "end" (post_info
    lists, or None), "missing" (ids no longer in the model), "error"
    (why a post that is there cannot be read, else u"")}."""
    src = rec.get("source", {}) or {}
    out = {"start": None, "end": None, "missing": [], "error": u""}
    pairs = []
    for key in ("start_ids", "end_ids"):
        pair = []
        for i in src.get(key, []) or []:
            try:
                el = doc.GetElement(make_id(int(i)))
            except Exception:
                el = None
            if el is None:
                out["missing"].append(int(i))
                continue
            try:
                pair.append(post_info(el))
            except Exception as ex:
                if not out["error"]:
                    out["error"] = u"post {0}: {1}".format(i, ex)
        pairs.append(pair)
    if not out["missing"] and not out["error"] and len(pairs) == 2:
        out["start"], out["end"] = pairs
    return out


def posts_problem(doc, rec):
    """u"" when all four posts can be read, else one line saying which are
    gone (or why one cannot be read)."""
    p = read_posts(doc, rec)
    if p["missing"]:
        return u"post{0} {1} {2} no longer in the model".format(
            u"s" if len(p["missing"]) > 1 else u"", _ids_text(p["missing"]),
            u"are" if len(p["missing"]) > 1 else u"is")
    if p["error"]:
        return p["error"]
    if p["start"] is None:
        return u"the bridge does not say which four posts it is on"
    return u""


def mark_of(el):
    """The bridge name a member carries in Comments, or u""."""
    try:
        p = el.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        return u"{0}".format(p.AsString() or u"") if p is not None else u""
    except Exception:
        return u""


def _recorded_ids(rec):
    ids = []
    for v in list((rec.get("members") or {}).values()) + [rec.get("floor")]:
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            continue
    return ids


def classify(doc, rec, name, others=()):
    """{recorded id: "ours" | "gone" | "other"} for a bridge's members and
    deck. "ours": still in the model and marked with the bridge's name in
    Comments - its name now, the record's name, the names it was renamed
    from, or (for a bridge renamed before those were kept) the one mark
    most of its elements carry, unless that is another tracked bridge's
    name (others). "other": in the model but not this bridge's - a post,
    or unmarked / marked otherwise - so never moved, re-marked or
    deleted. "gone": no longer in the model."""
    posts = set(post_ids_of(rec))
    marks = set([u"{0}".format(name), u"{0}".format(rec.get("name") or name)] +
                [u"{0}".format(a) for a in (rec.get("aliases") or [])])
    others = set(u"{0}".format(o) for o in others) - marks
    found = {}
    out = {}
    for i in _recorded_ids(rec):
        el = doc.GetElement(make_id(i))
        if el is None:
            out[i] = "gone"
        elif i in posts:
            out[i] = "other"
        else:
            found[i] = mark_of(el)
    counts = {}
    for m in found.values():
        if m and m not in marks and m not in others:
            counts[m] = counts.get(m, 0) + 1
    if counts:
        top = max(counts.items(), key=lambda kv: kv[1])
        if top[1] * 2 > len(found):
            marks.add(top[0])
    for i, m in found.items():
        out[i] = "ours" if m in marks else "other"
    return out


def bridge_elements(doc, rec, name, others=()):
    """(ours, gone, other): the recorded element ids that are this
    bridge's, how many are no longer in the model, and those that are
    there but are not this bridge's (see classify) - never to be
    touched."""
    cls = classify(doc, rec, name, others)
    ours = [i for i, c in cls.items() if c == "ours"]
    other = [i for i, c in cls.items() if c == "other"]
    return ours, len([1 for c in cls.values() if c == "gone"]), other


def post_ids_of(rec):
    src = rec.get("source", {}) or {}
    out = []
    for i in list(src.get("start_ids", []) or []) + list(src.get("end_ids", []) or []):
        try:
            out.append(int(i))
        except (TypeError, ValueError):
            pass
    return out


def delete_bridge(doc, rec, name, others=()):
    """Inside an open transaction, delete the bridge's elements - only
    those that are still there and carry its name; never its posts.
    The deck goes first (its stiffeners and railings go with it).
    Returns {"deleted", "gone", "kept"}."""
    ours, gone, other = bridge_elements(doc, rec, name, others)
    floor = rec.get("floor")
    first = [i for i in ours if floor is not None and i == int(floor)]
    done = delete_quiet(doc, first) if first else 0
    done += delete_quiet(doc, [i for i in ours if i not in first])
    return {"deleted": done, "gone": gone, "kept": other}


def bridge_rows(doc, registry):
    """One row per tracked bridge for the Bridges dialog: name, post ids,
    member count, last build and a status against the posts as they are
    now - unchanged, posts moved (a rigid move), posts moved / levels
    changed, layout updated in pyMEP, or which posts are gone and how
    much of the bridge is left (the real reason when a post cannot be
    read)."""
    import pymep_bridge as BR
    rows = []
    for name in sorted(registry.get("bridges", {})):
        rec = registry["bridges"][name]
        ids = post_ids_of(rec)
        missing = []
        try:
            p = read_posts(doc, rec)
            missing = p["missing"]
            if missing or p["error"] or p["start"] is None:
                ours, gone, other = bridge_elements(doc, rec, name, [n for n in registry["bridges"] if n != name])
                left = u"{0} of {1} elements still this bridge's".format(len(ours), len(ours) + gone + len(other))
                if gone:
                    left += u", {0} gone".format(gone)
                if other:
                    left += u", {0} recorded ids now other elements".format(len(other))
                if missing:
                    status = u"post{0} {1} gone; {2}".format(u"s" if len(missing) > 1 else u"", _ids_text(missing), left)
                else:
                    status = u"{0}; {1}".format(p["error"] or u"posts not recorded", left)
            else:
                new = BR.frame_from_posts(p["start"], p["end"])
                old = BR.frame_from_record(rec)
                if not BR.frame_changed(old, new):
                    status = u"layout updated in pyMEP" if BR.layout_outdated(rec) else u"unchanged"
                elif BR.rigid_motion(old, new) is not None:
                    status = u"posts moved"
                else:
                    status = u"posts moved / levels changed"
        except Exception as ex:
            status = u"cannot read: {0}".format(ex)
        rows.append({"name": name, "posts": _ids_text(ids),
                     "members": len(rec.get("members", {})), "updated": rec.get("updated", u""),
                     "status": status, "missing": missing, "checked": not missing})
    return rows


# ---------------------------------------------------------------------------
# picking the four posts
# ---------------------------------------------------------------------------
POST_CATEGORIES = ("Structural Framing", "Structural Columns", "Columns")


def is_post_candidate(el):
    try:
        return u"{0}".format(el.Category.Name) in POST_CATEGORIES
    except Exception:
        return False


def pair_posts(els):
    """Four elements as (start pair, end pair): the two nearest each other
    first. ValueError when one does not stand up."""
    infos = [post_info(e) for e in els]
    best = None
    for i in range(4):
        for j in range(i + 1, 4):
            d = (infos[i]["x"] - infos[j]["x"]) ** 2 + (infos[i]["y"] - infos[j]["y"]) ** 2
            if best is None or d < best[0]:
                best = (d, i, j)
    a = [els[best[1]], els[best[2]]]
    b = [els[k] for k in range(4) if k not in (best[1], best[2])]
    return a, b


def preselected_posts(doc, uidoc):
    """(start pair, end pair) from four posts selected before the click,
    else None."""
    try:
        sel = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
        sel = [e for e in sel if e is not None and is_post_candidate(e)]
        if len(sel) == 4:
            return pair_posts(sel)
    except Exception:
        pass
    return None


def pick_pair(doc, uidoc, which, alert):
    """Two posts picked in the model for the start or end, or None when
    cancelled."""
    clr.AddReference("RevitAPIUI")
    from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

    class PostFilter(ISelectionFilter):
        def AllowElement(self, el):
            return is_post_candidate(el)

        def AllowReference(self, ref, pt):
            return False
    while True:
        try:
            refs = uidoc.Selection.PickObjects(
                ObjectType.Element, PostFilter(),
                "Pick the TWO posts at the {0} (one each side), then Finish".format(which))
        except Exception:
            return None
        els = [doc.GetElement(r.ElementId) for r in refs]
        if len(els) == 2:
            return els
        alert(u"Pick exactly two posts for the {0} - you picked {1}.".format(which, len(els)))


def orient_like(rec, start, end):
    """(start, end) post pairs turned to run the way the recorded bridge
    runs: when the new end pair is behind the new start pair along the
    bridge's recorded direction they swap, so the design's start and end
    (typed levels, chord pieces, member keys) stay at the same ends."""
    import pymep_bridge as BR
    if not rec or not rec.get("frame"):
        return start, end
    try:
        old = BR.frame_from_record(rec)
        a = [post_info(e) for e in start]
        b = [post_info(e) for e in end]
        ax, ay = (a[0]["x"] + a[1]["x"]) / 2.0, (a[0]["y"] + a[1]["y"]) / 2.0
        bx, by = (b[0]["x"] + b[1]["x"]) / 2.0, (b[0]["y"] + b[1]["y"]) / 2.0
        if (bx - ax) * old["u"][0] + (by - ay) * old["u"][1] < 0:
            return end, start
    except Exception:
        pass
    return start, end


def pick_posts(doc, uidoc, alert, use_selection=True):
    """(start pair, end pair) of post elements: four selected before the
    click, else picked pair by pair. None when cancelled; ValueError when
    a post is at both ends."""
    pre = preselected_posts(doc, uidoc) if use_selection else None
    if pre is not None:
        return pre
    start = pick_pair(doc, uidoc, "START", alert)
    if start is None:
        return None
    end = pick_pair(doc, uidoc, "END", alert)
    if end is None:
        return None
    if set(id_value(e.Id) for e in start) & set(id_value(e.Id) for e in end):
        raise ValueError(u"A post was picked at both ends.")
    return start, end


def registry_path(doc):
    return os.path.join(get_export_folder(doc), "bridges.json")


def project_position(doc):
    """{"elev_m": shared elevation of the internal origin, in metres}."""
    try:
        pos = doc.ActiveProjectLocation.GetProjectPosition(XYZ.Zero)
        return {"elev_m": ft_to_m(pos.Elevation)}
    except Exception:
        return {"elev_m": 0.0}


# ---------------------------------------------------------------------------
# 1. The posts: vertical framing or columns picked in the model
# ---------------------------------------------------------------------------
def element_label(el):
    try:
        sym = getattr(el, "Symbol", None)
        if sym is not None:
            return u"{0} : {1}".format(sym.FamilyName, type_name(sym))
    except Exception:
        pass
    try:
        if isinstance(el, FamilySymbol):
            return u"{0} : {1}".format(el.FamilyName, type_name(el))
    except Exception:
        pass
    n = type_name(el)
    return n or u"element"


_SIZE_PARAMS = ("b", "B", "d", "D", "h", "H", "Width", "Depth", "Height", "Size", "bf")


_DEPTH_PARAMS = ("d", "D", "h", "H", "Depth", "Height", "Section Depth", "Nominal Depth", "ht")


def section_depth_m(symbol):
    """A framing type's section depth in metres from its type parameters
    (d, D, h, Height, Depth ...), 0 when none is found."""
    if symbol is None:
        return 0.0
    for name in _DEPTH_PARAMS:
        try:
            prm = symbol.LookupParameter(name)
            if prm is not None and prm.HasValue and u"{0}".format(prm.StorageType).endswith("Double"):
                v = ft_to_m(prm.AsDouble())
                if 0.02 < v < 2.0:
                    return v
        except Exception:
            continue
    return 0.0


def post_size_m(el):
    """The post's plan size in metres: the largest of its type's section
    parameters (b, d, h, Width, Depth ...), else its bounding box's
    smaller plan extent, else 0.2."""
    best = 0.0
    try:
        typ = el.Document.GetElement(el.GetTypeId())
    except Exception:
        typ = None
    for name in _SIZE_PARAMS:
        for holder in (typ, el):
            if holder is None:
                continue
            try:
                prm = holder.LookupParameter(name)
                if prm is not None and prm.HasValue and u"{0}".format(prm.StorageType).endswith("Double"):
                    v = ft_to_m(prm.AsDouble())
                    if 0.02 < v < 2.0:
                        best = max(best, v)
            except Exception:
                continue
    if best > 0:
        return best
    try:
        bb = el.get_BoundingBox(None)
        if bb is not None:
            v = min(bb.Max.X - bb.Min.X, bb.Max.Y - bb.Min.Y)
            if v > 0:
                return ft_to_m(v)
    except Exception:
        pass
    return 0.2


def _solid_points(el):
    """Every point of the element's solid geometry edges, in model feet
    (XYZ) - [] when Revit gives none."""
    from Autodesk.Revit.DB import GeometryInstance, Options, Solid
    out = []
    try:
        opt = Options()
        geo = el.get_Geometry(opt)
    except Exception:
        geo = None
    if geo is None:
        return out

    def walk(g, depth=0):
        for obj in g:
            if isinstance(obj, Solid):
                try:
                    if obj.Volume <= 1e-9:
                        continue
                    for edge in obj.Edges:
                        for p in edge.Tessellate():
                            out.append(p)
                except Exception:
                    continue
            elif isinstance(obj, GeometryInstance) and depth < 3:
                try:
                    walk(obj.GetInstanceGeometry(), depth + 1)
                except Exception:
                    continue
    try:
        walk(geo)
    except Exception:
        pass
    return out


def post_section(el, z_lo_ft, z_hi_ft):
    """The post's shaft in plan: (x, y) metres of the edges of its solid
    geometry that pass through its middle half (so a base plate, a cap
    or a bracket at the ends does not count), or None when that is not
    a section with some size both ways (a round post's seams, no
    geometry)."""
    pts = _solid_points(el)
    if not pts:
        return None
    h = z_hi_ft - z_lo_ft
    band_lo, band_hi = z_lo_ft + 0.25 * h, z_hi_ft - 0.25 * h
    # a plan position the shaft runs through has geometry both below and
    # above the middle half (a vertical edge's two ends, a round post's
    # bottom and top rims); a plate or bracket at one height does not
    by_xy = {}
    for p in pts:
        key = (round(p.X, 4), round(p.Y, 4))       # grouping only; the point keeps its own x, y
        lo, hi, x, y = by_xy.get(key, (p.Z, p.Z, p.X, p.Y))
        by_xy[key] = (min(lo, p.Z), max(hi, p.Z), x, y)
    sec = [(ft_to_m(x), ft_to_m(y)) for lo, hi, x, y in by_xy.values()
           if lo <= band_lo and hi >= band_hi]
    if len(sec) < 3:
        return None
    xs = [x for x, y in sec]
    ys = [y for x, y in sec]
    if max(xs) - min(xs) < 0.02 or max(ys) - min(ys) < 0.02:
        return None
    return sec


def post_info(el):
    """{"id", "x", "y", "z_bottom", "z_top", "size_m", "label",
    "section"} for a vertical framing member or column. x, y is the
    centre of its measured shaft section when its geometry gives one
    (a framing post's location line can sit off the steel with its
    justification), else its location; "section" is that section's plan
    points (None when not measured). ValueError when the element does
    not stand up."""
    pts = None
    try:
        crv = el.Location.Curve
        a, b = crv.GetEndPoint(0), crv.GetEndPoint(1)
        pts = (a, b)
    except Exception:
        pts = None
    if pts is None:
        bb = el.get_BoundingBox(None)
        if bb is None:
            raise ValueError(u"{0} has no position".format(element_label(el)))
        cx, cy = (bb.Min.X + bb.Max.X) * 0.5, (bb.Min.Y + bb.Max.Y) * 0.5
        pts = (XYZ(cx, cy, bb.Min.Z), XYZ(cx, cy, bb.Max.Z))
    a, b = pts
    if abs(a.Z - b.Z) < 1e-6:
        raise ValueError(u"{0} is not vertical - pick the posts, not a beam".format(element_label(el)))
    lo, hi = (a, b) if a.Z < b.Z else (b, a)
    if math.hypot(hi.X - lo.X, hi.Y - lo.Y) > abs(hi.Z - lo.Z) * 0.3:
        raise ValueError(u"{0} leans too far to be a post".format(element_label(el)))
    x, y = ft_to_m(lo.X), ft_to_m(lo.Y)
    sec = post_section(el, lo.Z, hi.Z)
    if sec:
        xs = [p[0] for p in sec]
        ys = [p[1] for p in sec]
        x, y = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    return {"id": id_value(el.Id), "x": x, "y": y,
            "z_bottom": ft_to_m(lo.Z), "z_top": ft_to_m(hi.Z), "size_m": post_size_m(el),
            "label": element_label(el), "section": sec}


def into_posts(el, frame, tol_m=0.005):
    """How far (metres) the element's steel reaches into any of the four
    posts past their inner faces along the span, judged from its solid
    geometry in the bridge's plan frame; 0 when it stays clear or Revit
    gives no geometry."""
    pts = _solid_points(el)
    if not pts:
        return 0.0
    ox, oy = frame["origin"]
    ux, uy = frame["u"]
    vx, vy = frame["v"]
    half = frame["post_size"] / 2.0
    half_t = BR.post_half_t(frame)
    span = frame["span"]
    worst = 0.0
    for p in pts:
        x, y = ft_to_m(p.X) - ox, ft_to_m(p.Y) - oy
        s, t = x * ux + y * uy, x * vx + y * vy
        for tc in (frame["tL"], frame["tR"]):
            if abs(t - tc) >= half_t - tol_m:
                continue
            if -half < s < half:
                worst = max(worst, half - s)          # into a start post past its inner face
            if span - half < s < span + half:
                worst = max(worst, s - (span - half))  # into an end post
    return worst if worst > tol_m else 0.0


def posts_from_ids(doc, ids):
    out = []
    for i in ids:
        el = doc.GetElement(make_id(int(i)))
        if el is None:
            raise ValueError(u"post {0} is no longer in the model".format(i))
        out.append(post_info(el))
    return out


# ---------------------------------------------------------------------------
# 2. What the builder page can choose from
# ---------------------------------------------------------------------------
def framing_types(doc):
    out = []
    for sym in (FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_StructuralFraming)
                .OfClass(FamilySymbol)):
        try:
            out.append({"id": u"{0}".format(id_value(sym.Id)),
                        "name": u"{0} : {1}".format(sym.FamilyName, type_name(sym)),
                        "depth_m": section_depth_m(sym)})
        except Exception:
            continue
    out.sort(key=lambda t: t["name"].lower())
    return out


def floor_types(doc):
    out = []
    for ft in FilteredElementCollector(doc).OfClass(FloorType):
        try:
            if ft.IsFoundationSlab:
                continue
        except Exception:
            pass
        try:
            out.append({"id": u"{0}".format(id_value(ft.Id)), "name": type_name(ft),
                        "thickness_m": floor_thickness_m(ft)})
        except Exception:
            continue
    out.sort(key=lambda t: t["name"].lower())
    return out


def stiffener_types(doc):
    """Structural stiffener family types, then structural framing types
    (a stiffener may be modelled as a small beam)."""
    out = []
    for bic in (BuiltInCategory.OST_StructuralStiffener,):
        try:
            for sym in FilteredElementCollector(doc).OfCategory(bic).OfClass(FamilySymbol):
                out.append({"id": u"{0}".format(id_value(sym.Id)),
                            "name": u"{0} : {1}".format(sym.FamilyName, type_name(sym))})
        except Exception:
            continue
    out.sort(key=lambda t: t["name"].lower())
    return out


def railing_types(doc):
    out = []
    try:
        for rt in FilteredElementCollector(doc).OfClass(RailingType):
            out.append({"id": u"{0}".format(id_value(rt.Id)), "name": type_name(rt)})
    except Exception:
        pass
    out.sort(key=lambda t: t["name"].lower())
    return out


def floor_thickness_m(floor_type):
    """The floor type's total thickness in metres (0 when unknown)."""
    try:
        cs = floor_type.GetCompoundStructure()
        if cs is not None:
            return ft_to_m(cs.GetWidth())
    except Exception:
        pass
    try:
        p = floor_type.get_Parameter(BuiltInParameter.FLOOR_ATTR_DEFAULT_THICKNESS_PARAM)
        if p is not None and p.HasValue:
            return ft_to_m(p.AsDouble())
    except Exception:
        pass
    return 0.0


def levels(doc):
    out = []
    for lv in FilteredElementCollector(doc).OfClass(Level):
        try:
            out.append({"id": u"{0}".format(id_value(lv.Id)), "name": type_name(lv) or u"{0}".format(lv.Name),
                        "elevation_m": ft_to_m(getattr(lv, "ProjectElevation", lv.Elevation))})
        except Exception:
            continue
    out.sort(key=lambda l: l["elevation_m"])
    return out


def by_id(doc, text_id):
    try:
        return doc.GetElement(make_id(int(text_id)))
    except Exception:
        return None


def first_of(items, wanted_id, doc):
    """The element for wanted_id when it is in items (list of dicts with
    id), else the first item's element, else None."""
    ids = [it["id"] for it in items]
    pick = wanted_id if wanted_id in ids else (ids[0] if ids else None)
    return by_id(doc, pick) if pick is not None else None


# ---------------------------------------------------------------------------
# 3. Making and moving the elements
# ---------------------------------------------------------------------------
def activate(symbol, doc):
    try:
        if not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()
    except Exception:
        pass


def _xyz(p):
    return XYZ(m_to_ft(p[0]), m_to_ft(p[1]), m_to_ft(p[2]))


def member_line(m):
    return Line.CreateBound(_xyz(m["p1"]), _xyz(m["p2"]))


def create_member(doc, m, symbol, level):
    """A structural framing instance along the member's line; cross beams
    and bracing get their TOP on the line (z justification Top) so they
    sit under the floor."""
    stype = StructuralType.Brace if m["stype"] == "brace" else StructuralType.Beam
    el = doc.Create.NewFamilyInstance(member_line(m), symbol, level, stype)
    if m.get("zjust") == "top":
        set_top_justified(el)
    if m.get("roll"):
        set_roll(doc, el, m["roll"], m.get("up"))
    if m.get("free_ends"):
        # Revit may already have joined the end to a post on placing it:
        # unjoin it, then put the line back on its node
        free_ends(el, m)
        try:
            el.Location.Curve = member_line(m)
        except Exception:
            pass
    apply_end_cuts(el, m)
    return el


CUT_ROLES = ("diagonal", "bracing")
EXTENSION_PARAMS = (("START_EXTENSION", u"Start Extension"), ("END_EXTENSION", u"End Extension"))


def _extension_param(el, end):
    bip_name, label = EXTENSION_PARAMS[end]
    p = None
    bip = getattr(BuiltInParameter, bip_name, None)
    if bip is not None:
        try:
            p = el.get_Parameter(bip)
        except Exception:
            p = None
    if p is None:
        try:
            p = el.LookupParameter(label)
        except Exception:
            p = None
    return p


def cut_member(m, ends=None):
    """m with its line pulled back by its end cuts (only the ends listed,
    when given) - the line Revit gets when it has no extension to set."""
    cuts = [c for c in (m.get("end_cuts") or []) if ends is None or c[0] in ends]
    if not cuts:
        return m
    c = dict(m)
    c["end_cuts"] = cuts
    c["p1"], c["p2"] = BR.cut_points(c)
    del c["end_cuts"]
    return c


def apply_end_cuts(el, m):
    """An end diagonal or plan brace on a post keeps its line on the node
    and stops short of the post: a negative start / end extension of the
    cut length (other ends of these members go back to 0, so a diagonal
    that is no longer at the end loses its cut). When Revit has no
    extension to set, the line itself is pulled back instead (the angle
    is the same). Returns the member whose line the element now has."""
    if m.get("role") not in CUT_ROLES:
        return m
    cuts = dict((int(e), f) for e, f in (m.get("end_cuts") or []))
    length_m = math.sqrt(sum((a - b) ** 2 for a, b in zip(m["p1"], m["p2"])))
    fallback = []
    for end in (0, 1):
        cut_m = cuts.get(end, 0.0) * length_m
        want = -m_to_ft(cut_m)
        p = _extension_param(el, end)
        done = False
        if p is not None and not p.IsReadOnly:
            try:
                done = abs(p.AsDouble() - want) <= 1e-6 or p.Set(want) is not False
            except Exception:
                done = False
        if not done and cut_m > 0.0:
            fallback.append(end)
    if fallback:
        c = cut_member(m, fallback)
        try:
            el.Location.Curve = member_line(c)
            return c
        except Exception:
            pass
    return m


def line_matches(el, m, tol_m=0.001):
    """True when the member's location line runs between m's two points
    (either way round), within tol_m metres."""
    try:
        crv = el.Location.Curve
        a, b = crv.GetEndPoint(0), crv.GetEndPoint(1)
    except Exception:
        return True
    tol = m_to_ft(tol_m)

    def near(p, q):
        return abs(p.X - q.X) <= tol and abs(p.Y - q.Y) <= tol and abs(p.Z - q.Z) <= tol
    # the full line, or - where Revit had no extension to set - the line
    # pulled back by the end cuts
    for c in (m, cut_member(m, [0]), cut_member(m, [1]), cut_member(m)):
        want = [_xyz(c["p1"]), _xyz(c["p2"])]
        if (near(a, want[0]) and near(b, want[1])) or (near(a, want[1]) and near(b, want[0])):
            return True
    return False


def retype(el, typ):
    """Change the element's type in place (its id stays); True when it
    changed."""
    try:
        if typ is not None and id_value(el.GetTypeId()) != id_value(typ.Id):
            el.ChangeTypeId(typ.Id)
            return True
    except Exception:
        pass
    return False


def update_hosted(doc, el, m, stiffener_sym, railing_type, level):
    """Keep a deck stiffener or railing, putting it on its new line and
    re-typing it in place; False when Revit will not (the caller then
    makes it again)."""
    try:
        if m["role"] == "stiffener":
            if stiffener_sym is None:
                return False
            el.Location.Curve = member_line(m)
            retype(el, stiffener_sym)
            settle_under(doc, el, m)
            return True
        if m["role"] == "railing":
            if railing_type is None:
                return False
            lz = level.Elevation
            loop = CurveLoop()
            loop.Append(Line.CreateBound(XYZ(m_to_ft(m["p1"][0]), m_to_ft(m["p1"][1]), lz),
                                         XYZ(m_to_ft(m["p2"][0]), m_to_ft(m["p2"][1]), lz)))
            el.SetPath(loop)
            retype(el, railing_type)
            return True
    except Exception:
        return False
    return False


def set_joins(el, ends, allowed):
    """Allow or disallow Revit's automatic join at the given member ends
    (0 start, 1 end). Returns how many ends changed."""
    try:
        from Autodesk.Revit.DB.Structure import StructuralFramingUtils
    except Exception:
        return 0
    n = 0
    for e in ends:
        try:
            now = StructuralFramingUtils.IsJoinAllowedAtEnd(el, int(e))
            if allowed and not now:
                StructuralFramingUtils.AllowJoinAtEnd(el, int(e))
                n += 1
            elif not allowed and now:
                StructuralFramingUtils.DisallowJoinAtEnd(el, int(e))
                n += 1
        except Exception:
            pass
    return n


def free_ends(el, m):
    """Disallow Revit's automatic join at the member ends listed in
    m["free_ends"] (0 start, 1 end) - the chord ends at the posts - so
    a join does not extend the member into the post past its cutback."""
    ends = m.get("free_ends") or []
    if not ends:
        return
    try:
        from Autodesk.Revit.DB.Structure import StructuralFramingUtils
    except Exception:
        return
    for e in ends:
        try:
            if StructuralFramingUtils.IsJoinAllowedAtEnd(el, int(e)):
                StructuralFramingUtils.DisallowJoinAtEnd(el, int(e))
        except Exception:
            pass


def set_roll(doc, el, angle, up=None):
    """Cross-Section Rotation about the member's own axis (radians) so a
    cross beam's top lies on the sloping floor underside. The angle is
    worked out with the right-hand rule about the member's start-to-end
    axis; the instance transform is read back and the sign flipped when
    the section came out tilted the wrong way (farther from the floor
    normal than an unrotated section would be)."""
    try:
        p = el.get_Parameter(BuiltInParameter.STRUCTURAL_BEND_DIR_ANGLE)
        if p is None or p.IsReadOnly:
            return False
        p.Set(float(angle))
        if up is not None and abs(angle) > 1e-9:
            try:
                doc.Regenerate()
                bz = el.GetTransform().BasisZ
                want = XYZ(float(up[0]), float(up[1]), float(up[2])).Normalize()
                if bz.DotProduct(want) < math.cos(angle) - 1e-6:
                    p.Set(-float(angle))
            except Exception:
                pass
        return True
    except Exception:
        return False


def rise_above_line(doc, el, m):
    """How far the member's top of steel sits above its line (metres):
    0 for a top justified beam, half the depth for one on its centre."""
    try:
        doc.Regenerate()
        bb = el.get_BoundingBox(None)
        if bb is None:
            return 0.0
        top = max(m_to_ft(m["p1"][2]), m_to_ft(m["p2"][2]))
        r = ft_to_m(bb.Max.Z - top)
        return r if 0.0 <= r < 0.6 else 0.0
    except Exception:
        return 0.0


def set_post_tops(doc, ids, z_top_m):
    """Lengthen or shorten vertical posts so their tops are at z_top_m
    (model metres): framing by its location curve, columns by their top
    offset. Returns how many were set."""
    done = 0
    z = m_to_ft(z_top_m)
    for i in ids:
        el = doc.GetElement(make_id(int(i)))
        if el is None:
            continue
        try:
            loc = el.Location
            crv = loc.Curve
            a, b = crv.GetEndPoint(0), crv.GetEndPoint(1)
            if a.Z >= b.Z:
                loc.Curve = Line.CreateBound(XYZ(a.X, a.Y, z), b)
            else:
                loc.Curve = Line.CreateBound(a, XYZ(b.X, b.Y, z))
            done += 1
            continue
        except Exception:
            pass
        try:
            p_lvl = el.get_Parameter(BuiltInParameter.SCHEDULE_TOP_LEVEL_PARAM)
            p_off = el.get_Parameter(BuiltInParameter.SCHEDULE_TOP_LEVEL_OFFSET_PARAM)
            lvl = doc.GetElement(p_lvl.AsElementId())
            p_off.Set(z - lvl.Elevation)
            done += 1
        except Exception:
            pass
    return done


def set_top_justified(el):
    try:
        p = el.get_Parameter(BuiltInParameter.Z_JUSTIFICATION)
        if p is not None and not p.IsReadOnly:
            p.Set(0)                # 0 Top, 1 Center, 2 Origin, 3 Bottom
    except Exception:
        pass


def bottom_face(doc, floor):
    """A reference to the floor's underside, or None."""
    try:
        refs = HostObjectUtils.GetBottomFaces(floor)
        for r in refs:
            return r
    except Exception:
        pass
    return None


def create_stiffener(doc, m, symbol, floor, level):
    """A deck stiffener along the member's line under the floor: hosted
    on the floor's underside when the family is face based (line based
    or point based), else a small beam with its top on the line."""
    line = member_line(m)
    face = bottom_face(doc, floor) if floor is not None else None
    attempts = []
    if face is not None:
        attempts.append(lambda: doc.Create.NewFamilyInstance(face, line, symbol))
        mid = XYZ((line.GetEndPoint(0).X + line.GetEndPoint(1).X) / 2.0,
                  (line.GetEndPoint(0).Y + line.GetEndPoint(1).Y) / 2.0,
                  (line.GetEndPoint(0).Z + line.GetEndPoint(1).Z) / 2.0)
        direction = (line.GetEndPoint(1) - line.GetEndPoint(0)).Normalize()
        attempts.append(lambda: doc.Create.NewFamilyInstance(face, mid, direction, symbol))

    def as_beam():
        el = doc.Create.NewFamilyInstance(line, symbol, level, StructuralType.NonStructural)
        set_top_justified(el)
        return el
    attempts.append(as_beam)
    last = None
    for make in attempts:
        try:
            el = make()
            if el is not None:
                settle_under(doc, el, m)
                return el
        except Exception as ex:
            last = ex
    raise RuntimeError(u"{0}".format(last) if last is not None else u"no stiffener made")


def settle_under(doc, el, m):
    """Make sure a stiffener's geometry hangs below its line (the floor
    underside). A family modelled on the wrong side of its reference
    comes out above the line - through a thin plate deck; a work-plane
    based instance is flipped to the other side of its face, anything
    else is moved down so its top sits on the line. Returns a note when
    it could not be settled."""
    try:
        doc.Regenerate()
        bb = el.get_BoundingBox(None)
    except Exception:
        return u""
    if bb is None:
        return u""
    top_line = max(m_to_ft(m["p1"][2]), m_to_ft(m["p2"][2]))
    lo_line = min(m_to_ft(m["p1"][2]), m_to_ft(m["p2"][2]))
    tol = m_to_ft(0.003)

    def above():
        b = el.get_BoundingBox(None)
        return None if b is None else ((b.Min.Z + b.Max.Z) / 2.0 > (top_line + lo_line) / 2.0 + tol, b)
    state = above()
    if state is None or not state[0]:
        return u""
    try:
        if el.CanFlipWorkPlane:
            el.IsWorkPlaneFlipped = not el.IsWorkPlaneFlipped
            doc.Regenerate()
            state = above()
            if state is None or not state[0]:
                return u""
    except Exception:
        pass
    try:
        dz = top_line - state[1].Max.Z
        ElementTransformUtils.MoveElement(doc, el.Id, XYZ(0, 0, dz))
        doc.Regenerate()
        state = above()
        if state is None or not state[0]:
            return u""
    except Exception:
        pass
    return u"stiffener family sits above its line - model it below its reference plane or use a face based family"


def is_hosted_on(el, host):
    """True when the railing (or other hosted element) reports host as
    its host."""
    try:
        return host is not None and id_value(el.HostId) == id_value(host.Id)
    except Exception:
        return False


def create_railing(doc, m, railing_type, floor, level):
    """A railing along the member's line hosted on the deck floor: made at
    the level, then given the floor as host and checked; if Revit will
    not take it, once more with the path the other way round, and failing
    that left on the level with its base at the deck top."""
    lz = level.Elevation
    a = XYZ(m_to_ft(m["p1"][0]), m_to_ft(m["p1"][1]), lz)
    b = XYZ(m_to_ft(m["p2"][0]), m_to_ft(m["p2"][1]), lz)
    rail = None
    hosted = False
    for p, q in ((a, b), (b, a)):
        loop = CurveLoop()
        loop.Append(Line.CreateBound(p, q))
        if rail is not None:
            try:
                doc.Delete(rail.Id)
            except Exception:
                pass
        rail = Railing.Create(doc, loop, railing_type.Id, level.Id)
        if floor is None:
            break
        try:
            rail.HostId = floor.Id
            doc.Regenerate()
        except Exception:
            pass
        hosted = is_hosted_on(rail, floor)
        if hosted:
            break
    if not hosted:
        try:
            p = rail.get_Parameter(BuiltInParameter.STAIRS_RAILING_BASE_LEVEL_PARAM)
        except Exception:
            p = None
        try:
            off = rail.get_Parameter(BuiltInParameter.STAIRS_RAILING_HEIGHT_OFFSET)
            if off is not None and not off.IsReadOnly:
                off.Set(m_to_ft(m["p1"][2]) - lz)
        except Exception:
            pass
    return rail


def set_member_curve(el, m):
    """Move an existing member onto the member's new line; False when
    the element has no location curve."""
    free_ends(el, m)              # unjoined first, so the line lands on its node
    try:
        loc = el.Location
        loc.Curve = member_line(m)
    except Exception:
        return False
    if "roll" in m:
        try:
            set_roll(el.Document, el, m["roll"], m.get("up"))
        except Exception:
            pass
    free_ends(el, m)
    apply_end_cuts(el, m)
    return True


def create_deck(doc, deck, floor_type, level):
    """The sloped deck floor: a rectangle at the level with a slope arrow
    from the start station to the end station, then lifted so its top
    at the tail sits at the deck top. Revit 2022+ uses Floor.Create,
    older builds NewSlopedFloor."""
    lz = level.Elevation
    pts = [XYZ(m_to_ft(x), m_to_ft(y), lz) for x, y in deck["corners"]]
    tail = XYZ(m_to_ft(deck["tail"][0]), m_to_ft(deck["tail"][1]), lz)
    head = XYZ(m_to_ft(deck["head"][0]), m_to_ft(deck["head"][1]), lz)
    arrow = Line.CreateBound(tail, head)
    slope = float(deck["slope"])
    floor = None
    try:
        from Autodesk.Revit.DB import CurveLoop, Floor
        loop = CurveLoop()
        for i in range(4):
            loop.Append(Line.CreateBound(pts[i], pts[(i + 1) % 4]))
        loops = List[CurveLoop]()
        loops.Add(loop)
        floor = Floor.Create(doc, loops, floor_type.Id, level.Id, True, arrow, slope)
    except Exception:
        floor = None
    if floor is None:
        ca = CurveArray()
        for i in range(4):
            ca.Append(Line.CreateBound(pts[i], pts[(i + 1) % 4]))
        floor = doc.Create.NewSlopedFloor(ca, floor_type, level, True, arrow, slope)
    # the sketch sits at the level; lift the floor so its top at the tail
    # is the deck top there
    try:
        p = floor.get_Parameter(BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
        if p is not None and not p.IsReadOnly:
            p.Set(m_to_ft(deck["tail_top_z"]) - lz)
    except Exception:
        pass
    return floor


def move_elements(doc, ids, dx_m, dy_m, dz_m, dangle, about_m):
    """Translate and rotate (about a vertical axis through about_m) the
    elements with these ids."""
    col = List[ElementId]()
    for i in ids:
        col.Add(make_id(i))
    if col.Count == 0:
        return
    if abs(dangle) > 1e-9:
        axis = Line.CreateBound(XYZ(m_to_ft(about_m[0]), m_to_ft(about_m[1]), 0),
                                XYZ(m_to_ft(about_m[0]), m_to_ft(about_m[1]), 10))
        ElementTransformUtils.RotateElements(doc, col, axis, dangle)
    if abs(dx_m) > 1e-9 or abs(dy_m) > 1e-9 or abs(dz_m) > 1e-9:
        ElementTransformUtils.MoveElements(doc, col, XYZ(m_to_ft(dx_m), m_to_ft(dy_m), m_to_ft(dz_m)))


def delete_quiet(doc, ids):
    done = 0
    for i in ids:
        try:
            if doc.GetElement(make_id(i)) is not None:
                doc.Delete(make_id(i))
                done += 1
        except Exception:
            continue
    return done


def set_mark_comments(el, text):
    """Tag a bridge member with the bridge name in Comments, so the
    model shows which bridge it belongs to."""
    try:
        p = el.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if p is not None and not p.IsReadOnly:
            p.Set(u"{0}".format(text))
    except Exception:
        pass
