# -*- coding: utf-8 -*-
"""CSV to Parameter - set one instance parameter on the elements of a
category from a CSV file, matching rows to elements on a key.

The dialog asks for:
  1. the category (and whether to keep to the current selection),
  2. the CSV file,
  3. the element parameter and the CSV column that identify an element
     - Mark against the CSV's Mark column, say,
  4. the element parameter to set and the CSV column with its values -
     Peak Load from a "Peak Load" column.

It counts the matches as the choices change, "Preview in browser" opens
a page with every element's current and new value plus the CSV rows no
element claimed, and Apply writes the values in one transaction.

Values: text parameters take the cell as it is; whole-number parameters
need a whole number; Yes/No takes yes/no/true/false/1/0; measured
parameters read the cell in the project's display units for that
parameter (1250 into a kN parameter is 1250 kN) and accept a unit
suffix Revit understands.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

__title__ = "CSV to\nParameter"
__author__ = "Glent Group"

import datetime
import io
import os
import re
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (CategoryType, FilteredElementCollector,
                               StorageType, Transaction, UnitUtils)

from pyrevit import revit, forms, script

import pymep_csv_params as CP
from pymep_config import get_export_folder, load_settings, save_settings
from pymep_dashboard_launch import launch_html
from pymep_revit import id_value, make_id, quiet

output = script.get_output()
doc = revit.doc
uidoc = revit.uidoc

XAML_PATH = os.path.join(os.path.dirname(os.path.abspath(CP.__file__)),
                         "pymep_csv_params.xaml")
DEFAULT_CATEGORY = "Structural Foundations"
ELEMENT_ID = u"Element Id"
PARAM_CAP = 400          # elements sampled for the parameter lists

S_CATEGORY = "csv_params_category"
S_MATCH = "csv_params_match"
S_SET = "csv_params_set"
S_CSV = "csv_params_csv"
S_KEYCOL = "csv_params_key_col"
S_VALCOL = "csv_params_value_col"
S_IGNORECASE = "csv_params_ignore_case"
S_SKIPBLANK = "csv_params_skip_blank"
S_SHOW = "csv_params_show_unchanged"


# ---------------------------------------------------------------------------
# 1. Model elements by category, and what is selected
# ---------------------------------------------------------------------------
by_category = {}
for el in FilteredElementCollector(doc).WhereElementIsNotElementType():
    try:
        cat = el.Category
        if cat is None or cat.CategoryType != CategoryType.Model:
            continue
        name = u"{0}".format(cat.Name)
    except Exception:
        continue
    by_category.setdefault(name, []).append(el)

if not by_category:
    forms.alert("No model elements in this document.", exitscript=True)

category_names = sorted(by_category, key=lambda n: (-len(by_category[n]),
                                                     n.lower()))
selected_ids = set()
try:
    selected_ids = set(id_value(i) for i in uidoc.Selection.GetElementIds())
except Exception:
    selected_ids = set()
settings = load_settings()


# ---------------------------------------------------------------------------
# 2. Parameters: what an element exposes, how it reads, how it is written
# ---------------------------------------------------------------------------
def _is_yesno(p):
    d = p.Definition
    for getter in ("GetDataType", "GetSpecTypeId"):      # 2022+ / 2021
        try:
            fn = getattr(d, getter, None)
            if fn is not None:
                sp = fn()
                if sp is not None and sp.TypeId:
                    return "spec.bool" in sp.TypeId
        except Exception:
            pass
    try:                                                 # <= 2020
        return str(d.ParameterType) == "YesNo"
    except Exception:
        return False


def _display_unit(p):
    """The unit Properties shows a measured parameter in, or None."""
    try:                                    # Revit 2021+
        u = p.GetUnitTypeId()
        if u is not None and u.TypeId:
            return u
    except Exception:
        pass
    try:                                    # Revit <= 2020
        dut = p.DisplayUnitType
        if str(dut) not in ("DUT_UNDEFINED", "DUT_GENERAL",
                            "DUT_CUSTOM", "DUT_FIXED"):
            return dut
    except Exception:
        pass
    return None


def _storage_name(st):
    s = u"{0}".format(st)
    return s.split(".")[-1]


def param_catalog(elements):
    """{name: {"storage": "String"/"Integer"/"Double"/"ElementId",
    "readonly": bool, "yesno": bool}} over the instance parameters of
    (a sample of) the elements. Writable on any element counts."""
    cat = {}
    for el in elements[:PARAM_CAP]:
        try:
            params = el.Parameters
        except Exception:
            continue
        for p in params:
            try:
                name = u"{0}".format(p.Definition.Name)
                st = _storage_name(p.StorageType)
            except Exception:
                continue
            if st == "None" or not name:
                continue
            info = cat.get(name)
            if info is None:
                cat[name] = {"storage": st, "readonly": bool(p.IsReadOnly),
                             "yesno": st == "Integer" and _is_yesno(p)}
            elif info["readonly"] and not p.IsReadOnly:
                info["readonly"] = False
    return cat


def _as_text(p):
    """A parameter's value as Properties shows it."""
    try:
        if not p.HasValue:
            return u""
        st = _storage_name(p.StorageType)
        if st == "String":
            return u"{0}".format(p.AsString() or u"")
        if st == "Integer":
            vs = p.AsValueString()
            return u"{0}".format(vs) if vs else u"{0}".format(p.AsInteger())
        if st == "Double":
            vs = p.AsValueString()
            return u"{0}".format(vs) if vs else u"{0}".format(p.AsDouble())
        if st == "ElementId":
            eid = p.AsElementId()
            if eid is None or id_value(eid) < 0:
                return u""
            ref = doc.GetElement(eid)
            return u"{0}".format(ref.Name) if ref is not None else u"{0}".format(id_value(eid))
    except Exception:
        pass
    return u""


