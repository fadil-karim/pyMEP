# -*- coding: utf-8 -*-
"""Bulk Change - a spreadsheet of parameter values for a filtered set of
elements, where a block of cells in one column takes a new value.

1. Filter: category, then family and type (or all of them), optionally
   only what is selected in Revit.
2. Tick the parameters to show; each becomes a column next to the
   Element Id and Family : Type.
3. Select cells in ONE column the Excel way - click, Shift+click, drag,
   Ctrl+click - type the new value and press "Change selected cells"
   (or Enter). The values are written in one transaction, read back
   into the grid, and the dialog stays open for the next change.

Type parameters ([type]) are written once per type, which changes every
instance of it. Read-only and element-reference parameters can be shown
but not changed. Numbers are read in the parameter's project units.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

__title__ = "Bulk\nChange"
__author__ = "Glent Group"

import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
clr.AddReference("System.Data")
clr.AddReference("PresentationFramework")
import System
from Autodesk.Revit.DB import CategoryType, FilteredElementCollector, Transaction
from System.Data import DataTable
from System.Windows.Controls import (CheckBox, DataGridCellInfo, DataGridTextColumn,
                                     TextBlock)
from System.Windows.Data import Binding
from System.Windows.Input import Key

from pyrevit import revit, forms, script

import pymep_bulk_change as BC
import pymep_param_rw as PR
from pymep_config import load_settings, save_settings
from pymep_revit import id_value, make_id, quiet

output = script.get_output()
doc = revit.doc
uidoc = revit.uidoc

XAML_PATH = os.path.join(os.path.dirname(os.path.abspath(BC.__file__)),
                         "pymep_bulk_change.xaml")
S_CATEGORY = "bulk_change_category"
S_FAMILY = "bulk_change_family"
S_TYPE = "bulk_change_type"
S_PARAMS = "bulk_change_params"      # {category: [parameter keys]}
ID_COL = "id"
LABEL_COL = "label"


# ---------------------------------------------------------------------------
# 1. Every element that can carry parameters, with its family and type
# ---------------------------------------------------------------------------
rows = []
_types = {}
for el in FilteredElementCollector(doc).WhereElementIsNotElementType():
    try:
        cat = el.Category
        if cat is None:
            continue
        if cat.CategoryType != CategoryType.Model and cat.CategoryType != CategoryType.Annotation:
            continue
        if not cat.AllowsBoundParameters:
            continue
        cname = u"{0}".format(cat.Name)
    except Exception:
        continue
    tid = None
    fam = BC.NO_TYPE
    tname = BC.NO_TYPE
    try:
        t_id = el.GetTypeId()
        if t_id is not None and id_value(t_id) > 0:
            tid = id_value(t_id)
            if tid not in _types:
                typ = doc.GetElement(t_id)
                _types[tid] = (u"{0}".format(getattr(typ, "FamilyName", u"") or BC.NO_TYPE),
                               PR.element_name(typ) or BC.NO_TYPE) if typ is not None \
                    else (BC.NO_TYPE, BC.NO_TYPE)
            fam, tname = _types[tid]
    except Exception:
        tid = None
    rows.append({"id": id_value(el.Id), "category": cname, "family": fam, "type": tname,
                 "type_id": tid})
if not rows:
    forms.alert("No elements in this model carry parameters.", exitscript=True)
rows_by_id = dict((r["id"], r) for r in rows)

selected_ids = set()
try:
    selected_ids = set(id_value(i) for i in uidoc.Selection.GetElementIds())
    selected_ids = set(i for i in selected_ids if i in rows_by_id)
except Exception:
    selected_ids = set()

settings = load_settings()
_elements = {}


def element(eid):
    if eid not in _elements:
        _elements[eid] = doc.GetElement(make_id(eid)) if eid is not None else None
    return _elements[eid]


def _header(text):
    tb = TextBlock()
    tb.Text = text          # a TextBlock keeps underscores (a plain string loses them)
    return tb


# ---------------------------------------------------------------------------
# 2. The dialog
# ---------------------------------------------------------------------------
class BulkWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self._ready = False
        self._sync = 0
        self.cats = BC.categories(rows)
        self.params = []
        self.param_by_key = {}
        self.chosen = []            # parameter keys, in column order
        self.view_rows = []
        self.values = {}            # (row id, key) -> text, None when missing
        self._type_values = {}      # (type id, key) -> text, None when missing
        self.datarows = {}          # row id -> DataRow
        self.col_keys = {}          # column path -> parameter key
        self.log = []               # (label, owner id, old, new, result)
        self.changes = 0
        self.remembered = dict(settings.get(S_PARAMS) or {})
        self._loaded_cat = None
        self._sync += 1
        try:
            for name, n in self.cats:
                self.CmbCategory.Items.Add(u"{0}  ({1})".format(name, n))
            names = [c for c, _n in self.cats]
            want = settings.get(S_CATEGORY)
            if selected_ids:
                counts = {}
                for i in selected_ids:
                    c = rows_by_id[i]["category"]
                    counts[c] = counts.get(c, 0) + 1
                want = sorted(counts.items(), key=lambda kv: -kv[1])[0][0]
                self.ChkSelection.Content = u"only the {0} selected in Revit".format(
                    len(selected_ids))
                self.ChkSelection.IsChecked = True
            else:
                self.ChkSelection.IsEnabled = False
                self.ChkSelection.Content = u"selected in Revit only (nothing selected)"
            self.CmbCategory.SelectedIndex = names.index(want) if want in names else 0
        finally:
            self._sync -= 1
        self._ready = True
        # a run started from a Revit selection shows all of it, whatever
        # family / type filter was used last time
        self._load_category(first=not selected_ids)

    # -- the filter ---------------------------------------------------------------
    def category(self):
        idx = self.CmbCategory.SelectedIndex
        return self.cats[idx][0] if 0 <= idx < len(self.cats) else None

    def family(self):
        return u"{0}".format(self.CmbFamily.SelectedItem or BC.ALL_FAMILIES)

    def type_name(self):
        return u"{0}".format(self.CmbType.SelectedItem or BC.ALL_TYPES)

    def _fill(self, combo, items, want):
        self._sync += 1
        try:
            combo.Items.Clear()
            for it in items:
                combo.Items.Add(it)
            combo.SelectedIndex = items.index(want) if want in items else 0
        finally:
            self._sync -= 1

    def _load_category(self, first=False):
        cat = self.category()
        self._fill(self.CmbFamily, BC.family_options(rows, cat),
                   settings.get(S_FAMILY) if first else BC.ALL_FAMILIES)
        self._load_family(first)

    def _load_family(self, first=False):
        self._fill(self.CmbType, BC.type_options(rows, self.category(), self.family()),
                   settings.get(S_TYPE) if first else BC.ALL_TYPES)
        self._load_rows()

    def _load_rows(self):
        ids = selected_ids if (selected_ids and self.ChkSelection.IsChecked) else None
        self.view_rows = BC.filter_rows(rows, self.category(), self.family(),
                                        self.type_name(), ids)
        sample = BC.sample_rows(self.view_rows)
        els = [e for e in (element(r["id"]) for r in sample) if e is not None]
        type_ids = []
        for r in sample:
            if r["type_id"] is not None and r["type_id"] not in type_ids:
                type_ids.append(r["type_id"])
        types = [t for t in (element(i) for i in type_ids) if t is not None]
        self.params = BC.build_params(PR.param_catalog(els), PR.param_catalog(types))
        self.param_by_key = dict((p["key"], p) for p in self.params)
        self._loaded_cat = self.category()
        remembered = self.remembered.get(self.category())
        keep = [k for k in self.chosen if k in self.param_by_key]
        if not keep:
            keep = [k for k in (remembered or []) if k in self.param_by_key]
        if not keep:
            keep = BC.default_keys(self.params)
        self.chosen = keep
        self.TxtCount.Text = u"{0} element(s); {1} parameter(s) found on them.".format(
            len(self.view_rows), len(self.params))
        self._fill_params()
        self._build_grid()

    # -- the parameter list -------------------------------------------------------
    def _fill_params(self):
        self._sync += 1
        try:
            self.LstParams.Items.Clear()
            for p in BC.search_params(self.params, self.TxtParamSearch.Text):
                cb = CheckBox()
                label = p["label"] if p["editable"] else u"{0}  (read-only)".format(p["label"])
                cb.Content = _header(label)
                cb.Tag = p["key"]
                cb.IsChecked = p["key"] in self.chosen
                cb.Checked += self.on_param_toggled
                cb.Unchecked += self.on_param_toggled
                self.LstParams.Items.Add(cb)
        finally:
            self._sync -= 1

    def on_param_toggled(self, sender, args):
        if self._sync:
            return
        key = u"{0}".format(sender.Tag)
        if sender.IsChecked and key not in self.chosen:
            self.chosen.append(key)
        elif not sender.IsChecked and key in self.chosen:
            self.chosen.remove(key)
        self._remember()
        self._build_grid()

    def _remember(self):
        if self._loaded_cat:
            self.remembered[self._loaded_cat] = list(self.chosen)

    # -- reading values -----------------------------------------------------------
    def value(self, rid, key):
        if (rid, key) in self.values:
            return self.values[(rid, key)]
        p = self.param_by_key.get(key)
        row = rows_by_id.get(rid)
        text = None
        if p is not None and row is not None:
            if p["scope"] == BC.TYPE:
                tkey = (row["type_id"], key)
                if tkey not in self._type_values:
                    self._type_values[tkey] = self._read(element(row["type_id"]), p["name"])
                text = self._type_values[tkey]
            else:
                text = self._read(element(rid), p["name"])
        self.values[(rid, key)] = text
        return text

    def _read(self, el, name):
        if el is None:
            return None
        try:
            prm = el.LookupParameter(name)
        except Exception:
            prm = None
        return PR.as_text(prm, doc) if prm is not None else None

    # -- the grid -----------------------------------------------------------------
    def _build_grid(self):
        table = DataTable()
        table.Columns.Add(ID_COL, clr.GetClrType(System.Int64))
        table.Columns.Add(LABEL_COL, clr.GetClrType(System.String))
        self.col_keys = {}
        for i, key in enumerate(self.chosen):
            path = u"p{0}".format(i)
            table.Columns.Add(path, clr.GetClrType(System.String))
            self.col_keys[path] = key
        self.datarows = {}
        for r in self.view_rows:
            dr = table.NewRow()
            dr[ID_COL] = System.Int64(r["id"])
            dr[LABEL_COL] = u"{0} : {1}".format(r["family"], r["type"])
            for path, key in self.col_keys.items():
                v = self.value(r["id"], key)
                dr[path] = v if v is not None else u""
            table.Rows.Add(dr)
            self.datarows[r["id"]] = dr
        self._sync += 1
        try:
            self.GridValues.ItemsSource = None
            self.GridValues.Columns.Clear()
            for path, head in ((ID_COL, u"Element Id"), (LABEL_COL, u"Family : Type")):
                col = DataGridTextColumn()
                col.Header = _header(head)
                col.Binding = Binding(path)
                col.SortMemberPath = path
                self.GridValues.Columns.Add(col)
            for i, key in enumerate(self.chosen):
                path = u"p{0}".format(i)
                p = self.param_by_key[key]
                col = DataGridTextColumn()
                col.Header = _header(p["label"] if p["editable"] else
                                     u"{0} (read-only)".format(p["label"]))
                col.Binding = Binding(path)
                col.SortMemberPath = path
                self.GridValues.Columns.Add(col)
            self.GridValues.ItemsSource = table.DefaultView
        finally:
            self._sync -= 1
        self._status()

    # -- the selection ------------------------------------------------------------
    def selected_cells(self):
        cells = []
        for ci in self.GridValues.SelectedCells:
            try:
                path = u"{0}".format(ci.Column.SortMemberPath)
                rid = int(ci.Item.Row[ID_COL])
            except Exception:
                continue
            cells.append((rid, self.col_keys.get(path)))
        return cells

    def plan(self):
        """(plan, param, error)"""
        cells = self.selected_cells()
        key, why = BC.selection_column(cells)
        if key is None:
            return None, None, why
        param = self.param_by_key.get(key)
        if param is None:
            return None, None, u"Unknown column."
        values = dict(((rid, k), self.value(rid, k)) for rid, k in cells)
        return BC.plan_change(cells, rows_by_id, param, self.TxtValue.Text, values), param, u""

    def _status(self):
        if not getattr(self, "_ready", False) or self._sync:
            return
        plan, param, err = self.plan()
        if plan is None:
            self.StatusText.Text = err
            self.BtnChange.IsEnabled = False
            return
        rows_hit = None
        if param["scope"] == BC.TYPE:
            owners = set(t["owner"] for t in plan["targets"])
            rows_hit = len([r for r in self.view_rows if r["type_id"] in owners])
        self.StatusText.Text = BC.plan_text(plan, param, rows_hit)
        self.BtnChange.IsEnabled = bool(plan["targets"])

    # -- handlers -------------------------------------------------------------------
    def on_category_changed(self, sender, args):
        if self._ready and not self._sync:
            self._remember()
            self.chosen = []
            self._load_category()

    def on_family_changed(self, sender, args):
        if self._ready and not self._sync:
            self._load_family()

    def on_type_changed(self, sender, args):
        if self._ready and not self._sync:
            self._load_rows()

    def on_scope_changed(self, sender, args):
        if self._ready and not self._sync:
            self._load_rows()

    def on_param_search(self, sender, args):
        if self._ready and not self._sync:
            self._fill_params()

    def on_cells_changed(self, sender, args):
        self._status()

    def on_value_changed(self, sender, args):
        self._status()

    def on_value_key(self, sender, args):
        if args.Key == Key.Enter or args.Key == Key.Return:
            args.Handled = True
            self.on_change(sender, args)

    def on_select_column(self, sender, args):
        col = None
        try:
            if self.GridValues.CurrentCell.IsValid:
                col = self.GridValues.CurrentCell.Column
        except Exception:
            col = None
        if col is None:
            for ci in self.GridValues.SelectedCells:
                col = ci.Column
                break
        if col is None:
            self.StatusText.Text = u"Click a cell in the column first."
            return
        self._sync += 1
        try:
            self.GridValues.SelectedCells.Clear()
            for item in self.GridValues.Items:
                self.GridValues.SelectedCells.Add(DataGridCellInfo(item, col))
        finally:
            self._sync -= 1
        self._status()

    def on_change(self, sender, args):
        plan, param, err = self.plan()
        if plan is None:
            self.StatusText.Text = err
            return
        if not plan["targets"]:
            self.StatusText.Text = BC.plan_text(plan, param) + u" Nothing to change."
            return
        done = []
        failed = []
        t = Transaction(doc, u"pyMEP: Bulk change '{0}' ({1})".format(
            param["label"], len(plan["targets"])))
        quiet(t)
        t.Start()
        try:
            for tg in plan["targets"]:
                owner = element(tg["owner"])
                prm = None
                try:
                    prm = owner.LookupParameter(param["name"]) if owner is not None else None
                except Exception:
                    prm = None
                if prm is None:
                    failed.append((tg, u"parameter not found"))
                    continue
                if prm.IsReadOnly:
                    failed.append((tg, u"read-only on this element"))
                    continue
                ok, why = PR.write_param(prm, param, plan["new"])
                if ok:
                    done.append(tg)
                else:
                    failed.append((tg, why))
            t.Commit()
        except Exception as ex:
            try:
                t.RollBack()
            except Exception:
                pass
            self.StatusText.Text = u"Failed, nothing changed: {0}".format(ex)
            return
        self._refresh_after(param, done + [tg for tg, _w in failed])
        for tg in done:
            self.log.append((param["label"], tg["owner"], tg["old"],
                             self.values.get((tg["rows"][0], param["key"])), u"changed"))
        for tg, why in failed:
            self.log.append((param["label"], tg["owner"], tg["old"], plan["new"],
                             u"FAILED: {0}".format(why)))
        self.changes += len(done)
        what = u"type(s)" if param["scope"] == BC.TYPE else u"element(s)"
        msg = u"Changed '{0}' on {1} {2}.".format(param["label"], len(done), what)
        if failed:
            msg += u" {0} failed - {1}.".format(len(failed), failed[0][1])
        self.StatusText.Text = msg
        self.TxtLog.Text = u"{0} value(s) changed this session - Ctrl+Z in Revit undoes " \
                           u"one Change at a time.".format(self.changes)

    def _refresh_after(self, param, targets):
        """Read the written values back into the grid."""
        key = param["key"]
        path = None
        for pth, k in self.col_keys.items():
            if k == key:
                path = pth
        if param["scope"] == BC.TYPE:
            owners = set(tg["owner"] for tg in targets)
            for o in owners:
                self._type_values.pop((o, key), None)
            rids = [r["id"] for r in self.view_rows if r["type_id"] in owners]
        else:
            rids = [rid for tg in targets for rid in tg["rows"]]
        for rid in rids:
            self.values.pop((rid, key), None)
            v = self.value(rid, key)
            if path is not None and rid in self.datarows:
                self.datarows[rid][path] = v if v is not None else u""
        self._status()

    def on_close(self, sender, args):
        try:
            settings[S_CATEGORY] = self.category()
            settings[S_FAMILY] = self.family()
            settings[S_TYPE] = self.type_name()
            self._remember()
            settings[S_PARAMS] = self.remembered
            save_settings(settings)
        except Exception:
            pass
        self.Close()


win = BulkWindow()
win.ShowDialog()

if win.log:
    output.print_md("### Bulk Change - {0} value(s) changed".format(win.changes))
    output.print_table(
        table_data=[[output.linkify(make_id(owner)), label, old if old is not None else u"",
                     new if new is not None else u"", result]
                    for label, owner, old, new, result in win.log],
        columns=["Element / type", "Parameter", "Before", "After", "Result"])
