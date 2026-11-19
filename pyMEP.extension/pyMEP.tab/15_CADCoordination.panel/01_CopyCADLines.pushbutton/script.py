# -*- coding: utf-8 -*-
"""Copy CAD Lines - copy the lines of a linked or imported CAD into Revit
as detail lines or model lines, layer by layer, in a chosen line style.

The CAD instance is the one selected before the click, else the only one
in the active view, else picked. Its geometry is read with every curve's
layer (the graphics style the CAD gives it), blocks included. The dialog
lists the layers with their line counts to tick, the Revit line styles,
and where to make the lines: detail lines in the active view (a plan or
drafting view) or model lines on a level. Every line is laid flat on the
target plane - lines, arcs, circles and flat splines keep their shape,
a polyline becomes its segments and a curve with heights straight
segments in plan. One transaction; the new lines are left selected and
the output lists them by layer. No closing dialog unless nothing could
be made.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

__title__ = "Copy CAD\nLines"
__author__ = "Glent Group"

import math
import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System.Data")
from Autodesk.Revit.DB import (Arc, BuiltInCategory, Curve, ElementId, FilteredElementCollector,
                               GeometryInstance, GraphicsStyle, GraphicsStyleType, ImportInstance,
                               Level, Line, Options, Plane, PolyLine, SketchPlane, Transaction,
                               Transform, ViewType, XYZ)
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from System.Collections.Generic import List

from pyrevit import revit, forms, script

import pymep_cad_lines as CL
from pymep_config import load_settings, save_settings
from pymep_revit import id_value, quiet

output = script.get_output()
doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView

XAML_PATH = os.path.join(os.path.dirname(os.path.abspath(CL.__file__)), "pymep_cad_lines.xaml")
DETAIL_VIEWS = ("FloorPlan", "CeilingPlan", "EngineeringPlan", "AreaPlan", "DraftingView")


# ---------------------------------------------------------------------------
# 1. The CAD: selected, the only one in the view, or picked
# ---------------------------------------------------------------------------
class CadFilter(ISelectionFilter):
    def AllowElement(self, el):
        return isinstance(el, ImportInstance)

    def AllowReference(self, ref, pt):
        return False


def find_cad():
    sel = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    sel = [e for e in sel if isinstance(e, ImportInstance)]
    if len(sel) == 1:
        return sel[0]
    try:
        in_view = [e for e in FilteredElementCollector(doc, view.Id).OfClass(ImportInstance)]
    except Exception:
        in_view = []
    if len(in_view) == 1:
        return in_view[0]
    try:
        ref = uidoc.Selection.PickObject(ObjectType.Element, CadFilter(), "Pick the linked or imported CAD")
    except Exception:
        return None
    return doc.GetElement(ref.ElementId)


cad = find_cad()
if cad is None:
    script.exit()
cad_name = u"{0}".format(cad.Category.Name if cad.Category is not None else u"CAD")
cad_kind = u"linked" if getattr(cad, "IsLinked", False) else u"imported"


# ---------------------------------------------------------------------------
# 2. Its curves, with their layers
# ---------------------------------------------------------------------------
_layer_names = {}


def layer_of(obj):
    try:
        sid = obj.GraphicsStyleId
        key = id_value(sid)
    except Exception:
        return CL.NO_LAYER
    if key not in _layer_names:
        name = CL.NO_LAYER
        try:
            gs = doc.GetElement(sid)
            if gs is not None and gs.GraphicsStyleCategory is not None:
                name = u"{0}".format(gs.GraphicsStyleCategory.Name)
        except Exception:
            pass
        _layer_names[key] = name
    return _layer_names[key]


def read_curves(inst):
    """[(layer, Curve or PolyLine)] in model coordinates."""
    opt = Options()
    try:
        if inst.ViewSpecific:
            opt.View = doc.GetElement(inst.OwnerViewId)
    except Exception:
        pass
    out = []

    def walk(geo, depth):
        if geo is None or depth > 12:
            return
        for obj in geo:
            if isinstance(obj, GeometryInstance):
                try:
                    walk(obj.GetInstanceGeometry(), depth + 1)
                except Exception:
                    continue
            elif isinstance(obj, (Curve, PolyLine)):
                out.append((layer_of(obj), obj))
    walk(inst.get_Geometry(opt), 0)
    return out


curves = read_curves(cad)
if not curves:
    forms.alert(u"{0} has no lines Revit can read (a link that is not loaded, or only solids "
                u"and text).".format(cad_name), exitscript=True)
counts = {}
for lay, _c in curves:
    counts[lay] = counts.get(lay, 0) + 1


# ---------------------------------------------------------------------------
# 3. Line styles and levels
# ---------------------------------------------------------------------------
styles = {}
try:
    for sub in doc.Settings.Categories.get_Item(BuiltInCategory.OST_Lines).SubCategories:
        gs = sub.GetGraphicsStyle(GraphicsStyleType.Projection)
        if gs is not None:
            styles[u"{0}".format(sub.Name)] = gs
except Exception:
    pass
style_names = CL.usable_styles(list(styles.keys()))
if not style_names:
    forms.alert("The model has no line styles to give the lines.", exitscript=True)
levels = sorted([l for l in FilteredElementCollector(doc).OfClass(Level)], key=lambda l: l.Elevation)
if not levels:
    forms.alert("The model has no levels.", exitscript=True)
detail_ok = u"{0}".format(view.ViewType) in DETAIL_VIEWS
settings = load_settings()


# ---------------------------------------------------------------------------
# 4. The dialog
# ---------------------------------------------------------------------------
class CadLinesWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        from System.Data import DataTable
        self.result = None
        self.TxtCad.Text = u"{0} ({1}) - {2} line(s) on {3} layer(s).".format(
            cad_name, cad_kind, len(curves), len(counts))
        table = DataTable("layers")
        table.Columns.Add("Pick", clr.GetClrType(bool))
        table.Columns.Add("Layer", clr.GetClrType(str))
        table.Columns.Add("Lines", clr.GetClrType(int))
        for r in CL.layer_rows(counts, settings.get(CL.SETTINGS_LAYERS) or []):
            row = table.NewRow()
            row["Pick"] = bool(r["pick"])
            row["Layer"] = r["name"]
            row["Lines"] = int(r["count"])
            table.Rows.Add(row)
        self.table = table
        self.GridLayers.ItemsSource = table.DefaultView
        table.ColumnChanged += self.on_ticked
        for n in style_names:
            self.CmbStyle.Items.Add(n)
        self.CmbStyle.SelectedItem = CL.default_style(style_names, settings.get(CL.SETTINGS_STYLE))
        want_level = settings.get(CL.SETTINGS_LEVEL)
        for i, l in enumerate(levels):
            self.CmbLevel.Items.Add(u"{0}  ({1:.3f} m)".format(l.Name, l.Elevation * 0.3048))
            if l.Name == want_level:
                self.CmbLevel.SelectedIndex = i
        if self.CmbLevel.SelectedIndex < 0:
            # the active view's own level, else the lowest
            ids = [id_value(l.Id) for l in levels]
            try:
                self.CmbLevel.SelectedIndex = ids.index(id_value(view.GenLevel.Id))
            except Exception:
                self.CmbLevel.SelectedIndex = 0
        if detail_ok:
            self.TxtDetailHint.Text = u"in '{0}'".format(view.Name)
        else:
            self.RadDetail.IsEnabled = False
            self.TxtDetailHint.Text = (u"'{0}' cannot take detail lines from a plan - open a plan or drafting "
                                       u"view for detail lines".format(view.Name))
        if settings.get(CL.SETTINGS_MODE) == "model" or not detail_ok:
            self.RadModel.IsChecked = True
        self.on_mode(None, None)
        self.on_ticked(None, None)

    def ticked(self):
        return [u"{0}".format(r["Layer"]) for r in self.table.Rows if bool(r["Pick"])]

    def on_ticked(self, sender, args):
        names = set(self.ticked())
        n = sum(counts.get(x, 0) for x in names)
        self.TxtCount.Text = u"{0} layer(s) ticked - {1} line(s) to copy.".format(len(names), n)

    def on_find(self, sender, args):
        try:
            self.table.DefaultView.RowFilter = CL.rowfilter(self.TxtFind.Text)
        except Exception:
            self.table.DefaultView.RowFilter = u""

    def _tick_shown(self, value):
        for rv in self.table.DefaultView:
            rv["Pick"] = value
        self.on_ticked(None, None)

    def on_all(self, sender, args):
        self._tick_shown(True)

    def on_none(self, sender, args):
        self._tick_shown(False)

    def on_mode(self, sender, args):
        self.CmbLevel.IsEnabled = bool(self.RadModel.IsChecked)

    def on_copy(self, sender, args):
        names = self.ticked()
        if not names:
            self.StatusText.Text = u"Tick at least one layer."
            return
        if self.CmbStyle.SelectedItem is None:
            self.StatusText.Text = u"Choose a line style."
            return
        model = bool(self.RadModel.IsChecked)
        if model and self.CmbLevel.SelectedIndex < 0:
            self.StatusText.Text = u"Choose the level for the model lines."
            return
        self.result = {"layers": names, "style": u"{0}".format(self.CmbStyle.SelectedItem),
                       "mode": "model" if model else "detail",
                       "level": levels[self.CmbLevel.SelectedIndex] if model else None}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


win = CadLinesWindow()
win.ShowDialog()
choice = win.result
if not choice:
    script.exit()
try:
    settings[CL.SETTINGS_LAYERS] = choice["layers"]
    settings[CL.SETTINGS_STYLE] = choice["style"]
    settings[CL.SETTINGS_MODE] = choice["mode"]
    if choice["level"] is not None:
        settings[CL.SETTINGS_LEVEL] = choice["level"].Name
    save_settings(settings)
except Exception:
    pass


# ---------------------------------------------------------------------------
# 5. Make the lines
# ---------------------------------------------------------------------------
min_len = 0.0026
try:
    min_len = doc.Application.ShortCurveTolerance
except Exception:
    pass


def flat_curves(geo, z):
    """The geometry laid flat at height z: [Curve] (empty when nothing is
    long enough)."""
    if isinstance(geo, PolyLine):
        pts = [(p.X, p.Y, p.Z) for p in geo.GetCoordinates()]
        return [Line.CreateBound(XYZ(a[0], a[1], a[2]), XYZ(b[0], b[1], b[2]))
                for a, b in CL.segments(pts, z, min_len)]
    if not geo.IsBound:
        if isinstance(geo, Arc):                  # a full circle
            c = geo.Center
            return [Arc.Create(Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ(c.X, c.Y, z)),
                               geo.Radius, 0.0, 2.0 * math.pi)]
        pts = [(p.X, p.Y, p.Z) for p in geo.Tessellate()]
        return [Line.CreateBound(XYZ(a[0], a[1], a[2]), XYZ(b[0], b[1], b[2]))
                for a, b in CL.segments(pts, z, min_len)]
    pts = list(geo.Tessellate())
    if CL.is_flat([p.Z for p in pts]):
        if geo.Length < min_len:
            return []
        dz = z - pts[0].Z
        return [geo if abs(dz) < 1e-9 else geo.CreateTransformed(Transform.CreateTranslation(XYZ(0, 0, dz)))]
    return [Line.CreateBound(XYZ(a[0], a[1], a[2]), XYZ(b[0], b[1], b[2]))
            for a, b in CL.segments([(p.X, p.Y, p.Z) for p in pts], z, min_len)]


def detail_plane_z():
    """The height detail lines in this view take: tried with a probe
    line, from the view's own sketch plane, its level and its origin."""
    cands = []
    try:
        if view.SketchPlane is not None:
            cands.append(view.SketchPlane.GetPlane().Origin.Z)
    except Exception:
        pass
    try:
        if view.GenLevel is not None:
            cands.append(view.GenLevel.Elevation)
    except Exception:
        pass
    try:
        cands.append(view.Origin.Z)
    except Exception:
        pass
    cands.append(0.0)
    seen = []
    for z in cands:
        if any(abs(z - s) < 1e-9 for s in seen):
            continue
        seen.append(z)
        try:
            probe = doc.Create.NewDetailCurve(view, Line.CreateBound(XYZ(0, 0, z), XYZ(1, 0, z)))
            doc.Delete(probe.Id)
            return z
        except Exception:
            continue
    raise ValueError(u"'{0}' does not take detail lines".format(view.Name))


