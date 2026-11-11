# -*- coding: utf-8 -*-
"""Build Bridge - model the bridge a Bridge Builder design describes.

Reads the design file the builder page saved (newest bridge_*.json in
Downloads, or a pick), reads the four posts it names, and in one
transaction places the deck floor inside the posts (sloped from the
start bases to the end bases), both trusses, the cross beams and plan
bracing with their tops on the floor underside, the deck stiffeners
hosted on the floor and the railings along the deck edges. Every element
is recorded against the bridge name for Update Bridges.

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
import pymep_bridge_sync as SYNC
from pymep_config import get_downloads_folder
from pymep_revit import id_value, make_id, quiet

output = script.get_output()
doc = revit.doc
uidoc = revit.uidoc


def design_files():
    files = glob.glob(os.path.join(get_downloads_folder(), "bridge_*.json"))
    files.sort(key=lambda p: -os.path.getmtime(p))
    return files


BROWSE = u"Browse for a design file saved elsewhere..."
files = design_files()
path = files[0] if files else None
if len(files) != 1:
    recent = files[:12]
    labels = [u"{0}   ({1})".format(os.path.basename(p), datetime.datetime.fromtimestamp(
        os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")) for p in recent] + [BROWSE]
    pick = forms.SelectFromList.show(labels, title="Which design?", button_name="Build",
                                     multiselect=False)
    if not pick:
        script.exit()
    if pick == BROWSE:
        path = forms.pick_file(file_ext="json", title="Bridge design file (bridge_<name>.json)")
        if not path:
            script.exit()
    else:
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

# the posts as they are now
try:
    start_posts = RV.posts_from_ids(doc, source["start_ids"])
    end_posts = RV.posts_from_ids(doc, source["end_ids"])
    frame = BR.frame_from_posts(start_posts, end_posts)
except ValueError as ex:
    forms.alert(u"{0}\n\nRun Bridge Builder again.".format(ex), exitscript=True)

# types and level (the values read from the types go into the design)
try:
    types = SYNC.resolve(doc, design, frame)
except ValueError as ex:
    forms.alert(u"{0}".format(ex), exitscript=True)
symbols = types["symbols"]
stiffener_sym = types["stiffener"]
railing_type = types["railing"]
level = types["level"]

registry_file = RV.registry_path(doc)
registry = BR.load_registry(registry_file)
old = registry["bridges"].get(name)
renamed_from = None
if old is None:
    # the same posts under another name: that bridge is the one being changed (renamed)
    other = BR.bridge_with_source(registry, source)
    if other:
        old = registry["bridges"][other]
        renamed_from = other
rebuild = False
if old:
    pick = forms.alert(
        u"'{0}' is already built ({1}){2}.\n\nUpdate it in place: its elements keep their ids, so "
        u"dimensions and tags on it stay - members the design no longer has are removed and new "
        u"ones added. Or rebuild it with all-new elements.".format(
            renamed_from or name, old.get("updated", "?"),
            u" - it becomes '{0}'".format(name) if renamed_from else u""),
        title="Build Bridge", options=["Update in place", "Rebuild with new elements", "Cancel"])
    if pick == "Rebuild with new elements":
        rebuild = True
    elif pick != "Update in place":
        script.exit()

shared_elev = RV.project_position(doc)["elev_m"]
t = Transaction(doc, u"pyMEP: {0} bridge '{1}'".format("Update" if old and not rebuild else "Build", name))
quiet(t)
t.Start()
try:
    base = old
    if old and rebuild:
        RV.delete_quiet(doc, list(old.get("members", {}).values()) +
                        ([old["floor"]] if old.get("floor") else []))
        base = None
    res = SYNC.sync(doc, name, design, source, frame, base, types, shared_elev)
    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert(u"Failed, nothing changed:\n\n{0}".format(ex), exitscript=True)
member_ids = res["member_ids"]
floor_id = res["floor_id"]
failed = res["failed"]
mems = res["members"]
rails_made, rails_hosted, posts_note = res["rails_made"], res["rails_hosted"], res["posts"]
# the posts as they are now, for the tracking record
try:
    frame = BR.frame_from_posts(RV.posts_from_ids(doc, source["start_ids"]), RV.posts_from_ids(doc, source["end_ids"]))
except Exception:
    pass

stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
if renamed_from and renamed_from != name:
    registry["bridges"].pop(renamed_from, None)
registry["bridges"][name] = BR.record(name, design, source, frame, member_ids, floor_id, stamp,
                                      extra={"design_file": path, "design_mtime": os.path.getmtime(path)})
try:
    BR.save_registry(registry_file, registry)
except Exception as ex:
    output.print_md("**Could not save the tracking file** ({0}) - Update Bridges will not "
                    "know this bridge.".format(ex))

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
summ = BR.summary(design, frame, mems)
eff = BR.effective_frame(design, frame, shared_elev)
output.print_md("### Build Bridge - {0}".format(name))
if old and not rebuild:
    output.print_md(u"**Updated in place** - element ids kept: {0}".format(SYNC.summary_line(res)))
output.print_md(u"**Posts:** {0}".format(posts_note))
if rails_made:
    output.print_md(u"**Railings:** {0} placed, {1} hosted on the deck{2}".format(
        rails_made, rails_hosted,
        u"" if rails_hosted == rails_made else u" - Revit would not host the rest; check the railing path lies inside the deck"))
output.print_md("**Span:** {0:.3f} m  |  **Rise:** {1:+.0f} mm  |  **Height:** {2:.3f} m  |  "
                "**Panels:** {3} x {4:.3f} m  |  **Plan bays:** {5}  |  **Placed:** {6}  |  "
                "**Failed:** {7}  |  **Deck:** {8}  |  **Level:** {9}".format(
                    frame["span"], (eff["z1"] - eff["z0"]) * 1000.0, summ["height_m"],
                    summ["panels"], summ["panel_length_m"], summ["bays"], len(member_ids),
                    len(failed), output.linkify(make_id(floor_id)) if floor_id else "not made",
                    level.Name))
rows = []
for role in BR.ROLES:
    r = summ["by_role"].get(role)
    if not r:
        continue
    if role == "stiffener":
        tname = RV.element_label(stiffener_sym) if stiffener_sym is not None else u"(none)"
    elif role == "railing":
        tname = RV.element_label(railing_type) if railing_type is not None else u"(none)"
    else:
        sym = symbols.get(role)
        tname = RV.element_label(sym) if sym is not None else u"-"
    rows.append([BR.ROLE_LABELS[role], r["count"], "{0:.2f}".format(r["length_m"]), tname])
output.print_table(table_data=rows, title="Members", columns=["Role", "Count", "Length (m)", "Type"])
if failed:
    output.print_table(table_data=[[m.get("key"), why] for m, why in failed],
                       title="Failed ({0})".format(len(failed)), columns=["Member", "Why"])
output.print_md("Tracked in `{0}` - move the posts and run **Update Bridges**.".format(registry_file))
forms.alert(u"'{0}' {1}: {2} element(s){3} and the deck{4}.{5}\n\nThe elements are selected; one "
            u"undo step reverses it.".format(
                name, u"updated in place" if old and not rebuild else u"built", len(member_ids),
                u", {0} failed".format(len(failed)) if failed else u"",
                u"" if floor_id else u" (deck FAILED - see the output window)",
                u"\n\n" + SYNC.summary_line(res) if old and not rebuild else u""),
            title="Build Bridge")