def read_param(el, name):
    if name == ELEMENT_ID:
        return u"{0}".format(id_value(el.Id))
    try:
        p = el.LookupParameter(name)
    except Exception:
        p = None
    return _as_text(p) if p is not None else u""


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


def write_param(p, info, text):
    """(ok, why) after writing text into p according to its storage type."""
    st = info["storage"]
    text = u"{0}".format(text)
    try:
        if st == "String":
            return (True, u"") if p.Set(text) else (False, u"Set refused")
        if st == "Integer":
            ok, v, why = CP.to_yesno(text) if info["yesno"] else CP.to_integer(text)
            if not ok:
                return False, why
            return (True, u"") if p.Set(int(v)) else (False, u"Set refused")
        if st == "Double":
            try:
                if p.SetValueString(text):      # parsed in the project's units
                    return True, u""
            except Exception:
                pass
            v = CP.to_number(text)
            if v is None:
                return False, u"not a number"
            unit = _display_unit(p)
            if unit is not None:
                v = UnitUtils.ConvertToInternalUnits(float(v), unit)
            return (True, u"") if p.Set(float(v)) else (False, u"Set refused")
    except Exception as ex:
        return False, u"{0}".format(ex)
    return False, u"parameter type {0} cannot be set from text".format(st)


def validator(info):
    """What the preview can already tell is wrong for this parameter."""
    if info is None:
        return None
    if info["storage"] == "Integer":
        if info["yesno"]:
            return lambda t: CP.to_yesno(t)[2]
        return lambda t: CP.to_integer(t)[2]
    if info["storage"] == "Double":
        return lambda t: (u"" if re.search(r"\d", u"{0}".format(t)) else u"no number in it")
    return None


