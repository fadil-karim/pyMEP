# -*- coding: utf-8 -*-
"""Element Coordinates - the coordinates of the selected elements, in
the coordinate system and unit of your choice, ready to copy.

For each element: the location point of a family instance (or any
point-located element), the start and end of a curve-located element
(pipes, walls, lines), else the centre of its bounding box. Shown as
internal model coordinates, relative to the project base point, or as
shared easting / northing / elevation, in metres, millimetres or feet,
with the element's level and its offset above it. 'Copy table' puts a
tab-separated table on the clipboard; 'Copy as x, y lines' gives name,
x, y per element in the format Place at Coordinates reads.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

__title__ = "Element\nCoordinates"
__author__ = "Glent Group"

import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
from Autodesk.Revit.DB import BuiltInParameter, Level, XYZ
from Autodesk.Revit.UI.Selection import ObjectType

from pyrevit import revit, forms, script

import pymep_place_coords as PC
from pymep_config import load_settings, save_settings
from pymep_revit import id_value, make_id

output = script.get_output()
doc = revit.doc
uidoc = revit.uidoc

XAML_PATH = os.path.join(os.path.dirname(os.path.abspath(PC.__file__)),
                         "pymep_element_coords.xaml")
FT_TO_M = 0.3048
S_SYSTEM = "place_coords_system"
S_UNITS = "place_coords_units"


# ---------------------------------------------------------------------------
# 1. The elements
# ---------------------------------------------------------------------------
elements = []
try:
    elements = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    elements = [e for e in elements if e is not None]
except Exception:
    elements = []
if not elements:
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element, "Pick the elements to read, then Finish")
        elements = [doc.GetElement(r.ElementId) for r in refs]
        elements = [e for e in elements if e is not None]
    except Exception:
        script.exit()
if not elements:
    script.exit()


# ---------------------------------------------------------------------------
# 2. Where the model sits: project position and base point
# ---------------------------------------------------------------------------
position = {"east": 0.0, "north": 0.0, "elev": 0.0, "angle": 0.0,
            "pbp_x": 0.0, "pbp_y": 0.0, "pbp_z": 0.0}
notes = []
try:
    pos = doc.ActiveProjectLocation.GetProjectPosition(XYZ.Zero)
    position["east"] = float(pos.EastWest)
    position["north"] = float(pos.NorthSouth)
    position["elev"] = float(pos.Elevation)
    position["angle"] = float(pos.Angle)
    try:
        notes.append(u"Shared coordinates follow the active project location "
                     u"'{0}'.".format(doc.ActiveProjectLocation.Name))
    except Exception:
        pass
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


# ---------------------------------------------------------------------------
# 3. One or two rows per element
# ---------------------------------------------------------------------------
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


def _level(el, z):
    """'Level name (+offset)' for the element's level, offset in feet
    returned separately; blank when it has none."""
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
                    lvl = doc.GetElement(p.AsElementId())
                    if isinstance(lvl, Level):
                        break
                    lvl = None
            except Exception:
                continue
    if lvl is None:
        return u"", None
    try:
        lz = float(getattr(lvl, "ProjectElevation", lvl.Elevation))
    except Exception:
        lz = float(lvl.Elevation)
    return u"{0}".format(lvl.Name), z - lz


def _points(el):
    """[(point name, XYZ)] for the element."""
    try:
        loc = el.Location
    except Exception:
        loc = None
    pt = getattr(loc, "Point", None) if loc is not None else None
    if pt is not None:
        return [(u"location", pt)]
    crv = getattr(loc, "Curve", None) if loc is not None else None
    if crv is not None:
        try:
            return [(u"start", crv.GetEndPoint(0)), (u"end", crv.GetEndPoint(1))]
        except Exception:
            pass
    try:
        bb = el.get_BoundingBox(None)
        if bb is not None:
            return [(u"bbox centre", XYZ((bb.Min.X + bb.Max.X) * 0.5,
                                         (bb.Min.Y + bb.Max.Y) * 0.5,
                                         (bb.Min.Z + bb.Max.Z) * 0.5))]
    except Exception:
        pass
    return []


rows = []
skipped = []
for el in elements:
    pts = _points(el)
    if not pts:
        skipped.append(el)
        continue
    name = _mark(el)
    label = _label(el)
    for pname, p in pts:
        level_name, off = _level(el, p.Z)
        rows.append({"id": id_value(el.Id), "name": name, "point": pname,
                     "x": float(p.X), "y": float(p.Y), "z": float(p.Z),
                     "label": label, "level_name": level_name, "offset": off,
                     "level": level_name})
if not rows:
    forms.alert("None of the selected elements has a location point, a curve "
                "or a bounding box to read.", exitscript=True)

settings = load_settings()


# ---------------------------------------------------------------------------
# 4. The dialog
# ---------------------------------------------------------------------------
def copy_text(text):
    try:
        clr.AddReference("PresentationCore")
        from System.Windows import Clipboard
        Clipboard.SetText(text)
        return True
    except Exception:
        pass
    try:
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import Clipboard as FormsClipboard
        FormsClipboard.SetText(text)
        return True
    except Exception:
        return False


class CoordsWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self._ready = False
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
        self.TxtInfo.Text = u"{0} element(s), {1} point(s).{2}{3}".format(
            len(elements) - len(skipped), len(rows),
            u" {0} element(s) without a readable position were skipped.".format(
                len(skipped)) if skipped else u"",
            (u" " + u" ".join(notes)) if notes else u"")
        self._ready = True
        self._show()

    def system(self):
        idx = self.CmbSystem.SelectedIndex
        return PC.SYSTEMS[idx][0] if 0 <= idx < len(PC.SYSTEMS) else "internal"

    def units(self):
        idx = self.CmbUnits.SelectedIndex
        return PC.UNITS[idx][0] if 0 <= idx < len(PC.UNITS) else "m"

    def _rows(self):
        unit = self.units()
        out = []
        for r in rows:
            r2 = dict(r)
            if r["level_name"] and r["offset"] is not None:
                r2["level"] = u"{0} ({1}{2})".format(
                    r["level_name"], u"+" if r["offset"] >= 0 else u"",
                    PC._fmt(r["offset"], unit))
            out.append(r2)
        return out

    def table(self):
        return PC.coords_table(self._rows(), self.system(), self.units(), **position)

    def _show(self):
        if not getattr(self, "_ready", False):
            return
        self.TxtCoords.Text = self.table().replace(u"\t", u"    ")
        self.StatusText.Text = u""

    def on_changed(self, sender, args):
        self._show()

    def on_copy(self, sender, args):
        ok = copy_text(self.table())
        self.StatusText.Text = (u"Table copied - paste into a spreadsheet." if ok
                                else u"Could not reach the clipboard.")

    def on_copy_lines(self, sender, args):
        text = PC.place_lines(self._rows(), self.system(), self.units(), **position)
        ok = copy_text(text)
        self.StatusText.Text = (u"{0} line(s) copied - paste into Place at Coordinates "
                                u"with '{1}' and {2} chosen there.".format(
                                    len(self._rows()), dict(PC.SYSTEMS)[self.system()],
                                    dict(PC.UNITS)[self.units()]) if ok
                                else u"Could not reach the clipboard.")

    def on_close(self, sender, args):
        try:
            settings[S_SYSTEM] = self.system()
            settings[S_UNITS] = self.units()
            save_settings(settings)
        except Exception:
            pass
        self.Close()


win = CoordsWindow()
win.ShowDialog()

# ---------------------------------------------------------------------------
# 5. The output window keeps a copy, all three systems in metres
# ---------------------------------------------------------------------------
output.print_md("### Element Coordinates ({0} point(s), metres)".format(len(rows)))
table = []
for r in rows:
    ix, iy, iz = PC.coords_in("internal", r["x"], r["y"], r["z"])
    px, py, pz = PC.coords_in("pbp", r["x"], r["y"], r["z"], **position)
    sx, sy, sz = PC.coords_in("shared", r["x"], r["y"], r["z"], **position)
    table.append([output.linkify(make_id(r["id"])), r["name"], r["point"],
                  r["label"],
                  u"{0} ({1}{2:.3f})".format(r["level_name"], u"+" if r["offset"] >= 0 else u"",
                                             r["offset"] * FT_TO_M) if r["level_name"] else u"",
                  u"{0:.3f}, {1:.3f}, {2:.3f}".format(ix * FT_TO_M, iy * FT_TO_M, iz * FT_TO_M),
                  u"{0:.3f}, {1:.3f}, {2:.3f}".format(px * FT_TO_M, py * FT_TO_M, pz * FT_TO_M),
                  u"{0:.3f}, {1:.3f}, {2:.3f}".format(sx * FT_TO_M, sy * FT_TO_M, sz * FT_TO_M)])
output.print_table(table_data=table, columns=[
    "Element", "Mark", "Point", "What", "Level (offset m)", "Internal x, y, z",
    "Project base point x, y, z", "Shared E, N, elevation"])
if notes:
    output.print_md(u" ".join(notes))
