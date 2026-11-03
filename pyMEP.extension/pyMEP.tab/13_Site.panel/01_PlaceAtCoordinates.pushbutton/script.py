# -*- coding: utf-8 -*-
"""Place at Coordinates - place a family type at typed x, y points on a
level, with an optional z offset above the level.

The dialog asks for the family type (searched by any words of the
family or type name, within a category), the level, what the typed
coordinates are relative to (internal origin, project base point, or
shared easting / northing), their unit, and the points themselves - one
per line as x, y or x, y, z. z is the offset above the level and 0
when left out; an extra word on a line becomes the instance's Mark.

Every point becomes one instance, placed in one transaction, by the
family's placement type: level based families through the level
overload (the point's z gives the offset), work-plane based families
on the level's plane with the offset written afterwards, columns at
the level with the base offset set, wall / floor / ceiling / roof
hosted families on the nearest host at the point, view based (2D)
families in the active view, line based families and beams along a
line from the point (a typed x1, y1, x2, y2, else 1 m east), adaptive
components with their placement points spread along that line.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

__title__ = "Place at\nCoordinates"
__author__ = "Glent Group"

import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (BuiltInCategory, BuiltInParameter, ElementId,
                               FamilySymbol, FilteredElementCollector, Floor,
                               Level, Line, RoofBase, StorageType, Transaction,
                               Wall, XYZ)
from Autodesk.Revit.DB import AdaptiveComponentInstanceUtils
from Autodesk.Revit.DB.Structure import StructuralType
from System.Collections.Generic import List

from pyrevit import revit, forms, script

import pymep_place_coords as PC
from pymep_config import load_settings, save_settings
from pymep_revit import id_value, make_id, quiet

output = script.get_output()
doc = revit.doc
uidoc = revit.uidoc

XAML_PATH = os.path.join(os.path.dirname(os.path.abspath(PC.__file__)),
                         "pymep_place_coords.xaml")
FT_TO_M = 0.3048
LIST_CAP = 400
POINT_TYPES = ("OneLevelBased", "TwoLevelsBased", "WorkPlaneBased",
               "OneLevelBasedHosted")
LINE_TYPES = ("CurveBased", "CurveBasedDetail", "CurveDrivenStructural")
HOST_NAMES = {1: u"wall", 2: u"floor", 3: u"ceiling", 4: u"roof", 5: u"face"}
HOST_REACH_FT = 1.0 / 0.3048            # a wall within 1 m of the point
HOST_REACH_Z_FT = 5.0 / 0.3048          # a floor / roof within 5 m vertically
TAGS = {"ViewBased": u"2D - goes in the active view",
        "CurveBased": u"line based - x1, y1, x2, y2 or 1 m east",
        "CurveBasedDetail": u"2D line based - in the active view",
        "CurveDrivenStructural": u"beam / brace - x1, y1, x2, y2 or 1 m east",
        "Adaptive": u"adaptive - points spread along the line",
        "TwoLevelsBased": u"column",
        "WorkPlaneBased": u"work plane based"}

S_CATEGORY = "place_coords_category"
S_FAMILY = "place_coords_family"
S_LEVEL = "place_coords_level"
S_SYSTEM = "place_coords_system"
S_UNITS = "place_coords_units"


# ---------------------------------------------------------------------------
# 1. What the model offers: point-placed family types, levels, position
# ---------------------------------------------------------------------------
def hosting_behaviour(fam):
    """1 wall, 2 floor, 3 ceiling, 4 roof, 5 face, 0 none."""
    try:
        prm = fam.get_Parameter(BuiltInParameter.FAMILY_HOSTING_BEHAVIOR)
        return int(prm.AsInteger()) if prm is not None else 0
    except Exception:
        return 0


def tag_for(placement, host):
    if placement == "OneLevelBasedHosted":
        return u"needs a {0} at the point".format(HOST_NAMES.get(host, u"host"))
    return TAGS.get(placement, u"")


entries = []
for sym in FilteredElementCollector(doc).OfClass(FamilySymbol):
    try:
        fam = sym.Family
        if fam is None or fam.IsInPlace:
            continue
        placement = u"{0}".format(fam.FamilyPlacementType)
        host = hosting_behaviour(fam) if placement == "OneLevelBasedHosted" else 0
        cat = sym.Category
        cat_name = u"{0}".format(cat.Name) if cat is not None else u"(no category)"
        entries.append({"id": id_value(sym.Id),
                        "label": PC.type_label(fam.Name, sym.Name),
                        "category": cat_name, "placement": placement, "host": host,
                        "tag": tag_for(placement, host),
                        "bic": id_value(cat.Id) if cat is not None else 0})
    except Exception:
        continue
entries.sort(key=lambda e: (e["category"].lower(), e["label"].lower()))
if not entries:
    forms.alert("No family types are loaded in this model.", exitscript=True)

view2d = None
try:
    _vt = u"{0}".format(doc.ActiveView.ViewType)
    if _vt not in ("ThreeD", "Schedule", "ProjectBrowser", "SystemBrowser",
                   "Report", "CostReport", "Walkthrough", "Rendering", "Internal",
                   "DrawingSheet", "Undefined"):
        view2d = doc.ActiveView
except Exception:
    view2d = None
categories = sorted(set(e["category"] for e in entries), key=lambda s: s.lower())

levels = sorted(FilteredElementCollector(doc).OfClass(Level),
                key=lambda l: l.Elevation)
if not levels:
    forms.alert("The model has no levels.", exitscript=True)


def level_z(level):
    """The level's internal z (ProjectElevation, falling back to
    Elevation on versions without it)."""
    try:
        return float(level.ProjectElevation)
    except Exception:
        return float(level.Elevation)


level_labels = [u"{0}  ({1:+.3f} m)".format(l.Name, l.Elevation * FT_TO_M)
                for l in levels]
active_level = None
try:
    gl = doc.ActiveView.GenLevel
    active_level = u"{0}".format(gl.Name) if gl is not None else None
except Exception:
    active_level = None

position = {"east": 0.0, "north": 0.0, "angle": 0.0, "pbp_x": 0.0, "pbp_y": 0.0}
pbp_known = False
try:
    pos = doc.ActiveProjectLocation.GetProjectPosition(XYZ.Zero)
    position["east"] = float(pos.EastWest)
    position["north"] = float(pos.NorthSouth)
    position["angle"] = float(pos.Angle)
except Exception:
    pass
try:
    from Autodesk.Revit.DB import BasePoint
    bp = BasePoint.GetProjectBasePoint(doc)          # 2020.2+
    position["pbp_x"] = float(bp.Position.X)
    position["pbp_y"] = float(bp.Position.Y)
    pbp_known = True
except Exception:
    pbp_known = False

settings = load_settings()


# ---------------------------------------------------------------------------
# 2. Placing one instance
# ---------------------------------------------------------------------------
def structural_type(bic):
    try:
        if bic == int(BuiltInCategory.OST_StructuralFoundation):
            return StructuralType.Footing
        if bic == int(BuiltInCategory.OST_StructuralColumns):
            return StructuralType.Column
        if bic == int(BuiltInCategory.OST_StructuralFraming):
            return StructuralType.Beam
    except Exception:
        pass
    return StructuralType.NonStructural


OFFSET_PARAMS = ("INSTANCE_FREE_HOST_OFFSET_PARAM", "INSTANCE_ELEVATION_PARAM",
                 "FAMILY_BASE_LEVEL_OFFSET_PARAM")

_hosts = {}


def host_candidates(kind):
    if kind in _hosts:
        return _hosts[kind]
    try:
        if kind == 1:
            els = list(FilteredElementCollector(doc).OfClass(Wall))
        elif kind == 2:
            els = list(FilteredElementCollector(doc).OfClass(Floor))
        elif kind == 3:
            els = list(FilteredElementCollector(doc).OfCategory(
                BuiltInCategory.OST_Ceilings).WhereElementIsNotElementType())
        elif kind == 4:
            els = list(FilteredElementCollector(doc).OfClass(RoofBase))
        else:
            els = []
    except Exception:
        els = []
    _hosts[kind] = els
    return els


def find_host(kind, p):
    """The wall nearest the point (within 1 m), or the floor / ceiling /
    roof under or over it (within 5 m vertically), else None."""
    best = None
    best_d = None
    for el in host_candidates(kind):
        try:
            if kind == 1:
                crv = el.Location.Curve
                res = crv.Project(XYZ(p.X, p.Y, crv.GetEndPoint(0).Z))
                if res is None:
                    continue
                d = res.Distance
                if d > HOST_REACH_FT:
                    continue
            else:
                bb = el.get_BoundingBox(None)
                if bb is None:
                    continue
                if not (bb.Min.X - 0.1 <= p.X <= bb.Max.X + 0.1
                        and bb.Min.Y - 0.1 <= p.Y <= bb.Max.Y + 0.1):
                    continue
                d = 0.0 if bb.Min.Z <= p.Z <= bb.Max.Z else min(
                    abs(p.Z - bb.Min.Z), abs(p.Z - bb.Max.Z))
                if d > HOST_REACH_Z_FT:
                    continue
        except Exception:
            continue
        if best_d is None or d < best_d:
            best, best_d = el, d
    return best


def _host_name(host):
    try:
        return u"{0} {1}".format(host.Category.Name, id_value(host.Id))
    except Exception:
        return u"host"


def place_one(sym, entry, level, pt, stype):
    """The new instance, and a note on how it got there - by the
    family's placement type."""
    placement = entry["placement"]
    p = XYZ(pt["x"], pt["y"], pt["z"])
    attempts = []
    if placement == "ViewBased":
        if view2d is None:
            raise RuntimeError(u"a 2D family goes in a view - open a plan, section "
                               u"or drafting view and run again")
        attempts.append((u"in view '{0}'".format(view2d.Name),
                         lambda: doc.Create.NewFamilyInstance(p, sym, view2d)))
    elif placement in LINE_TYPES:
        ex, ey = PC.line_end(pt)
        line = Line.CreateBound(p, XYZ(ex, ey, pt["z"]))
        how = u"along a line to {0:.3f}, {1:.3f}{2}".format(
            ex * FT_TO_M, ey * FT_TO_M, u"" if pt.get("has_end") else u" (1 m east)")
        if placement == "CurveBasedDetail":
            if view2d is None:
                raise RuntimeError(u"a 2D line based family goes in a view - open a "
                                   u"plan, section or drafting view and run again")
            attempts.append((how + u" in the active view",
                             lambda: doc.Create.NewFamilyInstance(line, sym, view2d)))
        elif placement == "CurveDrivenStructural":
            st = stype if stype in (StructuralType.Beam, StructuralType.Brace) \
                else StructuralType.Beam
            attempts.append((how + u" as a beam",
                             lambda: doc.Create.NewFamilyInstance(line, sym, level, st)))
        else:
            attempts.append((how + u" on the level",
                             lambda: doc.Create.NewFamilyInstance(line, sym, level, stype)))
            if view2d is not None:
                attempts.append((how + u" in the active view",
                                 lambda: doc.Create.NewFamilyInstance(line, sym, view2d)))
    elif placement == "Adaptive":
        def adaptive():
            inst = AdaptiveComponentInstanceUtils.CreateAdaptiveComponentInstance(doc, sym)
            ids = list(AdaptiveComponentInstanceUtils.GetInstancePlacementPointElementRefIds(inst))
            pts = PC.spread_points(pt, len(ids))
            for rid, (x, y) in zip(ids, pts):
                rp = doc.GetElement(rid)
                rp.Position = XYZ(x, y, pt["z"])
            return inst
        attempts.append((u"as an adaptive component", adaptive))
    elif placement == "OneLevelBasedHosted":
        host = find_host(entry["host"], p)
        if host is None:
            raise RuntimeError(u"needs a {0} at the point - none within reach".format(
                HOST_NAMES.get(entry["host"], u"host")))
        hn = _host_name(host)
        attempts.append((u"hosted on {0}".format(hn),
                         lambda: doc.Create.NewFamilyInstance(p, sym, host, level, stype)))
        attempts.append((u"hosted on {0}, no level".format(hn),
                         lambda: doc.Create.NewFamilyInstance(p, sym, host, stype)))
    else:
        if placement == "WorkPlaneBased":
            attempts.append(("on the level's plane", lambda: doc.Create.NewFamilyInstance(
                level.GetPlaneReference(), p, XYZ.BasisX, sym)))
        attempts.append(("on the level", lambda: doc.Create.NewFamilyInstance(
            p, sym, level, stype)))
        attempts.append(("on the active work plane", lambda: doc.Create.NewFamilyInstance(
            p, sym, stype)))
        if view2d is not None:
            attempts.append((u"in view '{0}'".format(view2d.Name),
                             lambda: doc.Create.NewFamilyInstance(p, sym, view2d)))
    last = None
    for how, make in attempts:
        try:
            inst = make()
            if inst is not None:
                return inst, how
        except Exception as ex:
            last = ex
    raise RuntimeError(u"{0}".format(last) if last is not None else u"no instance made")


