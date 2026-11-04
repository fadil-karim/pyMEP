# -*- coding: utf-8 -*-
"""Bulk Change - a spreadsheet of parameter values for a set of elements,
where a block of cells in one column takes a new value.

1. Source: an existing schedule (the main way) - its elements become the
   rows and its parameter fields the columns, in schedule order and
   with the schedule's headings - or a filter by category, then family
   and type. Either can keep to what is selected in Revit.
2. Tick more parameters to add columns next to the Element Id and
   Family : Type.
3. Sort by clicking a column header (numbers by value, P2 before P10)
   and narrow the rows with column filters - contains, equals, greater
   than, is blank and so on, or "Filter to selected values".
4. Select cells in ONE column the Excel way - click, Shift+click, drag,
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
import System
from Autodesk.Revit.DB import (BuiltInParameter, CategoryType, FilteredElementCollector,
                               Transaction, ViewSchedule)
from System.Data import DataTable

from pyrevit import revit, forms, script

# WPF types come after pyrevit (whose forms module loads WPF into this
# engine), with every WPF assembly referenced explicitly as well:
# Thickness / controls / Binding live in PresentationFramework, Key in
# WindowsBase.
clr.AddReference("PresentationCore")
clr.AddReference("PresentationFramework")
clr.AddReference("WindowsBase")
from System.Windows import Thickness
from System.Windows.Controls import (Button, CheckBox, DataGridCellInfo, DataGridTextColumn,
                                     TextBlock)
from System.Windows.Data import Binding
from System.Windows.Input import Key

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
S_PARAMS = "bulk_change_params"      # {category or "schedule:<name>": [parameter keys]}
S_SCHEDULE = "bulk_change_schedule"
ID_COL = "id"
LABEL_COL = "label"
LABEL_SORT = "slabel"
SHOW_COL = "show"
FIXED = {ID_COL: BC.ID_KEY, LABEL_SORT: BC.LABEL_KEY, LABEL_COL: BC.LABEL_KEY}


# ---------------------------------------------------------------------------
# 1. Rows (one per element, with category, family and type) and schedules
# ---------------------------------------------------------------------------
rows_by_id = {}
_types = {}


def make_row(el):
    """The row for an element, made once and kept by element id."""
    try:
        eid = id_value(el.Id)
    except Exception:
        return None
    if eid in rows_by_id:
        return rows_by_id[eid]
    try:
        cat = el.Category
        cname = u"{0}".format(cat.Name) if cat is not None else u"(no category)"
    except Exception:
        cname = u"(no category)"
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
    row = {"id": eid, "category": cname, "family": fam, "type": tname, "type_id": tid}
    rows_by_id[eid] = row
    return row


_all_rows = []


def all_rows():
    """Every element that can carry parameters - collected the first time
    the filter is used; a schedule only needs its own elements."""
    if not _all_rows:
        for el in FilteredElementCollector(doc).WhereElementIsNotElementType():
            try:
                cat = el.Category
                if cat is None:
                    continue
                if (cat.CategoryType != CategoryType.Model
                        and cat.CategoryType != CategoryType.Annotation):
                    continue
                if not cat.AllowsBoundParameters:
                    continue
            except Exception:
                continue
            r = make_row(el)
            if r is not None:
                _all_rows.append(r)
    return _all_rows


def collect_schedules():
    """The schedules that list elements - not templates, key schedules,
    title-block revision or keynote legends."""
    out = []
    for sch in FilteredElementCollector(doc).OfClass(ViewSchedule):
        try:
            if sch.IsTemplate:
                continue
            if getattr(sch, "IsTitleblockRevisionSchedule", False):
                continue
            if getattr(sch, "IsInternalKeynoteSchedule", False):
                continue
            if sch.Definition.IsKeySchedule:
                continue
            out.append(sch)
        except Exception:
            continue
    out.sort(key=lambda v: u"{0}".format(v.Name).lower())
    return out


schedules = collect_schedules()
_sources = {}


def schedule_source(sch):
    """(rows, fields) of a schedule: the elements it lists and its fields
    in schedule order."""
    sid = id_value(sch.Id)
    if sid in _sources:
        return _sources[sid]
    srows = []
    for el in FilteredElementCollector(doc, sch.Id).WhereElementIsNotElementType():
        r = make_row(el)
        if r is not None:
            srows.append(r)
    fields = []
    d = sch.Definition
    for fid in d.GetFieldOrder():
        try:
            f = d.GetField(fid)
            fields.append({"name": u"{0}".format(f.GetName()),
                           "heading": u"{0}".format(f.ColumnHeading or u""),
                           "kind": u"{0}".format(f.FieldType),
                           "hidden": bool(f.IsHidden),
                           "pid": id_value(f.ParameterId)})
        except Exception:
            continue
    _sources[sid] = (srows, fields)
    return _sources[sid]


selected_ids = set()
try:
    selected_ids = set(id_value(i) for i in uidoc.Selection.GetElementIds())
except Exception:
    selected_ids = set()
active_schedule = None
try:
    if isinstance(doc.ActiveView, ViewSchedule):
        active_schedule = id_value(doc.ActiveView.Id)
except Exception:
    active_schedule = None

settings = load_settings()
_elements = {}
_guids = {}


def element(eid):
    if eid not in _elements:
        _elements[eid] = doc.GetElement(make_id(eid)) if eid is not None else None
    return _elements[eid]


def find_param(el, p):
    """The Parameter p names on el: by its built-in id or shared GUID when
    the schedule gave one (so a renamed heading or a duplicate name is no
    problem), else by name."""
    if el is None:
        return None
    pid = p.get("pid")
    try:
        if pid is not None and pid < -1:
            prm = el.get_Parameter(System.Enum.ToObject(clr.GetClrType(BuiltInParameter), int(pid)))
            if prm is not None:
                return prm
        elif pid is not None and pid > 0:
            if pid not in _guids:
                _guids[pid] = getattr(doc.GetElement(make_id(pid)), "GuidValue", None)
            if _guids[pid] is not None:
                prm = el.get_Parameter(_guids[pid])
                if prm is not None:
                    return prm
    except Exception:
        pass
    try:
        return el.LookupParameter(p["name"])
    except Exception:
        return None


def probe(usable, sample):
    """{key: {storage, readonly, yesno}} for schedule fields, read from the
    schedule's own elements (or their types) - writable on any counts."""
    info = {}
    type_ids = []
    for r in sample:
        if r["type_id"] is not None and r["type_id"] not in type_ids:
            type_ids.append(r["type_id"])
    inst = [element(r["id"]) for r in sample[:80]]
    types = [element(t) for t in type_ids[:80]]
    for f in usable:
        found = None
        for el in (types if f["scope"] == BC.TYPE else inst):
            prm = find_param(el, f)
            if prm is None:
                continue
            try:
                st = PR.storage_name(prm.StorageType)
            except Exception:
                continue
            if st == "None":
                continue
            if found is None:
                found = {"storage": st, "readonly": True,
                         "yesno": st == "Integer" and PR.is_yesno(prm)}
            if not prm.IsReadOnly:
                found["readonly"] = False
                break
        if found is not None:
            info[f["key"]] = found
    return info


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
        self.cats = None            # filter categories, read on first use
        self.mode = None            # "schedule" or "filter"
        self.params = []
        self.param_by_key = {}
        self.chosen = []            # parameter keys, in column order
        self.view_rows = []
        self.values = {}            # (row id, key) -> text, None when missing
        self._type_values = {}      # (type id, key) -> text, None when missing
        self.datarows = {}          # row id -> DataRow
        self.col_keys = {}          # column path -> parameter key
        self.paths = {}             # parameter key -> (value path, sort path)
        self.table = None
        self.filters = []           # BC.make_filter dicts, all must match
        self.filter_cols = []       # [(key, label)] offered in the filter bar
        self.log = []               # (label, owner id, old, new, result)
        self.changes = 0
        self.remembered = dict(settings.get(S_PARAMS) or {})
        self._remember_key = None
        self.source_text = u""
        self.source_note = u""
        self._sync += 1
        try:
            for sch in schedules:
                self.CmbSchedule.Items.Add(u"{0}".format(sch.Name))
            if schedules:
                ids = [id_value(v.Id) for v in schedules]
                names = [u"{0}".format(v.Name) for v in schedules]
                want = settings.get(S_SCHEDULE)
                if active_schedule in ids:
                    self.CmbSchedule.SelectedIndex = ids.index(active_schedule)
                elif want in names:
                    self.CmbSchedule.SelectedIndex = names.index(want)
                else:
                    self.CmbSchedule.SelectedIndex = 0
            else:
                self.RbSchedule.IsEnabled = False
                self.RbSchedule.Content = u"Schedule (none in this model)"
                self.CmbSchedule.IsEnabled = False
            if selected_ids:
                self.ChkSelection.Content = u"only the {0} selected in Revit".format(
                    len(selected_ids))
                self.ChkSelection.IsChecked = True
            else:
                self.ChkSelection.IsEnabled = False
                self.ChkSelection.Content = u"selected in Revit only (nothing selected)"
            for _op, lbl in BC.OPS:
                self.CmbFilterOp.Items.Add(lbl)
            self.CmbFilterOp.SelectedIndex = 0
            # a schedule is the main way in; a selection made in a model
            # view (not in a schedule) is filtered instead
            use_schedule = bool(schedules) and (active_schedule is not None or not selected_ids)
            if use_schedule:
                self.RbSchedule.IsChecked = True
            else:
                self.RbFilter.IsChecked = True
        finally:
            self._sync -= 1
        self._ready = True
        self._set_mode("schedule" if use_schedule else "filter", first=True)

    # -- the source -----------------------------------------------------------------
    def schedule(self):
        idx = self.CmbSchedule.SelectedIndex
        return schedules[idx] if 0 <= idx < len(schedules) else None

    def _selection(self):
        return selected_ids if (selected_ids and self.ChkSelection.IsChecked) else None

    def _set_mode(self, mode, first=False):
        self.mode = mode
        is_filter = mode == "filter"
        for c in (self.CmbCategory, self.CmbFamily, self.CmbType,
                  self.LblCategory, self.LblFamily, self.LblType):
            c.IsEnabled = is_filter
        self.CmbSchedule.IsEnabled = (not is_filter) and bool(schedules)
        self._remember()
        self.chosen = []
        if is_filter:
            self._ensure_categories()
            self._load_category(first=first)
        else:
            self._load_schedule()

    def _ensure_categories(self):
        if self.cats is not None:
            return
        rows = all_rows()
        self.cats = BC.categories(rows)
        self._sync += 1
        try:
            self.CmbCategory.Items.Clear()
            for name, n in self.cats:
                self.CmbCategory.Items.Add(u"{0}  ({1})".format(name, n))
            names = [c for c, _n in self.cats]
            want = settings.get(S_CATEGORY)
            if selected_ids:
                counts = {}
                for i in selected_ids:
                    r = rows_by_id.get(i)
                    if r is not None:
                        counts[r["category"]] = counts.get(r["category"], 0) + 1
                if counts:
                    want = sorted(counts.items(), key=lambda kv: -kv[1])[0][0]
            self.CmbCategory.SelectedIndex = names.index(want) if want in names else 0
        finally:
            self._sync -= 1

    def _load_schedule(self):
        sch = self.schedule()
        if sch is None:
            self._set_source([], [], None, [], u"No schedule picked", u"")
            return
        srows, fields = schedule_source(sch)
        ids = self._selection()
        view_rows = [r for r in srows if ids is None or r["id"] in ids]
        usable, skipped = BC.schedule_fields(fields)
        sample = BC.sample_rows(view_rows)
        sparams = BC.schedule_params(usable, probe(usable, sample))
        params = BC.merge_params(sparams, self._catalog(sample))
        note = u""
        if skipped:
            note = u" Schedule fields that are not parameters (count, formulas, room ...) " \
                   u"are left out: {0}{1}.".format(u", ".join(h for h, _w in skipped[:6]),
                                                 u" ..." if len(skipped) > 6 else u"")
        self._set_source(view_rows, params, u"schedule:{0}".format(sch.Name),
                         [f["key"] for f in usable if not f["hidden"]],
                         u"Schedule '{0}'".format(sch.Name), note)

    def _catalog(self, sample):
        els = [e for e in (element(r["id"]) for r in sample) if e is not None]
        type_ids = []
        for r in sample:
            if r["type_id"] is not None and r["type_id"] not in type_ids:
                type_ids.append(r["type_id"])
        types = [t for t in (element(i) for i in type_ids) if t is not None]
        return BC.build_params(PR.param_catalog(els), PR.param_catalog(types))

    def _set_source(self, view_rows, params, remember_key, default_chosen, text, note,
                    keep_chosen=False):
        self.view_rows = view_rows
        self.params = params
        self.param_by_key = dict((p["key"], p) for p in self.params)
        keep = [k for k in self.chosen if k in self.param_by_key] if keep_chosen else []
        if not keep:
            keep = [k for k in (self.remembered.get(remember_key) or [])
                    if k in self.param_by_key]
        if not keep:
            keep = [k for k in default_chosen if k in self.param_by_key]
        if not keep:
            keep = BC.default_keys(self.params)
        self._remember_key = remember_key
        self.chosen = keep
        self.source_text = text
        self.source_note = note
        self._fill_params()
        self._build_grid()

    # -- the filter (optional way in) -------------------------------------------------
    def category(self):
        idx = self.CmbCategory.SelectedIndex
        return self.cats[idx][0] if self.cats and 0 <= idx < len(self.cats) else None

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
        self._fill(self.CmbFamily, BC.family_options(all_rows(), cat),
                   settings.get(S_FAMILY) if (first and not selected_ids) else BC.ALL_FAMILIES)
        self._load_family(first)

    def _load_family(self, first=False):
        self._fill(self.CmbType, BC.type_options(all_rows(), self.category(), self.family()),
                   settings.get(S_TYPE) if (first and not selected_ids) else BC.ALL_TYPES)
        self._load_rows(keep_chosen=not first)

    def _load_rows(self, keep_chosen=True):
        view_rows = BC.filter_rows(all_rows(), self.category(), self.family(),
                                   self.type_name(), self._selection())
        sample = BC.sample_rows(view_rows)
        self._set_source(view_rows, self._catalog(sample), self.category(), [],
                         u"{0}".format(self.category() or u"(no category)"), u"",
                         keep_chosen=keep_chosen)

    # -- the parameter list -------------------------------------------------------
    def _fill_params(self):
        self._sync += 1
        try:
            self.LstParams.Items.Clear()
            for p in BC.search_params(self.params, self.TxtParamSearch.Text):
                cb = CheckBox()
                cb.Content = _header(BC.column_header(p))
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
        if self._remember_key:
            self.remembered[self._remember_key] = list(self.chosen)

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
                    self._type_values[tkey] = self._read(element(row["type_id"]), p)
                text = self._type_values[tkey]
            else:
                text = self._read(element(rid), p)
        self.values[(rid, key)] = text
        return text

    def _read(self, el, p):
        prm = find_param(el, p)
        return PR.as_text(prm, doc) if prm is not None else None

    # -- the grid -----------------------------------------------------------------
    def cell(self, row, key):
        """A cell's text for filtering - the fixed columns included."""
        if key == BC.ID_KEY:
            return u"{0}".format(row["id"])
        if key == BC.LABEL_KEY:
            return u"{0} : {1}".format(row["family"], row["type"])
        return self.value(row["id"], key)

    def _build_grid(self):
        # a filter on a column no longer shown goes with it
        self.filters = [f for f in self.filters
                        if f["key"] in (BC.ID_KEY, BC.LABEL_KEY) or f["key"] in self.chosen]
        table = DataTable()
        table.Columns.Add(ID_COL, clr.GetClrType(System.Int64))
        table.Columns.Add(LABEL_COL, clr.GetClrType(System.String))
        table.Columns.Add(LABEL_SORT, clr.GetClrType(System.String))
        table.Columns.Add(SHOW_COL, clr.GetClrType(System.Boolean))
        self.col_keys = dict(FIXED)
        self.paths = {}
        for i, key in enumerate(self.chosen):
            vpath = u"p{0}".format(i)
            spath = u"s{0}".format(i)       # sorts by value, not by text
            table.Columns.Add(vpath, clr.GetClrType(System.String))
            table.Columns.Add(spath, clr.GetClrType(System.String))
            self.col_keys[vpath] = key
            self.col_keys[spath] = key
            self.paths[key] = (vpath, spath)
        visible = self._visible_ids()
        self.datarows = {}
        for r in self.view_rows:
            dr = table.NewRow()
            label = u"{0} : {1}".format(r["family"], r["type"])
            dr[ID_COL] = System.Int64(r["id"])
            dr[LABEL_COL] = label
            dr[LABEL_SORT] = BC.sort_key(label)
            dr[SHOW_COL] = r["id"] in visible
            for key, (vpath, spath) in self.paths.items():
                v = self.value(r["id"], key)
                dr[vpath] = v if v is not None else u""
                dr[spath] = BC.sort_key(v)
            table.Rows.Add(dr)
            self.datarows[r["id"]] = dr
        table.DefaultView.RowFilter = u"{0} = true".format(SHOW_COL)
        self.table = table
        self._sync += 1
        try:
            self.GridValues.ItemsSource = None
            self.GridValues.Columns.Clear()
            for vpath, spath, head in ((ID_COL, ID_COL, u"Element Id"),
                                       (LABEL_COL, LABEL_SORT, u"Family : Type")):
                col = DataGridTextColumn()
                col.Header = _header(head)
                col.Binding = Binding(vpath)
                col.SortMemberPath = spath
                self.GridValues.Columns.Add(col)
            for key in self.chosen:
                vpath, spath = self.paths[key]
                p = self.param_by_key[key]
                col = DataGridTextColumn()
                col.Header = _header(BC.column_header(p))
                col.Binding = Binding(vpath)
                col.SortMemberPath = spath
                self.GridValues.Columns.Add(col)
            self.GridValues.ItemsSource = table.DefaultView
        finally:
            self._sync -= 1
        self._fill_filter_cols()
        self._show_filters(len(visible))
        self._status()

    # -- filters --------------------------------------------------------------------
    def _visible_ids(self):
        return set(r["id"] for r in BC.apply_filters(self.view_rows, self.cell, self.filters))

    def _fill_filter_cols(self):
        keep = None
        idx = self.CmbFilterCol.SelectedIndex
        if 0 <= idx < len(self.filter_cols):
            keep = self.filter_cols[idx][0]
        self.filter_cols = [(BC.ID_KEY, u"Element Id"), (BC.LABEL_KEY, u"Family : Type")] + [
            (k, self.param_by_key[k]["label"]) for k in self.chosen]
        self._sync += 1
        try:
            self.CmbFilterCol.Items.Clear()
            for _k, lbl in self.filter_cols:
                self.CmbFilterCol.Items.Add(lbl)
            keys = [k for k, _l in self.filter_cols]
            self.CmbFilterCol.SelectedIndex = (keys.index(keep) if keep in keys
                                               else (2 if len(keys) > 2 else 1))
        finally:
            self._sync -= 1

    def _column_label(self, key):
        for k, lbl in self.filter_cols:
            if k == key:
                return lbl
        p = self.param_by_key.get(key)
        return p["label"] if p else key

    def _show_filters(self, shown):
        self.PnlFilters.Children.Clear()
        if not self.filters:
            tb = TextBlock()
            tb.Text = u"No filters - every element above is listed."
            try:
                tb.Style = self.FindResource("Hint")
            except Exception:
                pass
            self.PnlFilters.Children.Add(tb)
        for i, f in enumerate(self.filters):
            b = Button()
            b.Content = _header(u"{0}   \u00d7".format(BC.filter_label(f, self._column_label(f["key"]))))
            b.Tag = i
            b.Margin = Thickness(0, 0, 6, 4)
            b.Padding = Thickness(8, 2, 8, 2)
            b.ToolTip = u"Remove this filter"
            b.Click += self.on_remove_filter
            self.PnlFilters.Children.Add(b)
        self.TxtCount.Text = u"{0}: {1} element(s){2}; {3} parameter(s) to choose from.{4}".format(
            self.source_text, len(self.view_rows),
            u", {0} shown by the filters".format(shown) if self.filters else u"",
            len(self.params), self.source_note)

    def _apply_filters(self):
        if self.table is None:
            return
        visible = self._visible_ids()
        self._sync += 1
        try:
            self.GridValues.SelectedCells.Clear()
            view = self.table.DefaultView
            view.RowFilter = u""
            for rid, dr in self.datarows.items():
                want = rid in visible
                if bool(dr[SHOW_COL]) != want:
                    dr[SHOW_COL] = want
            view.RowFilter = u"{0} = true".format(SHOW_COL)
        finally:
            self._sync -= 1
        self._show_filters(len(visible))
        self._status()

    # -- the selection ------------------------------------------------------------
    def selected_cells(self, any_column=False):
        """[(row id, key)] for the selected cells. The fixed Element Id
        and Family : Type columns come back as None unless any_column
        (filtering may use them, changing may not)."""
        cells = []
        for ci in self.GridValues.SelectedCells:
            try:
                path = u"{0}".format(ci.Column.SortMemberPath)
                rid = int(ci.Item.Row[ID_COL])
            except Exception:
                continue
            key = self.col_keys.get(path)
            if key in (BC.ID_KEY, BC.LABEL_KEY) and not any_column:
                key = None
            cells.append((rid, key))
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
    def on_mode_changed(self, sender, args):
        if not self._ready or self._sync:
            return
        mode = "schedule" if self.RbSchedule.IsChecked else "filter"
        if mode != self.mode:
            self._set_mode(mode)

    def on_schedule_changed(self, sender, args):
        if self._ready and not self._sync and self.mode == "schedule":
            self._remember()
            self.chosen = []
            self._load_schedule()

    def on_category_changed(self, sender, args):
        if self._ready and not self._sync and self.mode == "filter":
            self._remember()
            self.chosen = []
            self._load_category()

    def on_family_changed(self, sender, args):
        if self._ready and not self._sync and self.mode == "filter":
            self._load_family()

    def on_type_changed(self, sender, args):
        if self._ready and not self._sync and self.mode == "filter":
            self._load_rows()

    def on_scope_changed(self, sender, args):
        if not self._ready or self._sync:
            return
        self._remember()
        if self.mode == "schedule":
            self._load_schedule()
        else:
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

    def on_filter_op_changed(self, sender, args):
        idx = self.CmbFilterOp.SelectedIndex
        op = BC.OPS[idx][0] if 0 <= idx < len(BC.OPS) else "contains"
        self.TxtFilterValue.IsEnabled = op not in BC.VALUELESS_OPS

    def on_filter_value_key(self, sender, args):
        if args.Key == Key.Enter or args.Key == Key.Return:
            args.Handled = True
            self.on_add_filter(sender, args)

    def on_add_filter(self, sender, args):
        idx = self.CmbFilterCol.SelectedIndex
        oidx = self.CmbFilterOp.SelectedIndex
        if not (0 <= idx < len(self.filter_cols)) or not (0 <= oidx < len(BC.OPS)):
            self.StatusText.Text = u"Pick the column and the condition to filter on."
            return
        try:
            f = BC.make_filter(self.filter_cols[idx][0], BC.OPS[oidx][0], self.TxtFilterValue.Text)
        except ValueError as ex:
            self.StatusText.Text = u"Filter not added - {0}.".format(ex)
            return
        self.filters.append(f)
        self.TxtFilterValue.Text = u""
        self._apply_filters()

    def on_filter_selected(self, sender, args):
        cells = self.selected_cells(any_column=True)
        keys = set(k for _r, k in cells)
        if not cells:
            self.StatusText.Text = u"Select the cells whose values to keep, then press this."
            return
        if len(keys) != 1:
            self.StatusText.Text = u"Select cells in one column to filter on their values."
            return
        key = list(keys)[0]
        vals = []
        blank = False
        for rid, _k in cells:
            v = self.cell(rows_by_id[rid], key)
            if v is None or not u"{0}".format(v).strip():
                blank = True
            elif v not in vals:
                vals.append(v)
        try:
            if vals:
                f = BC.make_filter(key, "equals", values=vals)
            else:
                f = BC.make_filter(key, "blank")
        except ValueError as ex:
            self.StatusText.Text = u"Filter not added - {0}.".format(ex)
            return
        self.filters.append(f)
        self._apply_filters()
        if blank and vals:
            self.StatusText.Text = u"Blank cells were left out of that filter - add 'is blank' " \
                                   u"separately if you want them."

    def on_clear_filters(self, sender, args):
        if self.filters:
            self.filters = []
            self._apply_filters()

    def on_remove_filter(self, sender, args):
        try:
            idx = int(sender.Tag)
        except Exception:
            return
        if 0 <= idx < len(self.filters):
            self.filters.pop(idx)
            self._apply_filters()

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
                    prm = find_param(owner, param)
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
        vpath, spath = self.paths.get(key, (None, None))
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
            if vpath is not None and rid in self.datarows:
                self.datarows[rid][vpath] = v if v is not None else u""
                self.datarows[rid][spath] = BC.sort_key(v)
        self._status()

    def on_close(self, sender, args):
        try:
            sch = self.schedule()
            if sch is not None:
                settings[S_SCHEDULE] = u"{0}".format(sch.Name)
            if self.cats is not None:
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
