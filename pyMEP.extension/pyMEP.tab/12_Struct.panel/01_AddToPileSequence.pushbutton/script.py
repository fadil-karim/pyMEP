# -*- coding: utf-8 -*-
"""Add to Pile Sequence - close missing numbers in a mark sequence by
moving the piles above them down, with a browser preview first.

WHAT it reads: every model element of the chosen category that has a
Mark with a number in it, grouped by letter prefix (P1604 -> prefix P,
number 1604). The order is the number alone; the letters and any zero
padding are kept when a mark is rewritten. A mark with a suffix such
as P1604-1, or without a number, is ignored and listed.

SETS: each set of foundations starts at a new leading digit - P1001..
is set 1 (1000-1999), P2001.. set 2, P6001.. set 6. One set is worked
on at a time: the numbers typed must lie in it, only its piles move,
and a move that would carry a pile past the set's last number is
refused instead of spilling into the next set.

WHAT it does: the numbers typed - 1605, 1606 or P1605-P1608 - are the
MISSING ones. Every pile above a typed number moves down by one for
each typed number below it, so the holes close and the order is kept:
with 1605 and 1606 missing, P1607 becomes P1605, P1608 becomes P1606,
P1609 becomes P1607 and so on, and the old top numbers come free for
new piles. A typed number that is in use, or above the last pile, is
refused. 'Fill every gap in the set' types them all for you.

DUPLICATES move together and are flagged red. With the suffix box
ticked every pile sharing a number gets -1, -2, ... in element id order,
the first one included, at the number it ends up on; a suffix an
ignored mark already carries there is skipped. That can run on its own
with no numbers typed.

PREVIEW IN BROWSER writes a self-contained page to the model's pyMEP
folder and opens it. The Table tab is the full sequence in number
order, renames in yellow, freed numbers in green, duplicate marks in
red, remaining gaps in grey, ignored marks below. The Plan tab draws
every pile where it stands (north up, internal metres), coloured by
what happens to it, with the old and new mark and the change on hover,
wheel zoom, drag pan and optional labels. APPLY renames in one transaction,
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
SETTINGS_ALLGAPS = "pile_sequence_all_gaps"
SETTINGS_SHOW = "pile_sequence_show_unchanged"
SETTINGS_SUFFIX = "pile_sequence_suffix_duplicates"


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


FT_TO_M = 0.3048


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


by_category = {}
points = {}
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
    eid = id_value(el.Id)
    by_category.setdefault(name, []).append((eid, mark))
    xy = _plan_xy(el)
    if xy is not None:
        points[eid] = xy

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
        self.sets = []            # [(bounds, entries)] of the chosen prefix
        self._syncing = False
        for name in category_names:
            self.CmbCategory.Items.Add(u"{0}  ({1})".format(
                name, len(by_category[name])))
        remembered = settings.get(SETTINGS_CATEGORY) or DEFAULT_CATEGORY
        idx = category_names.index(remembered) if remembered in category_names else 0
        self.CmbCategory.SelectedIndex = idx
        self.ChkAllGaps.IsChecked = bool(settings.get(SETTINGS_ALLGAPS, False))
        self.ChkShowUnchanged.IsChecked = bool(settings.get(SETTINGS_SHOW, True))
        self.ChkSuffix.IsChecked = bool(settings.get(SETTINGS_SUFFIX, False))
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
        self._load_sets()
        self._describe()

    def prefix_entries(self):
        i = self.CmbPrefix.SelectedIndex
        if i is None or i < 0 or i >= len(self.prefix_keys):
            return []
        return self.groups[self.prefix_keys[i]]

    def width(self):
        counts = {}
        for e in self.prefix_entries():
            counts[e["width"]] = counts.get(e["width"], 0) + 1
        if not counts:
            return 4
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

    def _load_sets(self, select=None):
        self.sets = PS.split_sets(self.prefix_entries())
        self._syncing = True
        try:
            self.CmbSet.Items.Clear()
            for bounds, ents in self.sets:
                self.CmbSet.Items.Add(PS.set_label(bounds, ents))
            idx = 0
            for i, (bounds, _e) in enumerate(self.sets):
                if bounds == select:
                    idx = i
            if self.sets:
                self.CmbSet.SelectedIndex = idx
        finally:
            self._syncing = False

    def set_index(self):
        i = self.CmbSet.SelectedIndex
        if i is None or i < 0 or i >= len(self.sets):
            return None
        return i

    def bounds(self):
        i = self.set_index()
        return self.sets[i][0] if i is not None else None

    def entries(self):
        i = self.set_index()
        return self.sets[i][1] if i is not None else []

    def _select_set(self, bounds):
        """Point the Set combo at these bounds (adding an empty set when
        no pile is numbered there yet)."""
        for i, (b, _e) in enumerate(self.sets):
            if b == bounds:
                if self.CmbSet.SelectedIndex != i:
                    self._syncing = True
                    try:
                        self.CmbSet.SelectedIndex = i
                    finally:
                        self._syncing = False
                    self._describe(plan=False)
                return
        self.sets.append((bounds, []))
        self.sets.sort(key=lambda kv: kv[0])
        self._load_sets(select=bounds)
        self._describe(plan=False)

    def prefix_key(self):
        i = self.CmbPrefix.SelectedIndex
        if i is None or i < 0 or i >= len(self.prefix_keys):
            return u""
        return self.prefix_keys[i]

    def _describe(self, plan=True):
        e = self.entries()
        s = PS.sequence_summary(e)
        b = self.bounds()
        if not s["count"]:
            self.TxtSummary.Text = (u"No piles numbered in this set yet - "
                                    u"numbers typed here are simply reserved."
                                    if b else u"No numbered marks in this "
                                    u"category.")
            if plan:
                self._plan_text()
            return
        bits = [u"Set {0}-{1}: {2} pile(s) numbered {3} to {4}".format(
            b[0], b[1], s["count"], s["low"], s["high"])]
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
        if plan:
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

    def all_gaps(self):
        try:
            return bool(self.ChkAllGaps.IsChecked)
        except Exception:
            return False

    def suffix(self):
        try:
            return bool(self.ChkSuffix.IsChecked)
        except Exception:
            return False

    def plan(self):
        nums, err = self.numbers()
        if nums is None:
            return None, err
        if not self.prefix_entries():
            return None, u"No numbered marks to work on."
        if nums:
            target = PS.numbers_set(nums, self.width())
            if target is None:
                return None, (u"The numbers span more than one set - do one "
                              u"set at a time.")
            if target != self.bounds():
                self._select_set(target)
        if self.all_gaps():
            nums = sorted(set(nums) | set(PS.sequence_summary(self.entries())["gaps"]))
        if not nums and not self.suffix():
            return None, u"Type the missing number(s) to fill."
        try:
            plan = PS.plan_fill(
                self.entries(), nums, bounds=self.bounds(),
                suffix_duplicates=self.suffix(),
                taken=PS.taken_suffixes(self.ignored, self.prefix_key()))
        except ValueError as ex:
            return None, u"{0}".format(ex)
        return plan, u""

    def _plan_text(self):
        if not getattr(self, "_ready", False):
            return
        plan, err = self.plan()
        if plan is None:
            self.TxtPlan.Text = (u"" if err == u"Type the missing number(s) to fill."
                                 else err)
            return
        self.TxtPlan.Text = (u"Fills {0}; renames {1} pile(s); frees {5} at the "
                             u"top; suffixes {4} duplicate(s); {2} duplicate "
                             u"number(s) left; {3} gap(s) left.".format(
                                 u", ".join(u"{0}".format(n) for n in plan["filled"])
                                 or u"nothing",
                                 plan["renamed"], len(plan["duplicates_after"]),
                                 len(plan["gaps_after"]), plan["suffixed"],
                                 u", ".join(u"{0}".format(n) for n in plan["freed"])
                                 or u"nothing"))

    # -- handlers -------------------------------------------------------------------
    def on_category_changed(self, sender, args):
        if getattr(self, "_ready", False):
            self._load_category()

    def on_prefix_changed(self, sender, args):
        if getattr(self, "_ready", False):
            self._load_sets()
            self._describe()

    def on_set_changed(self, sender, args):
        if getattr(self, "_ready", False) and not self._syncing:
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
                                 self.prefix_key(), self.bounds(),
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
                       "prefix": self.prefix_key(), "set": self.bounds(),
                       "all_gaps": self.all_gaps(), "suffix": self.suffix(),
                       "show": bool(self.ChkShowUnchanged.IsChecked)}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


def _stamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def write_preview(plan, ignored, category, prefix, bounds, show_unchanged,
                  stem="preview"):
    folder = get_export_folder(doc)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    path = os.path.join(folder, u"pile_sequence_{0}_{1}.html".format(stem, _stamp()))
    html = PS.preview_html(
        u"Pile sequence - {0}".format(doc.Title),
        [(u"Category", category), (u"Prefix", prefix or u"(none)"),
         (u"Set", u"{0}-{1}".format(bounds[0], bounds[1]) if bounds else u"-"),
         (u"Written", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))],
        plan, ignored=ignored, show_unchanged=show_unchanged,
        points=dict((r["id"], points[r["id"]]) for r in plan["rows"]
                    if r["id"] is not None and r["id"] in points))
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
    settings[SETTINGS_ALLGAPS] = bool(res["all_gaps"])
    settings[SETTINGS_SHOW] = bool(res["show"])
    settings[SETTINGS_SUFFIX] = bool(res["suffix"])
    save_settings(settings)
except Exception:
    pass

renames = PS.apply_order(plan["rows"])
if not renames:
    forms.alert("Nothing to rename - no pile sits above the typed numbers and "
                "there are no duplicates to suffix.", title="Add to Pile Sequence")
    script.exit()

# ---------------------------------------------------------------------------
# 3. Rename - one transaction, lowest new number first
# ---------------------------------------------------------------------------
done = []
failed = []
t = Transaction(doc, "pyMEP: Add to pile sequence ({0} rename(s))".format(
    len(renames)))   # lowest new number first - see apply_order
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
                           res["set"], True, stem="applied")
except Exception:
    record = None

output.print_md("### Add to Pile Sequence")
output.print_md("**Category:** {0}  |  **Prefix:** {1}  |  **Set:** {6}  |  "
                "**Filled:** {2}  |  **Freed at the top:** {8}  |  **Renamed:** "
                "{3}  |  **Suffixed:** {7}  |  **Failed:** {4}  |  **Duplicate "
                "numbers left:** {5}".format(
                    res["category"], res["prefix"] or "(none)",
                    u", ".join(u"{0}".format(n) for n in plan["filled"])
                    or "nothing",
                    len([r for r in done if r["kind"] == "renamed"]),
                    len(failed), len(plan["duplicates_after"]),
                    u"{0}-{1}".format(res["set"][0], res["set"][1])
                    if res["set"] else "-",
                    len([r for r in done if r["kind"] == "suffixed"]),
                    u", ".join(u"{0}".format(n) for n in plan["freed"]) or "none"))
rows = [[output.linkify(make_id(r["id"])), r["old"], r["new"],
         "suffixed (duplicate)" if r["kind"] == "suffixed" else "renamed"]
        for r in sorted(done, key=lambda r: (r["number_new"], r["new"]))]
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

forms.alert("Add to Pile Sequence\n\nFilled {0}; {5} now free at the top.\n"
            "{1} pile(s) renamed, {4} duplicate(s) suffixed, {2} failed.{3}".format(
                u", ".join(u"{0}".format(n) for n in plan["filled"]) or u"nothing",
                len([r for r in done if r["kind"] == "renamed"]), len(failed),
                u"\n\n{0} duplicate number(s) remain - see the output window."
                .format(len(plan["duplicates_after"]))
                if plan["duplicates_after"] else u"",
                len([r for r in done if r["kind"] == "suffixed"]),
                u", ".join(u"{0}".format(n) for n in plan["freed"]) or u"nothing"),
            title="Add to Pile Sequence")