# ---------------------------------------------------------------------------
# 3. The dialog
# ---------------------------------------------------------------------------
class CsvParamWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self._ready = False
        self.catalog = {}
        self.headers = []
        self.rows = []
        self.csv_path = u""
        self._cache = {}
        self._sync = 0            # > 0 while a combo is being refilled by code
        for name in category_names:
            self.CmbCategory.Items.Add(u"{0}  ({1})".format(
                name, len(by_category[name])))
        remembered = settings.get(S_CATEGORY) or DEFAULT_CATEGORY
        idx = category_names.index(remembered) if remembered in category_names else 0
        self.CmbCategory.SelectedIndex = idx
        if selected_ids:
            self.ChkSelection.Content = u"only the {0} element(s) selected in Revit".format(
                len(selected_ids))
            self.ChkSelection.IsChecked = True
        else:
            self.ChkSelection.IsEnabled = False
            self.ChkSelection.Content = u"only the elements selected in Revit (nothing is selected)"
        self.ChkIgnoreCase.IsChecked = bool(settings.get(S_IGNORECASE, True))
        self.ChkSkipBlank.IsChecked = bool(settings.get(S_SKIPBLANK, True))
        self.ChkShowUnchanged.IsChecked = bool(settings.get(S_SHOW, False))
        self._ready = True
        self._load_category()
        last = settings.get(S_CSV) or u""
        if last and os.path.isfile(last):
            self._load_csv(last, quiet_fail=True)
        self._refresh()

    # -- choices ------------------------------------------------------------------
    def category(self):
        text = u"{0}".format(self.CmbCategory.SelectedItem or u"")
        return text.rsplit(u"  (", 1)[0]

    def elements(self):
        els = by_category.get(self.category(), [])
        if selected_ids and self.ChkSelection.IsChecked:
            els = [e for e in els if id_value(e.Id) in selected_ids]
        return els

    def match_param(self):
        return u"{0}".format(self.CmbMatchParam.SelectedItem or u"")

    def set_param(self):
        return u"{0}".format(self.CmbSetParam.SelectedItem or u"")

    def key_col(self):
        return u"{0}".format(self.CmbKeyCol.SelectedItem or u"")

    def value_col(self):
        return u"{0}".format(self.CmbValueCol.SelectedItem or u"")

    def _flag(self, box, default=True):
        try:
            return bool(box.IsChecked)
        except Exception:
            return default

    def _load_category(self):
        els = self.elements()
        self.catalog = param_catalog(els)
        self._cache = {}
        self.TxtElements.Text = u"{0} element(s); {1} instance parameter(s) found on them.".format(
            len(els), len(self.catalog))
        names = sorted(self.catalog, key=lambda n: n.lower())
        readable = [ELEMENT_ID] + names
        writable = [n for n in names if not self.catalog[n]["readonly"]
                    and self.catalog[n]["storage"] in ("String", "Integer", "Double")]
        self._fill(self.CmbMatchParam, readable,
                   [self.match_param(), settings.get(S_MATCH), u"Mark"])
        self._fill(self.CmbSetParam, writable,
                   [self.set_param(), settings.get(S_SET)])
        self._guess_columns()

    def _fill(self, combo, items, preferred):
        """Refill a combo and pick the first preferred item that exists,
        else the first item. Selection events raised while doing so are
        ignored - the caller refreshes once afterwards."""
        self._sync += 1
        try:
            combo.Items.Clear()
            for it in items:
                combo.Items.Add(it)
            for want in preferred:
                if want and want in items:
                    combo.SelectedIndex = items.index(want)
                    return
            if items:
                combo.SelectedIndex = 0
        finally:
            self._sync -= 1

    def _busy(self):
        return not getattr(self, "_ready", False) or self._sync > 0

    def _load_csv(self, path, quiet_fail=False):
        try:
            headers, rows = CP.read_table(path)
        except Exception as ex:
            if not quiet_fail:
                self.StatusText.Text = u"Could not read the CSV: {0}".format(ex)
            return False
        self.csv_path = path
        self.headers = headers
        self.rows = rows
        self.TxtCsv.Text = path
        shown = u", ".join(headers[:8]) + (u" ..." if len(headers) > 8 else u"")
        self.TxtCsvInfo.Text = u"{0} row(s), {1} column(s): {2}".format(
            len(rows), len(headers), shown)
        self.StatusText.Text = u""
        self._fill(self.CmbKeyCol, headers, [])
        self._fill(self.CmbValueCol, headers, [])
        self._guess_columns(force=True)
        return True

    def _guess_columns(self, force=False):
        if not self.headers:
            return
        key = self.key_col()
        if force or not key:
            key = (settings.get(S_KEYCOL) if settings.get(S_KEYCOL) in self.headers
                   else CP.guess_column(self.headers, self.match_param()))
            self._fill(self.CmbKeyCol, self.headers, [key])
        val = self.value_col()
        if force or not val:
            val = (settings.get(S_VALCOL) if settings.get(S_VALCOL) in self.headers
                   else CP.guess_column(self.headers, self.set_param(), avoid=[key]))
            self._fill(self.CmbValueCol, self.headers, [val])

    # -- the plan -----------------------------------------------------------------
    def element_rows(self):
        key = (self.category(), bool(selected_ids and self.ChkSelection.IsChecked),
               self.match_param(), self.set_param())
        if key in self._cache:
            return self._cache[key]
        rows = []
        for el in self.elements():
            rows.append({"id": id_value(el.Id), "label": element_label(el),
                         "key": read_param(el, key[2]),
                         "old": read_param(el, key[3]) if key[3] else u""})
        self._cache[key] = rows
        return rows

    def plan(self):
        """(plan, lookup, error text)"""
        if not self.headers:
            return None, None, u"Pick a CSV file."
        if not self.set_param():
            return None, None, u"No writable text, number or yes/no parameter on these elements."
        if not self.key_col() or not self.value_col():
            return None, None, u"Pick the CSV columns."
        if self.key_col() == self.value_col():
            return None, None, u"The match column and the value column are the same column."
        if self.match_param() == self.set_param():
            return None, None, u"The match parameter and the parameter to set are the same."
        try:
            lookup = CP.build_lookup(self.headers, self.rows, self.key_col(),
                                     self.value_col(),
                                     ignore_case=self._flag(self.ChkIgnoreCase))
        except ValueError as ex:
            return None, None, u"{0}".format(ex)
        if not lookup["map"]:
            return None, None, u"The '{0}' column is empty.".format(self.key_col())
        plan = CP.plan_updates(self.element_rows(), lookup,
                               skip_blank=self._flag(self.ChkSkipBlank),
                               validate=validator(self.catalog.get(self.set_param())))
        return plan, lookup, u""

    def _refresh(self):
        if not getattr(self, "_ready", False):
            return
        plan, lookup, err = self.plan()
        if plan is None:
            self.TxtSummary.Text = err
            self.BtnApply.IsEnabled = False
            self.BtnPreview.IsEnabled = False
            return
        noun = u"{0}".format(self.category()).lower() or u"element(s)"
        self.TxtSummary.Text = CP.summary_text(plan, noun)
        info = self.catalog.get(self.set_param()) or {}
        kind = {"String": u"text", "Integer": u"a whole number",
                "Double": u"a number in the parameter's project units"}.get(
                    info.get("storage"), u"")
        if info.get("yesno"):
            kind = u"yes/no"
        if kind:
            self.TxtSetInfo.Text = u"'{0}' takes {1}. A blank cell {2}.".format(
                self.set_param(), kind,
                u"leaves the parameter alone" if self._flag(self.ChkSkipBlank)
                else u"clears the parameter")
        self.BtnPreview.IsEnabled = True
        self.BtnApply.IsEnabled = plan["to_set"] > 0

    # -- handlers -------------------------------------------------------------------
    def on_category_changed(self, sender, args):
        if not self._busy():
            self._load_category()
            self._refresh()

    def on_scope_changed(self, sender, args):
        if not self._busy():
            self._load_category()
            self._refresh()

    def on_browse(self, sender, args):
        init = os.path.dirname(self.csv_path) if self.csv_path else u""
        try:
            path = forms.pick_file(file_ext="csv", init_dir=init) if init \
                else forms.pick_file(file_ext="csv")
        except Exception:
            path = forms.pick_file(file_ext="csv")
        if not path:
            return
        if self._load_csv(u"{0}".format(path)):
            self._refresh()

    def on_match_changed(self, sender, args):
        if not self._busy():
            if self.headers:
                self._fill(self.CmbKeyCol, self.headers,
                           [CP.guess_column(self.headers, self.match_param()),
                            self.key_col()])
            self._refresh()

    def on_set_changed(self, sender, args):
        if not self._busy():
            if self.headers:
                self._fill(self.CmbValueCol, self.headers,
                           [CP.guess_column(self.headers, self.set_param(),
                                            avoid=[self.key_col()]),
                            self.value_col()])
            self._refresh()

    def on_column_changed(self, sender, args):
        if not self._busy():
            self._refresh()

    def on_option_changed(self, sender, args):
        if not self._busy():
            self._refresh()

    def on_preview(self, sender, args):
        plan, lookup, err = self.plan()
        if plan is None:
            self.StatusText.Text = err
            return
        try:
            path = write_preview(plan, lookup, self._meta(),
                                 self.match_param(), self.set_param(),
                                 self._flag(self.ChkShowUnchanged, False))
            launch_html(path)
            self.StatusText.Foreground = self.TxtSummary.Foreground
            self.StatusText.Text = u"Preview opened: {0}".format(path)
        except Exception as ex:
            self.StatusText.Text = u"Could not write the preview: {0}".format(ex)

    def on_apply(self, sender, args):
        plan, lookup, err = self.plan()
        if plan is None:
            self.StatusText.Text = err
            return
        if plan["to_set"] == 0:
            self.StatusText.Text = u"Nothing to change."
            return
        self.result = {"plan": plan, "lookup": lookup, "meta": self._meta(),
                       "category": self.category(), "match": self.match_param(),
                       "set": self.set_param(), "key_col": self.key_col(),
                       "value_col": self.value_col(), "csv": self.csv_path,
                       "ignore_case": self._flag(self.ChkIgnoreCase),
                       "skip_blank": self._flag(self.ChkSkipBlank),
                       "show": self._flag(self.ChkShowUnchanged, False),
                       "info": self.catalog.get(self.set_param())}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()

    def _meta(self):
        scope = self.category()
        if selected_ids and self.ChkSelection.IsChecked:
            scope = u"{0} (selection only)".format(scope)
        return [(u"Model", doc.Title), (u"Elements", scope),
                (u"CSV", os.path.basename(self.csv_path)),
                (u"Match", u"'{0}' = column '{1}'".format(self.match_param(), self.key_col())),
                (u"Set", u"'{0}' from column '{1}'".format(self.set_param(), self.value_col())),
                (u"Written", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))]