wanted = set(choice["layers"])
style = styles.get(choice["style"])
made, failed, short = {}, {}, {}
why = {}
new_ids = []
t = Transaction(doc, u"pyMEP: Copy CAD lines ({0})".format(cad_name))
quiet(t)
t.Start()
try:
    if choice["mode"] == "model":
        sp = SketchPlane.Create(doc, choice["level"].Id)
        z = sp.GetPlane().Origin.Z
        make = lambda c: doc.Create.NewModelCurve(c, sp)
    else:
        z = detail_plane_z()
        make = lambda c: doc.Create.NewDetailCurve(view, c)
    style_ok = None
    for lay, geo in curves:
        if lay not in wanted:
            continue
        try:
            pieces = flat_curves(geo, z)
        except Exception as ex:
            failed[lay] = failed.get(lay, 0) + 1
            why.setdefault(u"{0}".format(ex), 0)
            why[u"{0}".format(ex)] += 1
            continue
        if not pieces:
            short[lay] = short.get(lay, 0) + 1
            continue
        for c in pieces:
            try:
                el = make(c)
            except Exception as ex:
                failed[lay] = failed.get(lay, 0) + 1
                why[u"{0}".format(ex)] = why.get(u"{0}".format(ex), 0) + 1
                continue
            if style is not None:
                if style_ok is None:
                    try:
                        style_ok = any(id_value(i) == id_value(style.Id) for i in el.GetLineStyleIds())
                    except Exception:
                        style_ok = True
                if style_ok:
                    try:
                        el.LineStyle = style
                    except Exception:
                        pass
            new_ids.append(el.Id)
            made[lay] = made.get(lay, 0) + 1
    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert(u"Failed, nothing made:\n\n{0}".format(ex), exitscript=True)

try:
    ids = List[ElementId]()
    for i in new_ids:
        ids.Add(i)
    if ids.Count:
        uidoc.Selection.SetElementIds(ids)
except Exception:
    pass

where = (u"model lines on level {0}".format(choice["level"].Name) if choice["mode"] == "model"
         else u"detail lines in '{0}'".format(view.Name))
output.print_md("### Copy CAD Lines")
output.print_md(u"**CAD:** {0} ({1})  |  **Made:** {2} {3}  |  **Line style:** {4}{5}".format(
    cad_name, cad_kind, len(new_ids), where, choice["style"],
    u" (not allowed for these lines - left as Revit's default)" if style_ok is False else u""))
output.print_table(table_data=CL.report_rows(made, failed, short),
                   columns=["Layer", "Lines made", "Failed", "Too short"])
if why:
    output.print_md(u"**Why some failed:** " + u"; ".join(
        u"{0} ({1})".format(k, n) for k, n in sorted(why.items(), key=lambda kv: -kv[1])[:5]))
if not new_ids:
    forms.alert(u"No lines were made from the ticked layers - see the output window for why.",
                title="Copy CAD Lines")
