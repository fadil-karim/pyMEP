# -*- coding: utf-8 -*-
"""Bridge Builder - the Revit side shared by the three Bridges buttons:
reading the two end points and their deck levels, the model's types and
levels, and making, moving and re-curving the deck floor and the
framing members.

IronPython 2.7 only (needs the Revit API): .format() only, LF endings.
"""

import math
import os

import clr
clr.AddReference("RevitAPI")
import System
from Autodesk.Revit.DB import (BuiltInCategory, BuiltInParameter, CurveArray,
                               ElementId, ElementTransformUtils, FamilySymbol,
                               FilteredElementCollector, FloorType, Level, Line, XYZ)
from Autodesk.Revit.DB.Structure import StructuralType
from System.Collections.Generic import List

import pymep_bridge as BR
import pymep_csv_params as CP
from pymep_config import get_export_folder
from pymep_revit import id_value, make_id

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
# 1. The end points: generic model types, instances, levels
# ---------------------------------------------------------------------------
def element_label(el):
    try:
        sym = getattr(el, "Symbol", None)
        if sym is not None:
            return u"{0} : {1}".format(sym.FamilyName, el.Name)
    except Exception:
        pass
    try:
        return u"{0}".format(el.Name)
    except Exception:
        return u"element"


def generic_symbols(doc):
    """[{"id", "label", "family", "type", "count"}] - every generic model
    type, with how many instances it has, most used first."""
    out = []
    counts = {}
    for el in (FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_GenericModel)
               .WhereElementIsNotElementType()):
        try:
            tid = id_value(el.GetTypeId())
        except Exception:
            continue
        counts[tid] = counts.get(tid, 0) + 1
    for sym in (FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_GenericModel)
                .OfClass(FamilySymbol)):
        try:
            sid = id_value(sym.Id)
            out.append({"id": sid, "family": u"{0}".format(sym.FamilyName),
                        "type": u"{0}".format(sym.Name),
                        "label": u"{0} : {1}".format(sym.FamilyName, sym.Name),
                        "count": counts.get(sid, 0)})
        except Exception:
            continue
    out.sort(key=lambda s: (-s["count"], s["label"].lower()))
    return out


def instances_of(doc, symbol_id):
    out = []
    for el in (FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_GenericModel)
               .WhereElementIsNotElementType()):
        try:
            if id_value(el.GetTypeId()) == symbol_id:
                out.append(el)
        except Exception:
            continue
    return out


def param_names(elements):
    """Sorted names of the instance parameters with a value on any of
    the elements (text, number, whole number)."""
    names = set()
    for el in elements[:50]:
        try:
            for p in el.Parameters:
                try:
                    st = u"{0}".format(p.StorageType).split(".")[-1]
                    if st in ("String", "Double", "Integer") and p.Definition.Name:
                        names.add(u"{0}".format(p.Definition.Name))
                except Exception:
                    continue
        except Exception:
            continue
    return sorted(names, key=lambda n: n.lower())


def point_m(el):
    """(x, y, z) of the instance's location point in metres, else the
    centre of its box."""
    try:
        pt = el.Location.Point
        if pt is not None:
            return (ft_to_m(pt.X), ft_to_m(pt.Y), ft_to_m(pt.Z))
    except Exception:
        pass
    bb = el.get_BoundingBox(None)
    if bb is None:
        raise ValueError(u"{0} has no position".format(element_label(el)))
    return (ft_to_m((bb.Min.X + bb.Max.X) * 0.5), ft_to_m((bb.Min.Y + bb.Max.Y) * 0.5),
            ft_to_m((bb.Min.Z + bb.Max.Z) * 0.5))


def _is_length(p):
    try:
        d = p.Definition
        for getter in ("GetDataType", "GetSpecTypeId"):
            fn = getattr(d, getter, None)
            if fn is not None:
                sp = fn()
                if sp is not None and sp.TypeId:
                    return "spec.length" in sp.TypeId
        return str(d.ParameterType) == "Length"
    except Exception:
        return False