def _stamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def write_preview(plan, lookup, meta, match_label, set_label, show_unchanged,
                  stem="preview"):
    folder = get_export_folder(doc)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    path = os.path.join(folder, u"csv_to_parameter_{0}_{1}.html".format(stem, _stamp()))
    html = CP.preview_html(u"CSV to Parameter - {0}".format(doc.Title), meta, plan,
                           lookup, show_unchanged=show_unchanged,
                           match_label=match_label, set_label=set_label)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    return path


win = CsvParamWindow()
win.ShowDialog()
if not win.result:
    script.exit()
res = win.result
plan = res["plan"]
try:
    settings[S_CATEGORY] = res["category"]
    settings[S_MATCH] = res["match"]
    settings[S_SET] = res["set"]
    settings[S_CSV] = res["csv"]
    settings[S_KEYCOL] = res["key_col"]
    settings[S_VALCOL] = res["value_col"]
    settings[S_IGNORECASE] = bool(res["ignore_case"])
    settings[S_SKIPBLANK] = bool(res["skip_blank"])
    settings[S_SHOW] = bool(res["show"])
    save_settings(settings)
except Exception:
    pass

# ---------------------------------------------------------------------------
# 4. Write - one transaction
# ---------------------------------------------------------------------------
todo = [r for r in plan["rows"] if r["kind"] == "set"]
done = []
failed = []
t = Transaction(doc, "pyMEP: {0} from CSV ({1} element(s))".format(res["set"], len(todo)))
quiet(t)
t.Start()
try:
    for r in todo:
        try:
            el = doc.GetElement(make_id(r["id"]))
            p = el.LookupParameter(res["set"]) if el is not None else None
            if p is None:
                failed.append((r, u"element or parameter not found"))
                continue
            if p.IsReadOnly:
                failed.append((r, u"parameter is read-only here"))
                continue
            ok, why = write_param(p, res["info"], r["new"])
            if ok:
                done.append(r)
            else:
                failed.append((r, why))
        except Exception as ex:
            failed.append((r, u"{0}".format(ex)))
    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert("Failed, nothing changed:\n\n{0}".format(ex), exitscript=True)