def fix_offset(inst, pt):
    """Make sure the instance sits at the point's z: when the placement
    ignored it (columns, work-plane based families), write the offset
    into the instance's offset parameter."""
    try:
        loc = inst.Location.Point
        if loc is not None and abs(loc.Z - pt["z"]) < 1e-6:
            return u""
    except Exception:
        pass
    if abs(pt["offset"]) < 1e-9:
        return u""
    for name in OFFSET_PARAMS:
        bip = getattr(BuiltInParameter, name, None)
        if bip is None:
            continue
        try:
            prm = inst.get_Parameter(bip)
            if (prm is not None and not prm.IsReadOnly
                    and prm.StorageType == StorageType.Double):
                if prm.Set(float(pt["offset"])):
                    return u"offset written to '{0}'".format(prm.Definition.Name)
        except Exception:
            continue
    return u"offset could NOT be set - check the instance"


def set_mark(inst, name):
    if not name:
        return u""
    try:
        prm = inst.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        if prm is not None and not prm.IsReadOnly and prm.Set(u"{0}".format(name)):
            return u""
    except Exception:
        pass
    return u"Mark not set"


# ---------------------------------------------------------------------------
# 3. The dialog
# ---------------------------------------------------------------------------
class PlaceWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self._ready = False
        self.filtered = []
        self.CmbCategory.Items.Add(u"All categories  ({0})".format(len(entries)))
        for c in categories:
            self.CmbCategory.Items.Add(u"{0}  ({1})".format(
                c, len([e for e in entries if e["category"] == c])))
        remembered = settings.get(S_CATEGORY)
        self.CmbCategory.SelectedIndex = (categories.index(remembered) + 1
                                          if remembered in categories else 0)
        for lbl in level_labels:
            self.CmbLevel.Items.Add(lbl)
        names = [u"{0}".format(l.Name) for l in levels]
        want = settings.get(S_LEVEL)
        if want not in names:
            want = active_level
        self.CmbLevel.SelectedIndex = names.index(want) if want in names else 0
        for key, lbl in PC.SYSTEMS:
            self.CmbSystem.Items.Add(lbl)
        keys = [k for k, _ in PC.SYSTEMS]
        want = settings.get(S_SYSTEM) or "internal"
        self.CmbSystem.SelectedIndex = keys.index(want) if want in keys else 0
        for key, lbl in PC.UNITS:
            self.CmbUnits.Items.Add(lbl)
        ukeys = [k for k, _ in PC.UNITS]
        want = settings.get(S_UNITS) or "m"
        self.CmbUnits.SelectedIndex = ukeys.index(want) if want in ukeys else 0
        self._ready = True
        self._filter(prefer=settings.get(S_FAMILY))
        self._points_info()

    # -- choices ------------------------------------------------------------------
    def category(self):
        idx = self.CmbCategory.SelectedIndex
        return categories[idx - 1] if 0 < idx <= len(categories) else None

    def family(self):
        idx = self.LstFamilies.SelectedIndex
        return self.filtered[idx] if 0 <= idx < len(self.filtered) else None

    def level(self):
        idx = self.CmbLevel.SelectedIndex
        return levels[idx] if 0 <= idx < len(levels) else levels[0]

    def system(self):
        idx = self.CmbSystem.SelectedIndex
        return PC.SYSTEMS[idx][0] if 0 <= idx < len(PC.SYSTEMS) else "internal"

    def units(self):
        idx = self.CmbUnits.SelectedIndex
        return PC.UNITS[idx][0] if 0 <= idx < len(PC.UNITS) else "m"

    def points(self):
        return PC.parse_points(self.TxtPoints.Text)

    # -- display ------------------------------------------------------------------
    def _filter(self, prefer=None):
        if not getattr(self, "_ready", False):
            return
        keep = prefer or (self.family() or {}).get("label")
        self.filtered = PC.search(entries, self.TxtSearch.Text, self.category())
        self.LstFamilies.Items.Clear()
        for e in self.filtered[:LIST_CAP]:
            text = e["label"] if self.category() else u"{0}    [{1}]".format(
                e["label"], e["category"])
            if e["tag"]:
                text = u"{0}    - {1}".format(text, e["tag"])
            self.LstFamilies.Items.Add(text)
        labels = [e["label"] for e in self.filtered[:LIST_CAP]]
        if keep in labels:
            self.LstFamilies.SelectedIndex = labels.index(keep)
        elif len(self.filtered) == 1:
            self.LstFamilies.SelectedIndex = 0
        n = len(self.filtered)
        if n == 0:
            self.TxtFamilyInfo.Text = u"No family type matches - try fewer words or another category."
        elif n > LIST_CAP:
            self.TxtFamilyInfo.Text = u"{0} types match - the first {1} are listed; type more words.".format(n, LIST_CAP)
        else:
            self.TxtFamilyInfo.Text = u"{0} type(s) match.".format(n)

    def _points_info(self):
        if not getattr(self, "_ready", False):
            return
        pts, errs = self.points()
        text = PC.summary_text(pts, errs, self.units())
        if self.system() == "pbp" and not pbp_known:
            text += u" The project base point's position cannot be read in this Revit - treated as the internal origin."
        self.TxtPointsInfo.Text = text

    # -- handlers -------------------------------------------------------------------
    def on_category_changed(self, sender, args):
        self._filter()

    def on_search_changed(self, sender, args):
        self._filter()

    def on_family_changed(self, sender, args):
        self.StatusText.Text = u""

    def on_points_changed(self, sender, args):
        self._points_info()

    def on_place(self, sender, args):
        fam = self.family()
        if fam is None:
            self.StatusText.Text = u"Pick a family type from the list."
            return
        pts, errs = self.points()
        if not pts:
            self.StatusText.Text = (u"Type at least one point: x, y  or  x, y, z."
                                    if not errs else
                                    u"No usable point - {0}".format(PC.summary_text(pts, errs, self.units())))
            return
        self.result = {"family": fam, "level": self.level(), "points": pts,
                       "errors": errs, "system": self.system(),
                       "units": self.units(), "category": self.category()}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


