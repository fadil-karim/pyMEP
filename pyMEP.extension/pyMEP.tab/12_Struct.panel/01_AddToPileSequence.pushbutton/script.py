# -*- coding: utf-8 -*-
"""Add to Pile Sequence - free numbers in a mark sequence by moving the
piles above them up, with a browser preview first.

WHAT it reads: every model element of the chosen category that has a
Mark with a number in it, grouped by letter prefix (P1604 -> prefix P,
number 1604). The order is the number alone; the letters and any zero
padding are kept when a mark is rewritten. A mark with a suffix such
as P1604-1, or without a number, is ignored and listed.

WHAT it does: for each number typed - 1605, 1606 or P1605-P1608 - the
number is freed for a new pile. A number not in use is simply
reserved; a number in use moves the piles at it and above up by one,
either as far as the next free number (the default: a gap above
absorbs the move) or every higher pile. Lowest number first, each on
the result of the previous one.

PREVIEW IN BROWSER writes a self-contained page to the model's pyMEP
folder and opens it: the full sequence in number order, renames in
yellow, freed numbers in green, duplicate marks in red, remaining gaps
in grey, ignored marks below. APPLY renames in one transaction,
highest number first so no two piles share a mark part-way through,
and writes a CSV of what changed.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

__title__ = "Add to Pile\nSequence"
__author__ = "Glent Group"

import datetime
import io
import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (BuiltInParameter, CategoryType,
                               FilteredElementCollector, Transaction)

from pyrevit import revit, forms, script

import pymep_pile_sequence as PS
from pymep_config import get_export_folder, load_settings, save_settings
from pymep_dashboard_launch import launch_html
from pymep_revit import id_value, make_id, quiet

output = script.get_output()
doc = revit.doc

XAML_PATH = os.path.join(os.path.dirname(os.path.abspath(PS.__file__)),
                         "pymep_pile_sequence.xaml")
DEFAULT_CATEGORY = "Structural Foundations"
SETTINGS_CATEGORY = "pile_sequence_category"
SETTINGS_FILL = "pile_sequence_fill_gaps"
SETTINGS_SHOW = "pile_sequence_show_unchanged"


# ---------------------------------------------------------------------------
# 1. Every numbered mark in the model, by category
# ---------------------------------------------------------------------------
def _mark(el):
    try:
        p = el.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        if p is None or not p.HasValue:
            return u""
        return u"{0}".format(p.AsString() or u"").strip()
    except Exception:
        return u""


by_category = {}
for el in FilteredElementCollector(doc).WhereElementIsNotElementType():
    try:
        cat = el.Category
        if cat is None or cat.CategoryType != CategoryType.Model:
            continue
        name = u"{0}".format(cat.Name)
    except Exception:
        continue
    mark = _mark(el)
    if not mark or PS.parse_mark(mark) is None:
        continue
    by_category.setdefault(name, []).append((id_value(el.Id), mark))

if not by_category:
    forms.alert("No model element in this document has a Mark with a "
                "number in it.", exitscript=True)

category_names = sorted(by_category, key=lambda n: (-len(by_category[n]),
                                                     n.lower()))
settings = load_settings()


# ---------------------------------------------------------------------------
# 2. The dialog
# ---------------------------------------------------------------------------
class SequenceWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self._ready = False
        self.groups = {}
        self.ignored = []
        self.prefix_keys = []
        for name in category_names:
            self.CmbCategory.Items.Add(u"{0}  ({1})".format(
                name, len(by_category[name])))
        remembered = settings.get(SETTINGS_CATEGORY) or DEFAULT_CATEGORY
        idx = category_names.index(remembered) if remembered in category_names else 0
        self.CmbCategory.SelectedIndex = idx
        (self.RbFill if settings.get(SETTINGS_FILL, True)
         else self.RbAll).IsChecked = True
        self.ChkShowUnchanged.IsChecked = bool(settings.get(SETTINGS_SHOW, True))
        self._ready = True
        self._load_category()

    # -- piles --------------------------------------------------------------------
    def category(self):
        i = self.CmbCategory.SelectedIndex
        if i is None or i < 0 or i >= len(category_names):
            return category_names[0]
        return category_names[i]

    def _load_category(self):
        self.groups, self.ignored = PS.group_marks(by_category[self.category()])
        self.prefix_keys = sorted(self.groups,
                                  key=lambda k: (-len(self.groups[k]), k))
        self.CmbPrefix.Items.Clear()
        for k in self.prefix_keys:
            self.CmbPrefix.Items.Add(PS.group_label(k, self.groups[k]))
        if self.prefix_keys:
            want = 0
            for i, k in enumerate(self.prefix_keys):
                if k == "P":
                    want = i
                    break
            self.CmbPrefix.SelectedIndex = want
        self._describe()

    def entries(self):
        i = self.CmbPrefix.SelectedIndex
        if i is None or i < 0 or i >= len(self.prefix_keys):
            return []
        return self.groups[self.prefix_keys[i]]

    def prefix_key(self):
        i = self.CmbPrefix.SelectedIndex
        if i is None or i < 0 or i >= len(self.prefix_keys):
            return u""
        return self.prefix_keys[i]

    def _describe(self):
        e = self.entries()
        s = PS.sequence_summary(e)
        if not s["count"]:
            self.TxtSummary.Text = u"No numbered marks in this category."
            return
        bits = [u"{0} pile(s) numbered {1} to {2}".format(s["count"], s["low"],
                                                           s["high"])]
        if s["gaps"]:
            shown = u", ".join(u"{0}".format(g) for g in s["gaps"][:12])
            bits.append(u"{0} gap(s): {1}{2}".format(
                len(s["gaps"]), shown, u" ..." if len(s["gaps"]) > 12 else u""))
        if s["duplicates"]:
            shown = u", ".join(u"{0} (x{1})".format(n, c) for n, c in
                               sorted(s["duplicates"].items())[:12])
            bits.append(u"{0} duplicate number(s): {1}".format(
                len(s["duplicates"]), shown))
        if self.ignored:
            bits.append(u"{0} mark(s) ignored (suffix or no number)".format(
                len(self.ignored)))
        self.TxtSummary.Text = u".  ".join(bits) + u"."
        self._plan_text()

    # -- the plan -----------------------------------------------------------------
    def numbers(self):
        """(numbers, error text)"""
        try:
            nums = PS.parse_numbers(self.TxtNumbers.Text)
        except ValueError as ex:
            return None, u"Cannot read '{0}' - use numbers like 1605, 1606 " \
                         u"or a range 1605-1608.".format(ex)
        return nums, u""

    def fill_gaps(self):
        try:
            return bool(self.RbFill.IsChecked)
        except Exception:
            return True

    def plan(self):
        nums, err = self.numbers()
        if nums is None:
            return None, err
        if not nums:
            return None, u"Type the number(s) to add."
        if not self.entries():
            return None, u"No numbered marks to work on."
        return PS.plan_insertions(self.entries(), nums, self.fill_gaps()), u""

    def _plan_text(self):
        if not getattr(self, "_ready", False):
            return
        plan, err = self.plan()
        if plan is None:
            self.TxtPlan.Text = u"" if err == u"Type the number(s) to add." else err
            return
        self.TxtPlan.Text = (u"Frees {0}; renames {1} pile(s); {2} duplicate "
                             u"number(s) after; {3} gap(s) left.".format(
                                 u", ".join(u"{0}".format(n)
                                            for n in plan["new_numbers"]),
                                 plan["renamed"], len(plan["duplicates_after"]),
                                 len(plan["gaps_after"])))

    # -- handlers -------------------------------------------------------------------
    def on_category_changed(self, sender, args):
        if getattr(self, "_ready", False):
            self._load_category()

    def on_prefix_changed(self, sender, args):
        if getattr(self, "_ready", False):
            self._describe()

    def on_numbers_changed(self, sender, args):
        self._plan_text()

    def on_preview(self, sender, args):
        plan, err = self.plan()
        if plan is None:
            self.StatusText.Text = err
            return
        try:
            path = write_preview(plan, self.ignored, self.category(),
                                 self.prefix_key(),
                                 bool(self.ChkShowUnchanged.IsChecked))
            launch_html(path)
            self.StatusText.Foreground = self.TxtSummary.Foreground
            self.StatusText.Text = u"Preview opened: {0}".format(path)
        except Exception as ex:
            self.StatusText.Text = u"Could not write the preview: {0}".format(ex)

    def on_apply(self, sender, args):
        plan, err = self.plan()
        if plan is None:
            self.StatusText.Text = err
            return
        self.result = {"plan": plan, "category": self.category(),
                       "prefix": self.prefix_key(), "fill": self.fill_gaps(),
                       "show": bool(self.ChkShowUnchanged.IsChecked)}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


def _stamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def write_preview(plan, ignored, category, prefix, show_unchanged, stem="preview"):
    folder = get_export_folder(doc)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    path = os.path.join(folder, u"pile_sequence_{0}_{1}.html".format(stem, _stamp()))
    html = PS.preview_html(
        u"Pile sequence - {0}".format(doc.Title),
        [(u"Category", category), (u"Prefix", prefix or u"(none)"),
         (u"Written", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))],
        plan, ignored=ignored, show_unchanged=show_unchanged)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    return path


win = SequenceWindow()
win.ShowDialog()
if not win.result:
    script.exit()
res = win.result
plan = res["plan"]
try:
    settings[SETTINGS_CATEGORY] = res["category"]
    settings[SETTINGS_FILL] = bool(res["fill"])
    settings[SETTINGS_SHOW] = bool(res["show"])
    save_settings(settings)
except Exception:
    pass

renames = PS.apply_order(plan["rows"])
if not renames:
    forms.alert("Nothing to rename: the number(s) were free already, so "
                "they are simply available for new piles.\n\nFreed: {0}"
                .format(u", ".join(u"{0}".format(n) for n in plan["new_numbers"])),
                title="Add to Pile Sequence")
    script.exit()

# ---------------------------------------------------------------------------
# 3. Rename - one transaction, highest number first
# ---------------------------------------------------------------------------
done = []
failed = []
t = Transaction(doc, "pyMEP: Add to pile sequence ({0} rename(s))".format(
    len(renames)))
quiet(t)
t.Start()
try:
    for r in renames:
        try:
            el = doc.GetElement(make_id(r["id"]))
            p = el.get_Parameter(BuiltInParameter.ALL_MODEL_MARK) if el is not None else None
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
    forms.alert("Failed, no marks changed:\n\n{0}".format(ex), exitscript=True)

# ---------------------------------------------------------------------------
# 4. Report
# ---------------------------------------------------------------------------
folder = get_export_folder(doc)
csv_path = None
try:
    csv_path = os.path.join(folder, u"pile_sequence_renames_{0}.csv".format(_stamp()))
    with io.open(csv_path, "w", encoding="utf-8", newline="") as f:
        f.write(u"Id,Old mark,New mark,Result\r\n")
        for r in done:
            f.write(u"{0},{1},{2},renamed\r\n".format(r["id"], r["old"], r["new"]))
        for r, why in failed:
            f.write(u"{0},{1},{2},FAILED: {3}\r\n".format(
                r["id"], r["old"], r["new"], why.replace(u",", u";")))
except Exception:
    csv_path = None
record = None
try:
    record = write_preview(plan, win.ignored, res["category"], res["prefix"],
                           True, stem="applied")
except Exception:
    record = None

output.print_md("### Add to Pile Sequence")
output.print_md("**Category:** {0}  |  **Prefix:** {1}  |  **Freed:** {2}  |  "
                "**Renamed:** {3}  |  **Failed:** {4}  |  **Duplicate numbers "
                "after:** {5}".format(
                    res["category"], res["prefix"] or "(none)",
                    u", ".join(u"{0}".format(n) for n in plan["new_numbers"]),
                    len(done), len(failed), len(plan["duplicates_after"])))
rows = [[output.linkify(make_id(r["id"])), r["old"], r["new"], "renamed"]
        for r in sorted(done, key=lambda r: r["number_new"])]
rows += [[output.linkify(make_id(r["id"])), r["old"], r["new"],
          u"FAILED: {0}".format(why)] for r, why in failed]
output.print_table(table_data=rows, columns=["Id", "Old mark", "New mark", "Result"])
if plan["duplicates_after"]:
    output.print_md("- Duplicate numbers remain: {0}. They moved together; "
                    "sort them out by hand.".format(u", ".join(
                        u"{0} (x{1})".format(n, c) for n, c in
                        sorted(plan["duplicates_after"].items()))))
for p in (csv_path, record):
    if p:
        output.print_md("Report: `{0}`".format(p))

forms.alert("Add to Pile Sequence\n\nFreed {0}.\n{1} pile(s) renamed, {2} failed."
            "{3}".format(
                u", ".join(u"{0}".format(n) for n in plan["new_numbers"]),
                len(done), len(failed),
                u"\n\n{0} duplicate number(s) remain - see the output window."
                .format(len(plan["duplicates_after"]))
                if plan["duplicates_after"] else u""),
            title="Add to Pile Sequence")
