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


def post_info(el):
    """{"id", "x", "y", "z_bottom", "z_top", "size_m", "label"} for a
    vertical framing member or column. ValueError when the element does
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
    return {"id": id_value(el.Id), "x": ft_to_m(lo.X), "y": ft_to_m(lo.Y),
            "z_bottom": ft_to_m(lo.Z), "z_top": ft_to_m(hi.Z), "size_m": post_size_m(el),
            "label": element_label(el)}


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
                        "name": u"{0} : {1}".format(sym.FamilyName, type_name(sym))})
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
    return el


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


def create_railing(doc, m, railing_type, floor, level):
    """A railing along the member's line, hosted on the deck floor when
    Revit lets it (else left on the level with its base at the deck)."""
    lz = level.Elevation
    a = XYZ(m_to_ft(m["p1"][0]), m_to_ft(m["p1"][1]), lz)
    b = XYZ(m_to_ft(m["p2"][0]), m_to_ft(m["p2"][1]), lz)
    loop = CurveLoop()
    loop.Append(Line.CreateBound(a, b))
    rail = Railing.Create(doc, loop, railing_type.Id, level.Id)
    hosted = False
    if floor is not None:
        try:
            rail.HostId = floor.Id
            hosted = True
        except Exception:
            hosted = False
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