# ---------------------------------------------------------------------------
# 5. Report
# ---------------------------------------------------------------------------
folder = get_export_folder(doc)
csv_path = None
try:
    csv_path = os.path.join(folder, u"csv_to_parameter_results_{0}.csv".format(_stamp()))
    with io.open(csv_path, "w", encoding="utf-8", newline="") as f:
        f.write(u"Id,Element,{0},{1} before,{1} from CSV,Result\r\n".format(
            res["match"], res["set"]))
        def _c(s):
            s = u"{0}".format(s if s is not None else u"")
            return u'"{0}"'.format(s.replace(u'"', u'""')) if any(
                ch in s for ch in u',"\r\n') else s
        for r in done:
            f.write(u"{0},{1},{2},{3},{4},set\r\n".format(
                r["id"], _c(r["label"]), _c(r["key"]), _c(r["old"]), _c(r["new"])))
        for r, why in failed:
            f.write(u"{0},{1},{2},{3},{4},{5}\r\n".format(
                r["id"], _c(r["label"]), _c(r["key"]), _c(r["old"]), _c(r["new"]),
                _c(u"FAILED: {0}".format(why))))
except Exception:
    csv_path = None
record = None
try:
    record = write_preview(plan, res["lookup"], res["meta"], res["match"], res["set"],
                           True, stem="applied")
