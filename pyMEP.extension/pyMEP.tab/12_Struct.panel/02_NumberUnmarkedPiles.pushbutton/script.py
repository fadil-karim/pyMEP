# -*- coding: utf-8 -*-
"""Number Unmarked Piles - give every pile with an empty Mark a number in
a set of the pile sequence, with a browser preview first.

WHAT it reads: every model element of the chosen category (Structural
Foundations by default) whose Mark is empty, and the numbered marks of
the same category - grouped by letter prefix (P1604 -> prefix P) and by
set, one leading digit each: P1001.. is set 1 (1000-1999), P2001.. set 2.
Marks with a suffix such as P1608-1 hold their number.

WHAT it does: the unmarked piles - all of them, or only the ones
selected in the model - take numbers in the chosen set, with the set's
prefix and zero padding. By default the numbers go ABOVE the highest
number in the set (a set nobody is numbered in yet starts at x001). The
other option uses the GAPS in the sequence first - the unused numbers
between the set's lowest and highest pile, lowest first - and then goes
above the highest. A set that runs out of numbers is refused, never
spilled into the next one. The piles take their numbers in the order
they were placed (element id), west to east, or north to south.

PREVIEW IN BROWSER writes the same page as Add to Pile Sequence - the
table in number order and a plan of the piles - with the newly numbered
piles in blue. APPLY writes the marks in one transaction, selects the
piles and writes a CSV of what was done.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

__title__ = "Number\nUnmarked Piles"
__author__ = "Glent Group"

import datetime
import io
import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (BuiltInParameter, CategoryType, ElementId,
                               FilteredElementCollector, Transaction)

from pyrevit import revit, forms, script

import pymep_pile_sequence as PS
from pymep_config import get_export_folder, load_settings, save_settings
from pymep_dashboard_launch import launch_html
from pymep_revit import id_value, make_id, quiet

output = script.get_output()
doc = revit.doc
uidoc = revit.uidoc

XAML_PATH = os.path.join(os.path.dirname(os.path.abspath(PS.__file__)),
                         "pymep_pile_number.xaml")
DEFAULT_CATEGORY = "Structural Foundations"
SETTINGS_CATEGORY = "pile_number_category"
SETTINGS_MODE = "pile_number_mode"
SETTINGS_ORDER = "pile_number_order"
FT_TO_M = 0.3048


# ---------------------------------------------------------------------------
# 1. The piles: empty marks to number, numbered marks to number around
# ---------------------------------------------------------------------------
def _mark_param(el):
    try:
        return el.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
    except Exception:
        return None


def _plan_xy(el):
    """(x, y) in metres of where the pile stands: its location point,
    else the centre of its bounding box; None when neither reads."""
    try:
        pt = getattr(el.Location, "Point", None)
        if pt is not None:
            return (pt.X * FT_TO_M, pt.Y * FT_TO_M)
    except Exception:
        pass
    try:
        bb = el.get_BoundingBox(None)
        if bb is not None:
            return ((bb.Min.X + bb.Max.X) * 0.5 * FT_TO_M,
                    (bb.Min.Y + bb.Max.Y) * 0.5 * FT_TO_M)
    except Exception:
        pass
    return None


numbered = {}     # category -> [(id, mark)] with a number in the mark
unmarked = {}     # category -> [id] with an empty, writable mark
points = {}
for el in FilteredElementCollector(doc).WhereElementIsNotElementType():
    try:
        cat = el.Category
        if cat is None or cat.CategoryType != CategoryType.Model:
            continue
        name = u"{0}".format(cat.Name)
    except Exception:
        continue
    p = _mark_param(el)
    if p is None:
        continue
    try:
        mark = u"{0}".format(p.AsString() or u"").strip() if p.HasValue else u""
    except Exception:
        continue
    eid = id_value(el.Id)
    if not mark:
        if p.IsReadOnly:
            continue
        unmarked.setdefault(name, []).append(eid)
    elif PS.parse_mark(mark) is not None:
        numbered.setdefault(name, []).append((eid, mark))
    else:
        continue
    xy = _plan_xy(el)
    if xy is not None:
        points[eid] = xy

if not unmarked:
    forms.alert("No model element in this document has an empty Mark - "
                "there is nothing to number.", exitscript=True)

selected = set()
try:
    selected = set(id_value(i) for i in uidoc.Selection.GetElementIds())
except Exception:
    selected = set()

category_names = sorted(unmarked, key=lambda n: (n != DEFAULT_CATEGORY, n.lower()))
settings = load_settings()


# ---------------------------------------------------------------------------
# 2. The dialog
# ---------------------------------------------------------------------------
class NumberWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self._ready = False
        self.groups = {}
        self.ignored = []
        self.prefix_keys = []
        self.sets = []
        for name in category_names:
            self.CmbCategory.Items.Add(u"{0}  ({1} without a mark)".format(
                name, len(unmarked[name])))
        for _k, label in PS.ORDERS:
            self.CmbOrder.Items.Add(label)
        keys = [k for k, _l in PS.ORDERS]
        order = settings.get(SETTINGS_ORDER) or "placement"
        self.CmbOrder.SelectedIndex = keys.index(order) if order in keys else 0
        if settings.get(SETTINGS_MODE) == "gaps":
            self.RadGaps.IsChecked = True
        else:
            self.RadAbove.IsChecked = True
        remembered = settings.get(SETTINGS_CATEGORY) or DEFAULT_CATEGORY
        self.CmbCategory.SelectedIndex = (category_names.index(remembered)
                                          if remembered in category_names else 0)
        self._ready = True
        self._load_category()

    # -- reading the dialog ---------------------------------------------------
    def category(self):
        i = self.CmbCategory.SelectedIndex
        if i is None or i < 0 or i >= len(category_names):
            return category_names[0]
        return category_names[i]

    def prefix_key(self):
        i = self.CmbPrefix.SelectedIndex
        if i is None or i < 0 or i >= len(self.prefix_keys):
            return None
        return self.prefix_keys[i]

    def group_entries(self):
        k = self.prefix_key()
        return self.groups.get(k, []) if k is not None else []

    def group_width(self):
        ws = [e["width"] for e in self.group_entries()]
        return PS._dominant(ws) if ws else 4

    def mode(self):
        try:
            return "gaps" if self.RadGaps.IsChecked else "above"
        except Exception:
            return "above"

    def order(self):
        i = self.CmbOrder.SelectedIndex
        keys = [k for k, _l in PS.ORDERS]
        return keys[i] if i is not None and 0 <= i < len(keys) else "placement"

    def selected_unmarked(self):
        return [i for i in unmarked.get(self.category(), []) if i in selected]

    def targets(self):
        ids = unmarked.get(self.category(), [])
        try:
            if self.ChkSelected.IsChecked:
                ids = self.selected_unmarked()
        except Exception:
            pass
        return PS.order_unmarked(ids, points, self.order())

    def bounds(self):
        """(bounds, error): the typed set wins over the chosen one."""
        typed = (self.TxtSet.Text or u"").strip()
        if typed:
            try:
                nums = PS.parse_numbers(typed)
            except ValueError:
                nums = []
            if len(nums) != 1:
                return None, u"Type one number in the set to use, such as 3001 or P3001."
            digits = len(u"{0}".format(nums[0]))
            return PS.set_bounds(nums[0], max(digits, self.group_width() if self.prefix_keys else digits)), u""
        i = self.CmbSet.SelectedIndex
        if i is None or i < 0 or i >= len(self.sets):
            return None, u"Choose a set, or type a number in the set to use."
        return self.sets[i][0], u""

    def numbering(self):
        """The plan and the prefix / width it writes, or (None, error)."""
        ids = self.targets()
        if not ids:
            return None, (u"None of the selected elements is an unmarked pile of this category."
                          if self.ChkSelected.IsChecked else u"No pile of this category has an empty Mark.")
        b, err = self.bounds()
        if b is None:
            return None, err
        entries = [e for e in self.group_entries() if b[0] <= e["number"] <= b[1]]
        if self.prefix_keys:
            prefix = PS._dominant([e["prefix"] for e in (entries or self.group_entries())])
        else:
            prefix = (self.TxtPrefix.Text or u"").strip() or u"P"
        width = PS._dominant([e["width"] for e in entries]) if entries else len(u"{0}".format(b[1]))
        used = PS.numbers_in_use(entries, self.ignored, self.prefix_key(), b)
        try:
            plan = PS.plan_numbering(entries, ids, b, self.mode(), prefix, width, used)
        except ValueError as ex:
            return None, u"{0}".format(ex)
        plan["prefix"], plan["width"], plan["bounds"] = prefix, width, b
        return plan, u""

    # -- filling the dialog ---------------------------------------------------
    def _show(self, ctl, on):
        try:
            from System.Windows import Visibility
            ctl.Visibility = Visibility.Visible if on else Visibility.Collapsed
        except Exception:
            pass

    def _load_category(self):
        self.groups, self.ignored = PS.group_marks(numbered.get(self.category(), []))
        self.prefix_keys = sorted(self.groups, key=lambda k: (-len(self.groups[k]), k))
        self.CmbPrefix.Items.Clear()
        for k in self.prefix_keys:
            self.CmbPrefix.Items.Add(PS.group_label(k, self.groups[k]))
        self._show(self.TxtPrefix, not self.prefix_keys)
        self._show(self.TxtPrefixHint, not self.prefix_keys)
        if not self.prefix_keys and not (self.TxtPrefix.Text or u"").strip():
            self.TxtPrefix.Text = u"P"
        if self.prefix_keys:
            self.CmbPrefix.SelectedIndex = self.prefix_keys.index("P") if "P" in self.prefix_keys else 0
        self._load_sets()
        n_sel = len(self.selected_unmarked())
        self.ChkSelected.IsEnabled = n_sel > 0
        if not n_sel:
            self.ChkSelected.IsChecked = False
        self.ChkSelected.Content = (u"only the piles selected in the model ({0} of them have no mark)".format(n_sel)
                                    if n_sel else u"only the piles selected in the model (none selected)")
        self._refresh()

    def _load_sets(self):
        self.sets = PS.split_sets(self.group_entries())
        self.CmbSet.Items.Clear()
        for b, ents in self.sets:
            self.CmbSet.Items.Add(PS.set_label(b, ents))
        if self.sets:
            self.CmbSet.SelectedIndex = 0

    def _refresh(self):
        if not getattr(self, "_ready", False):
            return
        n = len(unmarked.get(self.category(), []))
        self.TxtUnmarked.Text = u"{0} element(s) of {1} have an empty Mark{2}.".format(
            n, self.category(), u" - {0} of them selected".format(len(self.selected_unmarked()))
            if self.selected_unmarked() else u"")
        plan, err = self.numbering()
        self.TxtPlan.Text = PS.numbering_text(plan, plan["prefix"], plan["width"]) if plan else err
        self.StatusText.Text = u""

    # -- handlers -------------------------------------------------------------
    def on_category_changed(self, sender, args):
        if getattr(self, "_ready", False):
            self._load_category()

    def on_prefix_changed(self, sender, args):
        if getattr(self, "_ready", False):
            self._load_sets()
            self._refresh()

    def on_changed(self, sender, args):
        self._refresh()

    def on_preview(self, sender, args):
        plan, err = self.numbering()
        if plan is None:
            self.StatusText.Text = err
            return
        try:
            path = write_preview(plan, self.ignored, self.category())
            launch_html(path)
            self.StatusText.Text = u""
            self.TxtPlan.Text = PS.numbering_text(plan, plan["prefix"], plan["width"]) + \
                u"  Preview opened: {0}".format(path)
        except Exception as ex:
            self.StatusText.Text = u"Could not write the preview: {0}".format(ex)

    def on_apply(self, sender, args):
        plan, err = self.numbering()
        if plan is None:
            self.StatusText.Text = err
            return
        self.result = {"plan": plan, "category": self.category(), "mode": self.mode(),
                       "order": self.order(), "ignored": self.ignored}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


def _stamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def write_preview(plan, ignored, category, stem="preview"):
    folder = get_export_folder(doc)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    path = os.path.join(folder, u"pile_numbering_{0}_{1}.html".format(stem, _stamp()))
    b = plan["bounds"]
    html = PS.preview_html(
        u"Number unmarked piles - {0}".format(doc.Title),
        [(u"Category", category), (u"Prefix", plan["prefix"] or u"(none)"),
         (u"Set", u"{0}-{1}".format(b[0], b[1])),
         (u"Written", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))],
        plan, ignored=ignored, show_unchanged=True,
        points=dict((r["id"], points[r["id"]]) for r in plan["rows"]
                    if r["id"] is not None and r["id"] in points))
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    return path


win = NumberWindow()
win.ShowDialog()
if not win.result:
    script.exit()
res = win.result
plan = res["plan"]
try:
    settings[SETTINGS_CATEGORY] = res["category"]
    settings[SETTINGS_MODE] = res["mode"]
    settings[SETTINGS_ORDER] = res["order"]
    save_settings(settings)
except Exception:
    pass

# ---------------------------------------------------------------------------
# 3. Write the marks - one transaction
# ---------------------------------------------------------------------------
todo = sorted([r for r in plan["rows"] if r["kind"] == "numbered"], key=lambda r: r["number_new"])
done = []
failed = []
t = Transaction(doc, "pyMEP: Number unmarked piles ({0})".format(len(todo)))
quiet(t)
t.Start()
try:
    for r in todo:
        try:
            el = doc.GetElement(make_id(r["id"]))
            p = _mark_param(el) if el is not None else None
            if p is None:
                failed.append((r, u"element or Mark not found"))
                continue
            if p.IsReadOnly:
                failed.append((r, u"Mark is read-only"))
                continue
            if p.Set(r["new"]):
                done.append(r)
            else:
                failed.append((r, u"Set refused"))
        except Exception as ex:
            failed.append((r, u"{0}".format(ex)))
    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert("Failed, no marks written:\n\n{0}".format(ex), exitscript=True)

try:
    from System.Collections.Generic import List
    ids = List[ElementId]()
    for r in done:
        ids.Add(make_id(r["id"]))
    uidoc.Selection.SetElementIds(ids)
except Exception:
    pass

# ---------------------------------------------------------------------------
# 4. Report
# ---------------------------------------------------------------------------
folder = get_export_folder(doc)
csv_path = None
try:
    if not os.path.isdir(folder):
        os.makedirs(folder)
    csv_path = os.path.join(folder, u"pile_numbering_{0}.csv".format(_stamp()))
    with io.open(csv_path, "w", encoding="utf-8", newline="") as f:
        f.write(u"Id,New mark,Result\r\n")
        for r in done:
            f.write(u"{0},{1},numbered\r\n".format(r["id"], r["new"]))
        for r, why in failed:
            f.write(u"{0},{1},FAILED: {2}\r\n".format(r["id"], r["new"], why.replace(u",", u";")))
except Exception:
    csv_path = None
record = None
try:
    record = write_preview(plan, res["ignored"], res["category"], stem="applied")
except Exception:
    record = None

b = plan["bounds"]
output.print_md("### Number Unmarked Piles")
output.print_md(u"**Category:** {0}  |  **Set:** {1}-{2}  |  **Numbers:** {3}  |  "
                u"**Numbered:** {4}  |  **Failed:** {5}".format(
                    res["category"], b[0], b[1],
                    u"gaps first, then above the highest" if res["mode"] == "gaps" else u"above the highest",
                    len(done), len(failed)))
output.print_md(PS.numbering_text(plan, plan["prefix"], plan["width"]))
rows = [[output.linkify(make_id(r["id"])), r["new"], u"numbered"] for r in done]
rows += [[output.linkify(make_id(r["id"])), r["new"], u"FAILED: {0}".format(why)] for r, why in failed]
output.print_table(table_data=rows, columns=["Id", "New mark", "Result"])
for p in (csv_path, record):
    if p:
        output.print_md(u"Report: `{0}`".format(p))
forms.alert(u"Number Unmarked Piles\n\n{0} pile(s) numbered{1}.\n\n{2}\n\nThe piles are "
            u"selected; one undo step reverses it.".format(
                len(done), u", {0} failed - see the output window".format(len(failed)) if failed else u"",
                PS.numbering_text(plan, plan["prefix"], plan["width"])),
            title="Number Unmarked Piles")