win = PlaceWindow()
win.ShowDialog()
if not win.result:
    script.exit()
res = win.result
try:
    settings[S_CATEGORY] = res["category"] or ""
    settings[S_FAMILY] = res["family"]["label"]
    settings[S_LEVEL] = u"{0}".format(res["level"].Name)
    settings[S_SYSTEM] = res["system"]
    settings[S_UNITS] = res["units"]
    save_settings(settings)
except Exception:
    pass

level = res["level"]
entry = res["family"]
prepared = PC.prepare(res["points"], res["units"], level_z(level), res["system"],
                      **position)
sym = doc.GetElement(make_id(entry["id"]))
if sym is None:
    forms.alert("The family type is no longer in the model.", exitscript=True)
stype = structural_type(entry["bic"])

# ---------------------------------------------------------------------------
# 4. Place - one transaction
# ---------------------------------------------------------------------------
placed = []
failed = []
t = Transaction(doc, "pyMEP: Place {0} at {1} point(s)".format(
    entry["label"], len(prepared)))
quiet(t)
t.Start()
try:
    try:
        if not sym.IsActive:
            sym.Activate()
            doc.Regenerate()
    except Exception:
        pass
    for pt in prepared:
        try:
            inst, how = place_one(sym, entry, level, pt, stype)
            notes = [n for n in ((fix_offset(inst, pt) if entry["placement"] in POINT_TYPES
                                  or entry["placement"] == "Invalid" else u""),
                                 set_mark(inst, pt["name"])) if n]
            placed.append((pt, inst, how, u"; ".join(notes)))
        except Exception as ex:
            failed.append((pt, u"{0}".format(ex)))
    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert("Failed, nothing placed:\n\n{0}".format(ex), exitscript=True)

