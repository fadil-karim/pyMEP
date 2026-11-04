# -*- coding: utf-8 -*-
"""Bulk Change - the pure part (no Revit): the category / family / type
filter, the parameter keys and search, the one-column check on the
selected cells, and the plan of what a new value would change.

The Revit side (pyMEP.tab/05_Parameters.panel/BulkChange) lists the
elements as rows, reads the chosen parameters into a grid, and when
cells in one column are selected and a value typed, asks plan_change
which elements (or types) to write.

Runs under IronPython 2.7 and CPython 3: .format() only, LF endings.
"""

import pymep_csv_params as CP

ALL_FAMILIES = u"(all families)"
ALL_TYPES = u"(all types)"
NO_TYPE = u"(no type)"
INSTANCE = "i"
TYPE = "t"


def _low(s):
    return u"{0}".format(s).lower()


# ---------------------------------------------------------------------------
# 1. The filter
# ---------------------------------------------------------------------------


def categories(rows):
    """[(category, count)], most elements first."""
    counts = {}
    for r in rows:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], _low(kv[0])))


def family_options(rows, category):
    fams = set(r["family"] for r in rows if r["category"] == category)
    return [ALL_FAMILIES] + sorted(fams, key=_low)


def type_options(rows, category, family=None):
    types = set(r["type"] for r in rows if r["category"] == category
                and (family in (None, u"", ALL_FAMILIES) or r["family"] == family))
    return [ALL_TYPES] + sorted(types, key=_low)


def filter_rows(rows, category, family=None, type_name=None, ids=None):
    """The rows of one category, narrowed to a family and a type when
    given (ALL_* or None means any), and to a set of element ids when
    given (the Revit selection)."""
    out = []
    for r in rows:
        if r["category"] != category:
            continue
        if family not in (None, u"", ALL_FAMILIES) and r["family"] != family:
            continue
        if type_name not in (None, u"", ALL_TYPES) and r["type"] != type_name:
            continue
        if ids is not None and r["id"] not in ids:
            continue
        out.append(r)
    return out


def sample_rows(rows, cap=400):
    """Up to cap rows for reading the parameter list: one of each type
    first, so a parameter only one family has is still found, then the
    rest in order."""
    seen = set()
    first = []
    rest = []
    for r in rows:
        key = r.get("type_id")
        if key not in seen:
            seen.add(key)
            first.append(r)
        else:
            rest.append(r)
    return (first + rest)[:cap]


# ---------------------------------------------------------------------------
# 2. Parameters
# ---------------------------------------------------------------------------


def param_key(scope, name):
    return u"{0}:{1}".format(scope, name)


def split_key(key):
    scope, _sep, name = u"{0}".format(key).partition(u":")
    return scope, name


def param_label(scope, name):
    return name if scope == INSTANCE else u"{0} [type]".format(name)


def build_params(instance_catalog, type_catalog):
    """One list of parameter dicts - key, scope, name, label, storage,
    readonly, yesno, editable - instance parameters first, then type
    ones, each alphabetical. Element-id parameters are listed but not
    editable here."""
    out = []
    for scope, cat in ((INSTANCE, instance_catalog or {}), (TYPE, type_catalog or {})):
        for name in sorted(cat, key=_low):
            info = cat[name]
            editable = (not info.get("readonly")) and info.get("storage") in (
                "String", "Integer", "Double")
            out.append({"key": param_key(scope, name), "scope": scope, "name": name,
                        "label": param_label(scope, name), "storage": info.get("storage"),
                        "readonly": bool(info.get("readonly")), "yesno": bool(info.get("yesno")),
                        "editable": editable})
    return out


def search_params(params, text):
    """The parameters whose label holds every word typed, case aside."""
    words = [w for w in _low(text or u"").split() if w]
    return [p for p in params if all(w in _low(p["label"]) for w in words)]


def default_keys(params, wanted=(u"Mark", u"Comments")):
    keys = set(p["key"] for p in params)
    return [param_key(INSTANCE, w) for w in wanted if param_key(INSTANCE, w) in keys]


# ---------------------------------------------------------------------------
# 3. The selection and the plan
# ---------------------------------------------------------------------------


def selection_column(cells):
    """(param key, '') when every selected cell is in one parameter
    column, else (None, why). cells: [(row id, key or None)] - None for
    the fixed Element Id / Family : Type columns."""
    if not cells:
        return None, u"Select the cells to change - click, Shift+click or drag down one column."
    keys = set(k for _r, k in cells)
    if None in keys:
        return None, u"The Element Id and Family : Type columns cannot be changed."
    if len(keys) > 1:
        return None, (u"The selection spans {0} columns - select cells in one column "
                      u"only.".format(len(keys)))
    return list(keys)[0], u""


