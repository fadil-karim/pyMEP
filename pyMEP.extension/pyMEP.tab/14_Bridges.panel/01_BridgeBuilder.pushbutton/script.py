# -*- coding: utf-8 -*-
"""Bridge Builder - choose the two end points, then open the builder
page in the browser.

The dialog asks for the generic model family type at the start and at
the end, the instance parameter that holds the deck level there and
how to read it (shared elevation, internal elevation, or offset above
the element's level). With one instance of a type it is taken; with
more you pick it in the model. The page gets the span, the two levels
and the model's framing types, floor types and levels, and saves a
design file for Build Bridge.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

__title__ = "Bridge\nBuilder"
__author__ = "Glent Group"

import datetime
import io
import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

from pyrevit import revit, forms, script

import pymep_bridge as BR
import pymep_bridge_revit as RV
from pymep_config import get_export_folder, load_settings, save_settings
from pymep_dashboard_launch import launch_html
from pymep_revit import id_value

output = script.get_output()
doc = revit.doc
uidoc = revit.uidoc

XAML_PATH = os.path.join(os.path.dirname(os.path.abspath(BR.__file__)), "pymep_bridge_setup.xaml")
HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(BR.__file__)), "pymep_bridge_builder.html")
S_START = "bridge_start_type"
S_END = "bridge_end_type"
S_PARAM = "bridge_level_param"
S_MODE = "bridge_level_mode"
OWN_Z = u"(the instance's own elevation)"

symbols = RV.generic_symbols(doc)
if not symbols:
    forms.alert("No generic model families are loaded - the start and end points "
                "are generic model instances.", exitscript=True)
settings = load_settings()
registry = BR.load_registry(RV.registry_path(doc))


class SetupWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self._ready = False
        self._sync = 0
        self.start_items = []
        self.end_items = []
        self.TxtName.Text = BR.next_name(registry)
        self._fill_types(self.CmbStart, u"", settings.get(S_START), "start")
        self._fill_types(self.CmbEnd, u"", settings.get(S_END), "end")
        # nothing remembered and both ends on the same type: offer the next
        # type for the end, since the two points are normally different
        if (not settings.get(S_END) and len(self.end_items) > 1
                and self.CmbEnd.SelectedIndex == self.CmbStart.SelectedIndex):
            self._sync += 1
            try:
                self.CmbEnd.SelectedIndex = (self.CmbStart.SelectedIndex + 1) % len(self.end_items)
            finally:
                self._sync -= 1
        self._sync += 1
        try:
            for _k, lbl in BR.LEVEL_MODES:
                self.CmbMode.Items.Add(lbl)
            keys = [k for k, _l in BR.LEVEL_MODES]
            want = settings.get(S_MODE) or "shared"
            self.CmbMode.SelectedIndex = keys.index(want) if want in keys else 0
        finally:
            self._sync -= 1
        self._ready = True
        self._load_params()

    def _fill_types(self, combo, text, want, which):
        words = [w for w in u"{0}".format(text or u"").lower().split() if w]
        items = [s for s in symbols if all(w in s["label"].lower() for w in words)]
        if which == "start":
            self.start_items = items
        else:
            self.end_items = items
        self._sync += 1
        try:
            combo.Items.Clear()
            for s in items:
                combo.Items.Add(u"{0}    ({1} placed)".format(s["label"], s["count"]))
            labels = [s["label"] for s in items]
            combo.SelectedIndex = labels.index(want) if want in labels else (0 if items else -1)
        finally:
            self._sync -= 1

    def start(self):
        i = self.CmbStart.SelectedIndex
        return self.start_items[i] if 0 <= i < len(self.start_items) else None

    def end(self):
        i = self.CmbEnd.SelectedIndex
        return self.end_items[i] if 0 <= i < len(self.end_items) else None

    def mode(self):
        i = self.CmbMode.SelectedIndex
        return BR.LEVEL_MODES[i][0] if 0 <= i < len(BR.LEVEL_MODES) else "shared"

    def param(self):
        text = u"{0}".format(self.CmbParam.SelectedItem or u"")
        return u"" if text == OWN_Z else text

    def _load_params(self):
        if not self._ready:
            return
        keep = self.param() or settings.get(S_PARAM) or u""
        els = []
        for s in (self.start(), self.end()):
            if s is not None:
                els.extend(RV.instances_of(doc, s["id"]))
        names = RV.param_names(els)
        self._sync += 1
        try:
            self.CmbParam.Items.Clear()
            self.CmbParam.Items.Add(OWN_Z)
            for n in names:
                self.CmbParam.Items.Add(n)
            if keep and keep in names:
                self.CmbParam.SelectedIndex = names.index(keep) + 1
            else:
                guess = [n for n in names if "level" in n.lower() or "elev" in n.lower()]
                self.CmbParam.SelectedIndex = names.index(guess[0]) + 1 if guess else 0
        finally:
            self._sync -= 1
        for s, box in ((self.start(), self.TxtStartInfo), (self.end(), self.TxtEndInfo)):
            if s is None:
                box.Text = u"No generic model type matches."
            elif s["count"] == 0:
                box.Text = u"No instance of this type is placed - place the point first."
            elif s["count"] == 1:
                box.Text = u"One instance placed - it is the point."
            else:
                box.Text = u"{0} instances placed - you will pick the one to use.".format(s["count"])
        self.StatusText.Text = u""

    def on_start_search(self, sender, args):
        if self._ready and not self._sync:
            self._fill_types(self.CmbStart, self.TxtStartSearch.Text, (self.start() or {}).get("label"), "start")
            self._load_params()

    def on_end_search(self, sender, args):
        if self._ready and not self._sync:
            self._fill_types(self.CmbEnd, self.TxtEndSearch.Text, (self.end() or {}).get("label"), "end")
            self._load_params()

    def on_type_changed(self, sender, args):
        if self._ready and not self._sync:
            self._load_params()

    def on_param_changed(self, sender, args):
        if self._ready and not self._sync:
            self.StatusText.Text = u""

    def on_open(self, sender, args):
        s, e = self.start(), self.end()
        if s is None or e is None:
            self.StatusText.Text = u"Pick a family type for both ends."
            return
        if s["count"] == 0 or e["count"] == 0:
            self.StatusText.Text = u"Both types need at least one placed instance."
            return
        name = u"{0}".format(self.TxtName.Text).strip()
        if not name:
            self.StatusText.Text = u"Give the bridge a name."
            return
        self.result = {"name": name, "start": s, "end": e, "param": self.param(), "mode": self.mode()}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


class TypeFilter(ISelectionFilter):
    def __init__(self, symbol_id):
        self.symbol_id = symbol_id

    def AllowElement(self, el):
        try:
            return id_value(el.GetTypeId()) == self.symbol_id
        except Exception:
            return False

    def AllowReference(self, ref, pt):
        return False


def pick_instance(sym, which):
    els = RV.instances_of(doc, sym["id"])
    if len(els) == 1:
        return els[0]
    try:
        ref = uidoc.Selection.PickObject(ObjectType.Element, TypeFilter(sym["id"]),
                                         "Pick the {0} point ({1})".format(which, sym["label"]))
        return doc.GetElement(ref.ElementId)
    except Exception:
        return None


win = SetupWindow()
win.ShowDialog()
if not win.result:
    script.exit()
res = win.result
try:
    settings[S_START] = res["start"]["label"]
    settings[S_END] = res["end"]["label"]
    settings[S_PARAM] = res["param"]
    settings[S_MODE] = res["mode"]
    save_settings(settings)
except Exception:
    pass

start_el = pick_instance(res["start"], "START")
if start_el is None:
    script.exit()
end_el = pick_instance(res["end"], "END")
if end_el is None:
    script.exit()
if start_el.Id == end_el.Id:
    forms.alert("The start and the end are the same instance.", exitscript=True)

position = RV.project_position(doc)
try:
    p_start = RV.end_point(doc, start_el, res["param"], res["mode"], position)
    p_end = RV.end_point(doc, end_el, res["param"], res["mode"], position)
    frame = BR.frame_from_points(p_start, p_end)
except ValueError as ex:
    forms.alert(u"{0}".format(ex), exitscript=True)

mode_label = dict(BR.LEVEL_MODES)[res["mode"]]
source = {"start_id": id_value(start_el.Id), "end_id": id_value(end_el.Id),
          "level_param": res["param"], "level_mode": res["mode"], "level_mode_label": mode_label,
          "start_label": RV.element_label(start_el), "end_label": RV.element_label(end_el)}
existing = registry["bridges"].get(res["name"])
data = {"name": res["name"], "model": doc.Title,
        "start": {"x_m": p_start[0], "y_m": p_start[1], "z_m": p_start[2],
                  "label": RV.element_label(start_el), "id": source["start_id"]},
        "end": {"x_m": p_end[0], "y_m": p_end[1], "z_m": p_end[2],
                "label": RV.element_label(end_el), "id": source["end_id"]},
        "span_m": frame["span"], "source": source,
        "framing_types": RV.framing_types(doc), "floor_types": RV.floor_types(doc),
        "levels": RV.levels(doc),
        "design": existing["design"] if existing else None}

with io.open(HTML_PATH, "r", encoding="utf-8") as f:
    template = f.read()
folder = get_export_folder(doc)
if not os.path.isdir(folder):
    os.makedirs(folder)
page = os.path.join(folder, u"bridge_builder_{0}.html".format(
    datetime.datetime.now().strftime("%Y%m%d_%H%M%S")))
with io.open(page, "w", encoding="utf-8", newline="\n") as f:
    f.write(BR.fill_page(template, data))
launch_html(page)

output.print_md("### Bridge Builder")
output.print_md("**{0}**: start {1} (id {2}) at {3:.3f}, {4:.3f}, deck level {5:.3f} m; end {6} "
                "(id {7}) at {8:.3f}, {9:.3f}, deck level {10:.3f} m; span **{11:.3f} m**, "
                "rise {12:+.0f} mm. Level parameter: {13} ({14}).".format(
                    res["name"], source["start_label"], source["start_id"], p_start[0], p_start[1],
                    p_start[2], source["end_label"], source["end_id"], p_end[0], p_end[1], p_end[2],
                    frame["span"], (p_end[2] - p_start[2]) * 1000.0,
                    res["param"] or "the instance's own elevation", mode_label))
output.print_md("Builder page: `{0}`. Save the design there, then run **Build Bridge**.".format(page))