# ---------------------------------------------------------------------------
# 5. Report
# ---------------------------------------------------------------------------
try:
    ids = List[ElementId]()
    for _pt, inst, _how, _n in placed:
        ids.Add(inst.Id)
    if ids.Count:
        uidoc.Selection.SetElementIds(ids)
except Exception:
    pass

sysname = dict(PC.SYSTEMS).get(res["system"], res["system"])
unitname = dict(PC.UNITS).get(res["units"], res["units"])
output.print_md("### Place at Coordinates")
output.print_md("**Family type:** {0}  |  **Level:** {1}  |  **Coordinates:** {2}, in {3}  |  "
                "**Placed:** {4}  |  **Failed:** {5}  |  **Lines skipped:** {6}".format(
                    entry["label"], level.Name, sysname, unitname, len(placed),
                    len(failed), len(res["errors"])))
if placed:
    output.print_table(
        table_data=[[output.linkify(inst.Id), pt["name"] or "", pt["typed"],
                     "{0:.3f}, {1:.3f}".format(pt["x"] * FT_TO_M, pt["y"] * FT_TO_M),
                     "{0:.3f}".format(pt["offset"] * FT_TO_M), how, note]
                    for pt, inst, how, note in placed],
        title="Placed ({0})".format(len(placed)),
        columns=["Element", "Mark", "Typed", "Internal x, y (m)", "Offset (m)",
                 "How", "Notes"])
if failed:
    output.print_table(
        table_data=[[pt["line"], pt["typed"], why] for pt, why in failed],
        title="Failed ({0})".format(len(failed)),
        columns=["Line", "Typed", "Why"])
if res["errors"]:
    output.print_table(
        table_data=[[n, raw.strip(), why] for n, raw, why in res["errors"]],
        title="Lines skipped ({0})".format(len(res["errors"])),
        columns=["Line", "Text", "Why"])

forms.alert(u"{0} instance(s) of {1} placed on {2}{3}{4}.\n\nThey are selected; one "
            u"undo step removes them.".format(
                len(placed), entry["label"], level.Name,
                u", {0} FAILED (see the output window)".format(len(failed)) if failed else u"",
                u", {0} line(s) skipped".format(len(res["errors"])) if res["errors"] else u""),
            title="Place at Coordinates")