def check_value(param, text):
    """'' when text can go into the parameter, else why not."""
    if param is None:
        return u"unknown parameter"
    if param.get("readonly"):
        return u"'{0}' is read-only".format(param["label"])
    st = param.get("storage")
    t = u"{0}".format(text if text is not None else u"").strip()
    if st == "String":
        return u""
    if st == "Integer":
        if param.get("yesno"):
            return CP.to_yesno(t)[2]
        return CP.to_integer(t)[2]
    if st == "Double":
        return u"" if CP.to_number(t) is not None else u"type a number"
    return u"'{0}' refers to another element and cannot be typed here".format(param["label"])


def unchanged(old, new, storage):
    """True when the current value already is the new one: exact text
    for text parameters (so case can be fixed), the same number for
    numeric ones."""
    if old is None:
        return False
    if storage == "String":
        return u"{0}".format(old) == u"{0}".format(new)
    return CP.same_value(old, new)


def plan_change(cells, rows_by_id, param, new_text, values):
    """What writing new_text into the selected cells would do.

    cells:      [(row id, key)] all in param's column.
    rows_by_id: {row id: row dict with type_id}.
    values:     {(row id, key): current text, or None when the element
                has no such parameter}.

    Instance parameters are written per element; a type parameter is
    written once per type, which changes every instance of it.
    Returns {"targets": [{"owner", "rows", "old", "new"}], "counts",
    "skipped": [(row id, kind, why)], "cells", "types_hit"}."""
    new = u"{0}".format(new_text if new_text is not None else u"")
    if param.get("storage") != "String":
        new = new.strip()
    why = check_value(param, new)
    targets = []
    by_owner = {}
    skipped = []
    counts = {"set": 0, "same": 0, "missing": 0, "invalid": 0}
    for rid, key in cells:
        row = rows_by_id.get(rid)
        if row is None:
            continue
        cur = values.get((rid, key))
        owner = row.get("type_id") if param["scope"] == TYPE else rid
        if cur is None:
            counts["missing"] += 1
            skipped.append((rid, "missing", u"no '{0}' on this element".format(param["label"])))
            continue
        if why:
            counts["invalid"] += 1
            skipped.append((rid, "invalid", why))
            continue
        if owner in by_owner:
            by_owner[owner]["rows"].append(rid)
            continue
        if unchanged(cur, new, param.get("storage")):
            counts["same"] += 1
            by_owner[owner] = {"owner": owner, "rows": [rid], "old": cur, "new": new,
                               "same": True}
            continue
        t = {"owner": owner, "rows": [rid], "old": cur, "new": new, "same": False}
        by_owner[owner] = t
        targets.append(t)
        counts["set"] += 1
    return {"targets": targets, "counts": counts, "skipped": skipped,
            "cells": len(cells), "new": new,
            "types_hit": len(targets) if param["scope"] == TYPE else 0}


def plan_text(plan, param, rows_in_table=None):
    """One line for the status bar."""
    c = plan["counts"]
    bits = [u"{0} cell(s) selected in '{1}'".format(plan["cells"], param["label"])]
    if param["scope"] == TYPE:
        bits.append(u"{0} type(s) to change - every instance of them changes{1}".format(
            c["set"], u" ({0} row(s) in the table)".format(rows_in_table)
            if rows_in_table is not None else u""))
    else:
        bits.append(u"{0} to change".format(c["set"]))
    if c["same"]:
        bits.append(u"{0} already that value".format(c["same"]))
    if c["missing"]:
        bits.append(u"{0} without the parameter".format(c["missing"]))
    if c["invalid"]:
        why = [w for _r, k, w in plan["skipped"] if k == "invalid"]
        bits.append(u"{0} refused - {1}".format(c["invalid"], why[0] if why else u""))
    return u"; ".join(bits) + u"."


__all__ = ["ALL_FAMILIES", "ALL_TYPES", "NO_TYPE", "INSTANCE", "TYPE", "categories",
           "family_options", "type_options", "filter_rows", "sample_rows", "param_key",
           "split_key", "param_label", "build_params", "search_params", "default_keys",
           "selection_column", "check_value", "unchanged", "plan_change", "plan_text"]
