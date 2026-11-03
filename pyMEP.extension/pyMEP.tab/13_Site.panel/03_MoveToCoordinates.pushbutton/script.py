# -*- coding: utf-8 -*-
"""Move to Coordinates - move the selected elements to typed x, y
coordinates: families, filled regions, annotations, pipes, anything
Revit lets move.

Each element is measured at a reference point - the location point of
a family instance, the start of a curve-located element, else the
centre of its bounding box - and that point is what lands on the typed
coordinates. One typed line moves the whole selection together, the
highlighted element landing on it; one line per element pairs them in
list order; lines starting with a Mark move the element with that Mark.
An optional z is the new offset above the element's level; without it
the height stays. View-specific elements (regions, annotations, detail
items) move only in x, y.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

__title__ = "Move to\nCoordinates"
__author__ = "Glent Group"

import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
from Autodesk.Revit.DB import (BuiltInParameter, ElementId, ElementTransformUtils,
                               Level, Transaction, XYZ)
from Autodesk.Revit.UI.Selection import ObjectType
from System.Collections.Generic import List

from pyrevit import revit, forms, script

import pymep_place_coords as PC
from pymep_config import load_settings, save_settings
from pymep_revit import id_value, make_id, quiet

output = script.get_output()
doc = revit.doc
uidoc = revit.uidoc

XAML_PATH = os.path.join(os.path.dirname(os.path.abspath(PC.__file__)),
                         "pymep_move_coords.xaml")
FT_TO_M = 0.3048
S_SYSTEM = "place_coords_system"
S_UNITS = "place_coords_units"


# ---------------------------------------------------------------------------
# 1. The elements and where they are
# ---------------------------------------------------------------------------
selected = []
try:
    selected = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    selected = [e for e in selected if e is not None]
except Exception:
    selected = []
if not selected:
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element, "Pick the elements to move, then Finish")
        selected = [doc.GetElement(r.ElementId) for r in refs]
        selected = [e for e in selected if e is not None]
    except Exception:
        script.exit()
if not selected:
    script.exit()

position = {"east": 0.0, "north": 0.0, "elev": 0.0, "angle": 0.0,
            "pbp_x": 0.0, "pbp_y": 0.0, "pbp_z": 0.0}
notes = []
try:
    pos = doc.ActiveProjectLocation.GetProjectPosition(XYZ.Zero)
    position["east"] = float(pos.EastWest)
    position["north"] = float(pos.NorthSouth)
    position["elev"] = float(pos.Elevation)
    position["angle"] = float(pos.Angle)
except Exception:
    notes.append(u"The project position could not be read - shared coordinates "
                 u"equal internal ones here.")
try:
    from Autodesk.Revit.DB import BasePoint
    bp = BasePoint.GetProjectBasePoint(doc)          # 2020.2+
    position["pbp_x"] = float(bp.Position.X)
    position["pbp_y"] = float(bp.Position.Y)
    position["pbp_z"] = float(bp.Position.Z)
except Exception:
    notes.append(u"The project base point's position cannot be read in this "
                 u"Revit - treated as the internal origin.")


def _mark(el):
    try:
        p = el.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        if p is not None and p.HasValue:
            return u"{0}".format(p.AsString() or u"").strip()
    except Exception:
        pass
    return u""


def _label(el):
    bits = []
    try:
        if el.Category is not None:
            bits.append(u"{0}".format(el.Category.Name))
    except Exception:
        pass
    try:
        sym = getattr(el, "Symbol", None)
        if sym is not None:
            bits.append(u"{0} : {1}".format(sym.FamilyName, el.Name))
        else:
            bits.append(u"{0}".format(el.Name))
    except Exception:
        pass
    return u" - ".join(b for b in bits if b)


def _level_z(el):
    lvl = None
    try:
        lid = el.LevelId
        if lid is not None and id_value(lid) > 0:
            lvl = doc.GetElement(lid)
    except Exception:
        lvl = None
    if lvl is None:
        for bip in ("FAMILY_LEVEL_PARAM", "INSTANCE_REFERENCE_LEVEL_PARAM",
                    "SCHEDULE_LEVEL_PARAM", "RBS_START_LEVEL_PARAM"):
            try:
                p = el.get_Parameter(getattr(BuiltInParameter, bip))
                if p is not None and p.HasValue and id_value(p.AsElementId()) > 0:
                    cand = doc.GetElement(p.AsElementId())
                    if isinstance(cand, Level):
                        lvl = cand
                        break
            except Exception:
                continue
    if lvl is None:
        return None
    try:
        return float(getattr(lvl, "ProjectElevation", lvl.Elevation))
    except Exception:
        return float(lvl.Elevation)


def _reference(el):
    """(point name, XYZ) the element is measured at."""
    try:
        loc = el.Location
    except Exception:
        loc = None
    pt = getattr(loc, "Point", None) if loc is not None else None
    if pt is not None:
        return u"location", pt
    crv = getattr(loc, "Curve", None) if loc is not None else None
    if crv is not None:
        try:
            return u"start", crv.GetEndPoint(0)
        except Exception:
            pass
    try:
        bb = el.get_BoundingBox(doc.ActiveView) or el.get_BoundingBox(None)
        if bb is not None:
            return u"centre", XYZ((bb.Min.X + bb.Max.X) * 0.5, (bb.Min.Y + bb.Max.Y) * 0.5,
                                  (bb.Min.Z + bb.Max.Z) * 0.5)
    except Exception:
        pass
    return None, None


elements = []
skipped = []
for el in selected:
    pname, p = _reference(el)
    if p is None:
        skipped.append(el)
        continue
    try:
        flat = bool(el.ViewSpecific)
    except Exception:
        flat = False
    elements.append({"id": id_value(el.Id), "name": _mark(el), "label": _label(el),
                     "point": pname, "x": float(p.X), "y": float(p.Y), "z": float(p.Z),
                     "level_z": None if flat else _level_z(el), "flat": flat,
                     "pinned": bool(getattr(el, "Pinned", False))})
if not elements:
    forms.alert("None of the selected elements has a position to move from.",
                exitscript=True)
elements.sort(key=lambda e: (e["name"] == u"", e["name"].lower(), e["id"]))
settings = load_settings()


# ---------------------------------------------------------------------------
# 2. The dialog
# ---------------------------------------------------------------------------
class MoveWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self._ready = False
        self._sync = 0            # > 0 while the list is being refilled by code
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
        self.TxtInfo.Text = u"{0} element(s) selected{1}{2}".format(
            len(elements),
            u"; {0} without a position were left out".format(len(skipped)) if skipped else u"",
            (u". " + u" ".join(notes)) if notes else u".")
        self._ready = True
        self._fill_list()
        self.LstElements.SelectedIndex = 0
        self._refresh()

    def system(self):
        idx = self.CmbSystem.SelectedIndex
        return PC.SYSTEMS[idx][0] if 0 <= idx < len(PC.SYSTEMS) else "internal"

    def units(self):
        idx = self.CmbUnits.SelectedIndex
        return PC.UNITS[idx][0] if 0 <= idx < len(PC.UNITS) else "m"

    def _fill_list(self):
        keep = self.LstElements.SelectedIndex
        self._sync += 1
        try:
            self.LstElements.Items.Clear()
            unit = self.units()
            for e in elements:
                X, Y, Z = PC.coords_in(self.system(), e["x"], e["y"], e["z"], **position)
                self.LstElements.Items.Add(u"{0}  {1}  ({2})  {3}, {4}{5}{6}".format(
                    (e["name"] or u"-").ljust(10)[:10], e["label"], e["point"],
                    PC._fmt(X, unit), PC._fmt(Y, unit),
                    u"" if e["flat"] else u", {0}".format(PC._fmt(Z, unit)),
                    u"  [pinned]" if e["pinned"] else u""))
            if 0 <= keep < len(elements):
                self.LstElements.SelectedIndex = keep
        finally:
            self._sync -= 1

    def plan(self):
        pts, errs = PC.parse_points(self.TxtPoints.Text)
        if not pts:
            return None, (u"Type where to move to - x, y  or  x, y, z." if not errs else
                          PC.summary_text(pts, errs, self.units()))
        targets = PC.prepare(pts, self.units(), 0.0, self.system(), **dict(
            (k, v) for k, v in position.items() if k in ("east", "north", "angle",
                                                         "pbp_x", "pbp_y")))
        try:
            plan = PC.plan_moves(elements, targets, reference=max(0, self.LstElements.SelectedIndex))
        except ValueError as ex:
            return None, u"{0}".format(ex)
        if errs:
            plan["warnings"].append(PC.summary_text([], errs, self.units()))
        return plan, u""

    def _refresh(self):
        if not getattr(self, "_ready", False):
            return
        plan, err = self.plan()
        if plan is None:
            self.TxtPlan.Text = err
            self.BtnMove.IsEnabled = False
            return
        self.TxtPlan.Text = PC.moves_text(plan, self.system(), self.units(), **position)
        self.BtnMove.IsEnabled = bool(plan["moves"])

    def on_changed(self, sender, args):
        if not getattr(self, "_ready", False) or self._sync:
            return
        if u"{0}".format(getattr(sender, "Name", u"")) in ("CmbSystem", "CmbUnits"):
            self._fill_list()
        self._refresh()

    def on_move(self, sender, args):
        plan, err = self.plan()
        if plan is None or not plan["moves"]:
            self.StatusText.Text = err or u"Nothing to move."
            return
        self.result = {"plan": plan, "system": self.system(), "units": self.units(),
                       "unpin": bool(self.ChkUnpin.IsChecked)}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


win = MoveWindow()
win.ShowDialog()
if not win.result:
    script.exit()
res = win.result
plan = res["plan"]
try:
    settings[S_SYSTEM] = res["system"]
    settings[S_UNITS] = res["units"]
    save_settings(settings)
except Exception:
    pass

# ---------------------------------------------------------------------------
# 3. Move - one transaction
# ---------------------------------------------------------------------------
done = []
failed = []
t = Transaction(doc, "pyMEP: Move {0} element(s) to coordinates".format(len(plan["moves"])))
quiet(t)
t.Start()
try:
    for m in plan["moves"]:
        try:
            el = doc.GetElement(make_id(m["id"]))
            if el is None:
                failed.append((m, u"element not found"))
                continue
            if getattr(el, "Pinned", False):
                if res["unpin"]:
                    el.Pinned = False
                else:
                    failed.append((m, u"pinned - tick 'unpin pinned elements' or unpin it first"))
                    continue
            if m["dist"] < 1e-9:
                done.append((m, u"already there"))
                continue
            ElementTransformUtils.MoveElement(doc, el.Id, XYZ(m["dx"], m["dy"], m["dz"]))
            done.append((m, u""))
        except Exception as ex:
            failed.append((m, u"{0}".format(ex)))
    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert("Failed, nothing moved:\n\n{0}".format(ex), exitscript=True)

# ---------------------------------------------------------------------------
# 4. Report
# ---------------------------------------------------------------------------
try:
    ids = List[ElementId]()
    for m, _n in done:
        ids.Add(make_id(m["id"]))
    if ids.Count:
        uidoc.Selection.SetElementIds(ids)
except Exception:
    pass

output.print_md("### Move to Coordinates")
output.print_md("**Moved:** {0}  |  **Failed:** {1}  |  **Left alone:** {2}  |  **Pairing:** {3}".format(
    len(done), len(failed), len(plan["stay"]), plan["mode"]))
if done:
    output.print_table(
        table_data=[[output.linkify(make_id(m["id"])), m["name"], m["label"],
                     u"{0:.3f}, {1:.3f}, {2:.3f}".format(m["from"][0] * FT_TO_M, m["from"][1] * FT_TO_M, m["from"][2] * FT_TO_M),
                     u"{0:.3f}, {1:.3f}, {2:.3f}".format(m["to"][0] * FT_TO_M, m["to"][1] * FT_TO_M, m["to"][2] * FT_TO_M),
                     u"{0:.3f}".format(m["dist"] * FT_TO_M), note]
                    for m, note in done],
        title="Moved ({0})".format(len(done)),
        columns=["Element", "Mark", "What", "From (internal m)", "To (internal m)", "Distance (m)", "Note"])
if failed:
    output.print_table(
        table_data=[[output.linkify(make_id(m["id"])), m["name"], m["label"], why] for m, why in failed],
        title="Failed ({0})".format(len(failed)),
        columns=["Element", "Mark", "What", "Why"])
for w in plan["warnings"]:
    output.print_md(u"- {0}".format(w))

forms.alert(u"{0} element(s) moved{1}{2}.\n\nThey stay selected; one undo step puts them back.".format(
    len(done),
    u", {0} FAILED (see the output window)".format(len(failed)) if failed else u"",
    u", {0} left alone".format(len(plan["stay"])) if plan["stay"] else u""),
    title="Move to Coordinates")
