# -*- coding: utf-8 -*-
"""Reading and writing Revit parameters as text - shared by
CSV to Parameter and Bulk Change (05_Parameters.panel).

A value is read the way Properties shows it (AsValueString for numbers,
the referenced element's name for an element id) and written back from
text by storage type: text as it is, whole numbers and Yes/No checked
first, measured values through SetValueString in the project's units
with a unit-converted fallback.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

import re

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import BuiltInParameter, UnitUtils

import pymep_csv_params as CP
from pymep_revit import id_value

PARAM_CAP = 400          # elements sampled for a parameter list


def is_yesno(p):
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


def display_unit(p):
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


def storage_name(st):
    s = u"{0}".format(st)
    return s.split(".")[-1]


def param_catalog(elements, cap=PARAM_CAP):
    """{name: {"storage": "String"/"Integer"/"Double"/"ElementId",
    "readonly": bool, "yesno": bool}} over the parameters of (a sample
    of) the elements. Writable on any element counts."""
    cat = {}
    for el in elements[:cap]:
        try:
            params = el.Parameters
        except Exception:
            continue
        for p in params:
            try:
                name = u"{0}".format(p.Definition.Name)
                st = storage_name(p.StorageType)
            except Exception:
                continue
            if st == "None" or not name:
                continue
            info = cat.get(name)
            if info is None:
                cat[name] = {"storage": st, "readonly": bool(p.IsReadOnly),
                             "yesno": st == "Integer" and is_yesno(p)}
            elif info["readonly"] and not p.IsReadOnly:
                info["readonly"] = False
    return cat


def element_name(el):
    """An element's name - also for family symbols, whose Name property
    IronPython sometimes cannot reach directly."""
    if el is None:
        return u""
    try:
        return u"{0}".format(el.Name)
    except Exception:
        pass
    for bip in ("SYMBOL_NAME_PARAM", "ALL_MODEL_TYPE_NAME"):
        try:
            p = el.get_Parameter(getattr(BuiltInParameter, bip))
            if p is not None and p.HasValue:
                return u"{0}".format(p.AsString() or u"")
        except Exception:
            continue
    return u""


def as_text(p, doc):
    """A parameter's value as Properties shows it."""
    try:
        if not p.HasValue:
            return u""
        st = storage_name(p.StorageType)
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
            return element_name(ref) if ref is not None else u"{0}".format(id_value(eid))
    except Exception:
        pass
    return u""


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
            unit = display_unit(p)
            if unit is not None:
                v = UnitUtils.ConvertToInternalUnits(float(v), unit)
            return (True, u"") if p.Set(float(v)) else (False, u"Set refused")
    except Exception as ex:
        return False, u"{0}".format(ex)
    return False, u"parameter type {0} cannot be set from text".format(st)


def validator(info):
    """What can already be told is wrong for this parameter."""
    if info is None:
        return None
    if info["storage"] == "Integer":
        if info["yesno"]:
            return lambda t: CP.to_yesno(t)[2]
        return lambda t: CP.to_integer(t)[2]
    if info["storage"] == "Double":
        return lambda t: (u"" if re.search(r"\d", u"{0}".format(t)) else u"no number in it")
    return None