def _level_z_m(doc, el):
    lvl = None
    try:
        lid = el.LevelId
        if lid is not None and id_value(lid) > 0:
            lvl = doc.GetElement(lid)
    except Exception:
        lvl = None
    if lvl is None:
        try:
            p = el.get_Parameter(BuiltInParameter.FAMILY_LEVEL_PARAM)
            if p is not None and p.HasValue:
                lvl = doc.GetElement(p.AsElementId())
        except Exception:
            lvl = None
    if lvl is None:
        return None
    try:
        return ft_to_m(getattr(lvl, "ProjectElevation", lvl.Elevation))
    except Exception:
        return ft_to_m(lvl.Elevation)


def read_level_m(doc, el, param_name, mode, position):
    """The deck level at this end as an internal elevation in metres.

    param_name blank: the instance's own location z. Else the parameter:
    a length reads in its stored units, a plain number or text as
    metres. mode: 'shared' (value is a shared / survey elevation),
    'internal' (value is an internal elevation) or 'offset' (value is
    the height above the instance's level). Raises ValueError with a
    plain reason."""
    if not param_name:
        return point_m(el)[2]
    try:
        prm = el.LookupParameter(param_name)
    except Exception:
        prm = None
    if prm is None:
        raise ValueError(u"{0} has no parameter '{1}'".format(element_label(el), param_name))
    if not prm.HasValue:
        raise ValueError(u"'{0}' is empty on {1}".format(param_name, element_label(el)))
    st = u"{0}".format(prm.StorageType).split(".")[-1]
    if st == "Double":
        raw = prm.AsDouble()
        value_m = ft_to_m(raw) if _is_length(prm) else float(raw)
    elif st == "Integer":
        value_m = float(prm.AsInteger())
    else:
        n = CP.to_number(prm.AsString())
        if n is None:
            raise ValueError(u"'{0}' on {1} is '{2}', not a number".format(
                param_name, element_label(el), prm.AsString()))
        value_m = float(n)
    if mode == "shared":
        return value_m - position.get("elev_m", 0.0)
    if mode == "offset":
        lz = _level_z_m(doc, el)
        if lz is None:
            raise ValueError(u"{0} has no level to offset from".format(element_label(el)))
        return lz + value_m
    return value_m


def end_point(doc, el, param_name, mode, position):
    """(x_m, y_m, z_deck_m) for one end."""
    x, y, _z = point_m(el)
    return (x, y, read_level_m(doc, el, param_name, mode, position))


# ---------------------------------------------------------------------------
# 2. What the builder page can choose from
# ---------------------------------------------------------------------------
def framing_types(doc):
    out = []
    for sym in (FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_StructuralFraming)
                .OfClass(FamilySymbol)):
        try:
            out.append({"id": u"{0}".format(id_value(sym.Id)),
                        "name": u"{0} : {1}".format(sym.FamilyName, sym.Name)})
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
            out.append({"id": u"{0}".format(id_value(ft.Id)), "name": u"{0}".format(ft.Name)})
        except Exception:
            continue
    out.sort(key=lambda t: t["name"].lower())
    return out


def levels(doc):
    out = []
    for lv in FilteredElementCollector(doc).OfClass(Level):
        try:
            out.append({"id": u"{0}".format(id_value(lv.Id)), "name": u"{0}".format(lv.Name),
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
    """A structural framing instance along the member's line."""
    stype = StructuralType.Brace if m["stype"] == "brace" else StructuralType.Beam
    return doc.Create.NewFamilyInstance(member_line(m), symbol, level, stype)


def set_member_curve(el, m):
    """Move an existing member onto the member's new line; False when
    the element has no location curve."""
    try:
        loc = el.Location
        loc.Curve = member_line(m)
        return True
    except Exception:
        return False


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
