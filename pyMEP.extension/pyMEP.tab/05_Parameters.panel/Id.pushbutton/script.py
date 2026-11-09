# -*- coding: utf-8 -*-
"""Id - pick an element, its id is copied to the clipboard.

No dialog: the id shows in Revit's own status bar (the prompt of the
next pick) and is on the clipboard at once. Picking goes on until Esc.
With exactly one element selected when the button runs, that id is
copied first.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

__title__ = "Id"
__author__ = "Glent Group"

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
from Autodesk.Revit.UI.Selection import ObjectType

from pyrevit import revit, script

from pymep_revit import id_value

doc = revit.doc
uidoc = revit.uidoc


def copy_text(text):
    try:
        clr.AddReference("PresentationCore")
        from System.Windows import Clipboard
        Clipboard.SetText(text)
        return True
    except Exception:
        pass
    try:
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import Clipboard as FormsClipboard
        FormsClipboard.SetText(text)
        return True
    except Exception:
        return False


def describe(el):
    try:
        cat = el.Category.Name if el.Category is not None else u""
    except Exception:
        cat = u""
    try:
        name = el.Name
    except Exception:
        name = u""
    return u" ".join(b for b in (cat, name) if b)


copied = []
prompt = u"Pick an element to copy its id (Esc to stop)"
try:
    sel = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    if len(sel) == 1 and sel[0] is not None:
        eid = u"{0}".format(id_value(sel[0].Id))
        if copy_text(eid):
            copied.append(eid)
            prompt = u"Id {0} copied ({1}) - pick another element, Esc to stop".format(
                eid, describe(sel[0]))
except Exception:
    pass

while True:
    try:
        ref = uidoc.Selection.PickObject(ObjectType.Element, prompt)
    except Exception:
        break               # Esc
    if ref is None:
        break
    el = doc.GetElement(ref.ElementId)
    eid = u"{0}".format(id_value(ref.ElementId))
    if copy_text(eid):
        copied.append(eid)
        prompt = u"Id {0} copied ({1}) - pick another element, Esc to stop".format(
            eid, describe(el) if el is not None else u"")
    else:
        prompt = u"Id {0} - clipboard not reachable; pick another, Esc to stop".format(eid)

if copied:
    # keep the last one on the clipboard; no window unless several were picked,
    # then list them all in the output so none is lost
    if len(copied) > 1:
        out = script.get_output()
        out.print_md("Ids copied, last one on the clipboard: " + ", ".join(copied))