except Exception:
    record = None

c = plan["counts"]
output.print_md("### CSV to Parameter")
output.print_md("**Elements:** {0}  |  **Match:** '{1}' = column '{2}'  |  **Set:** '{3}' "
                "from column '{4}'  |  **CSV:** {5}".format(
                    res["category"], res["match"], res["key_col"], res["set"],
                    res["value_col"], os.path.basename(res["csv"])))
output.print_md("**Set:** {0}  |  **Failed:** {1}  |  **Already equal:** {2}  |  "
                "**Blank in CSV:** {3}  |  **No CSV row:** {4}  |  **Nothing to match on:** "
                "{5}  |  **CSV rows unused:** {6}".format(
                    len(done), len(failed), c.get("same", 0), c.get("blank", 0),
                    c.get("nomatch", 0), c.get("nokey", 0), len(plan["unused"])))
if done:
    output.print_table(
        table_data=[[output.linkify(make_id(r["id"])), r["label"], r["key"],
                     r["old"], r["new"]] for r in done[:300]],
        title="Set ({0}{1})".format(len(done), ", first 300 shown" if len(done) > 300 else ""),
        columns=["Element", "Type", res["match"], "Before", "Now"])
if failed:
    output.print_table(
        table_data=[[output.linkify(make_id(r["id"])), r["label"], r["key"],
                     r["new"], why] for r, why in failed],
        title="Failed ({0})".format(len(failed)),
        columns=["Element", "Type", res["match"], "Value", "Why"])
if plan["unused"]:
    output.print_md("**CSV rows without an element ({0}):** {1}{2}".format(
        len(plan["unused"]), u", ".join(plan["unused"][:40]),
        u" ..." if len(plan["unused"]) > 40 else u""))
if csv_path:
    output.print_md("Results CSV: `{0}`".format(csv_path))
if record:
    output.print_md("As-applied page: `{0}`".format(record))

forms.alert(u"{0} value(s) written to '{1}'{2}.\n\n{3} already equal, {4} without a "
            u"CSV row, {5} CSV row(s) unused.\n\nOne undo step reverts it.".format(
                len(done), res["set"],
                u", {0} FAILED (see the output window)".format(len(failed)) if failed else u"",
                c.get("same", 0), c.get("nomatch", 0), len(plan["unused"])),
            title="CSV to Parameter")
