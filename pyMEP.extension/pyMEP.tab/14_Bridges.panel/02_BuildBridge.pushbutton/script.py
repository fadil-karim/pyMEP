# -*- coding: utf-8 -*-
"""Build Bridge - model the bridge a Bridge Builder design describes.

Reads the design file the builder page saved (newest bridge_*.json in
Downloads, or a pick when several are recent), finds the two end
points it names and reads their deck levels, then in one transaction
places the sloped deck floor, both trusses, the cross beams and the plan
bracing as structural framing. Every element is recorded against the
bridge name so Update Bridges can move it later; building a name that
exists replaces its elements.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

__title__ = "Build\nBridge"
__author__ = "Glent Group"

import datetime
import glob
import io
import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import ElementId, Transaction

from pyrevit import revit, forms, script

import pymep_bridge as BR
import pymep_bridge_revit as RV
from pymep_config import get_downloads_folder
from pymep_revit import id_value, make_id, quiet

output = script.get_output()
doc = revit.doc
uidoc = revit.uidoc


# ---------------------------------------------------------------------------
# 1. The design file
# ---------------------------------------------------------------------------
def design_files():
    folder = get_downloads_folder()
    files = glob.glob(os.path.join(folder, "bridge_*.json"))
    files.sort(key=lambda p: -os.path.getmtime(p))
    return files


files = design_files()
if not files:
    forms.alert("No bridge_*.json in Downloads. Open Bridge Builder, design the bridge and "
                "press Save design first.", exitscript=True)
path = files[0]
if len(files) > 1:
    recent = files[:12]
    labels = [u"{0}   ({1})".format(os.path.basename(p), datetime.datetime.fromtimestamp(
        os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")) for p in recent]
    pick = forms.SelectFromList.show(labels, title="Which design?", button_name="Build",
                                     multiselect=False)
    if not pick:
        script.exit()
    path = recent[labels.index(pick)]
with io.open(path, "r", encoding="utf-8") as f:
    text = f.read()
try:
    design_file = BR.parse_design_file(text)
except ValueError as ex:
    forms.alert(u"{0}:\n\n{1}".format(os.path.basename(path), ex), exitscript=True)
name = design_file["name"]
design = design_file["design"]
source = design_file["source"]

# ---------------------------------------------------------------------------
# 2. The two ends, as they are now
# ---------------------------------------------------------------------------
start_el = doc.GetElement(make_id(int(source["start_id"])))
end_el = doc.GetElement(make_id(int(source["end_id"])))
if start_el is None or end_el is None:
    forms.alert("The start or end point this design names is no longer in the model "
                "(ids {0} and {1}). Run Bridge Builder again.".format(
                    source["start_id"], source["end_id"]), exitscript=True)
position = RV.project_position(doc)
try:
    p_start = RV.end_point(doc, start_el, source["level_param"], source["level_mode"], position)
    p_end = RV.end_point(doc, end_el, source["level_param"], source["level_mode"], position)
    frame = BR.frame_from_points(p_start, p_end)
except ValueError as ex:
    forms.alert(u"{0}".format(ex), exitscript=True)

# ---------------------------------------------------------------------------
# 3. Types and level
# ---------------------------------------------------------------------------
framing = RV.framing_types(doc)
floors = RV.floor_types(doc)
levels = RV.levels(doc)
if not framing:
    forms.alert("No structural framing family is loaded - load the chord / beam families "
                "first.", exitscript=True)
if not floors or not levels:
    forms.alert("The model needs a floor type and a level for the deck.", exitscript=True)
symbols = {}
for role in BR.ROLES:
    symbols[role] = RV.first_of(framing, u"{0}".format(design["types"].get(role) or u""), doc)
floor_type = RV.first_of(floors, u"{0}".format(design["types"].get("floor") or u""), doc)
level = RV.first_of(levels, u"{0}".format(design.get("level") or u""), doc)
if level is None:
    below = [l for l in levels if l["elevation_m"] <= p_start[2]]
    level = RV.by_id(doc, (below[-1] if below else levels[0])["id"])

registry_file = RV.registry_path(doc)
registry = BR.load_registry(registry_file)
old = registry["bridges"].get(name)
if old:
    if forms.alert(u"'{0}' was built before ({1}). Replace its {2} element(s) with this "
                   u"design?".format(name, old.get("updated", "?"), len(old.get("members", {})) + 1),
                   title="Build Bridge", options=["Replace", "Cancel"]) != "Replace":
        script.exit()

# ---------------------------------------------------------------------------
# 4. Build - one transaction
# ---------------------------------------------------------------------------
mems = BR.world_members(design, frame)
deck = BR.world_deck(design, frame)
member_ids = {}
failed = []
floor_id = None
t = Transaction(doc, u"pyMEP: Build bridge '{0}'".format(name))
quiet(t)
t.Start()
try:
    if old:
        RV.delete_quiet(doc, list(old.get("members", {}).values()) +
                        ([old["floor"]] if old.get("floor") else []))
    for sym in set(s for s in symbols.values() if s is not None):
        RV.activate(sym, doc)
    for m in mems:
        sym = symbols.get(m["role"])
        if sym is None:
            failed.append((m, u"no framing type"))
            continue
        try:
            el = RV.create_member(doc, m, sym, level)
            RV.set_mark_comments(el, name)
            member_ids[m["key"]] = id_value(el.Id)
        except Exception as ex:
            failed.append((m, u"{0}".format(ex)))
    try:
        floor = RV.create_deck(doc, deck, floor_type, level)
        RV.set_mark_comments(floor, name)
        floor_id = id_value(floor.Id)
    except Exception as ex:
        failed.append(({"key": "deck", "role": "deck"}, u"{0}".format(ex)))
    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert(u"Failed, nothing built:\n\n{0}".format(ex), exitscript=True)

stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
registry["bridges"][name] = BR.record(name, design, source, frame, member_ids, floor_id, stamp)
try:
    BR.save_registry(registry_file, registry)
except Exception as ex:
    output.print_md("**Could not save the tracking file** ({0}) - Update Bridges will not "
                    "know this bridge.".format(ex))

# ---------------------------------------------------------------------------
# 5. Report
# ---------------------------------------------------------------------------
try:
    from System.Collections.Generic import List
    ids = List[ElementId]()
    for i in member_ids.values():
        ids.Add(make_id(i))
    if floor_id:
        ids.Add(make_id(floor_id))
    uidoc.Selection.SetElementIds(ids)
except Exception:
    pass
summ = BR.summary(design, frame["span"], mems)
output.print_md("### Build Bridge - {0}".format(name))
output.print_md("**Span:** {0:.3f} m  |  **Rise:** {1:+.0f} mm  |  **Panels:** {2} x {3:.3f} m  |  "
                "**Members placed:** {4}  |  **Failed:** {5}  |  **Deck:** {6}  |  **Level:** {7}".format(
                    frame["span"], (p_end[2] - p_start[2]) * 1000.0, summ["panels"],
                    summ["panel_length_m"], len(member_ids), len(failed),
                    output.linkify(make_id(floor_id)) if floor_id else "not made", level.Name))
rows = []
for role in BR.ROLES:
    r = summ["by_role"].get(role)
    if r:
        sym = symbols.get(role)
        rows.append([BR.ROLE_LABELS[role], r["count"], "{0:.2f}".format(r["length_m"]),
                     u"{0} : {1}".format(sym.FamilyName, sym.Name) if sym is not None else "-"])
output.print_table(table_data=rows, title="Members", columns=["Role", "Count", "Length (m)", "Type"])
if failed:
    output.print_table(table_data=[[m.get("key"), why] for m, why in failed],
                       title="Failed ({0})".format(len(failed)), columns=["Member", "Why"])
output.print_md("Tracked in `{0}` - move the end points or change their levels and run "
                "**Update Bridges**.".format(registry_file))
forms.alert(u"'{0}' built: {1} member(s){2} and the deck{3}.\n\nThe elements are selected; one "
            u"undo step removes them.".format(
                name, len(member_ids), u", {0} failed".format(len(failed)) if failed else u"",
                u"" if floor_id else u" (deck FAILED - see the output window)"),
            title="Build Bridge")
