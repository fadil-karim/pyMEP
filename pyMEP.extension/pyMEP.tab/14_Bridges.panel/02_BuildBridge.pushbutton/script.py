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

# types and level
framing = RV.framing_types(doc)
floors = RV.floor_types(doc)
levels = RV.levels(doc)
if not framing:
    forms.alert("No structural framing family is loaded.", exitscript=True)
if not floors or not levels:
    forms.alert("The model needs a floor type and a level for the deck.", exitscript=True)
symbols = {}
for role in BR.FRAMING_ROLES:
    symbols[role] = RV.first_of(framing, u"{0}".format(design["types"].get(role) or u""), doc)
floor_type = RV.first_of(floors, u"{0}".format(design["types"].get("floor") or u""), doc)
design["floor_thickness_m"] = RV.floor_thickness_m(floor_type) or design["floor_thickness_m"]
stiff_id = u"{0}".format(design["types"].get("stiffener") or u"none")
stiffener_sym = RV.by_id(doc, stiff_id) if stiff_id not in (u"", u"none") else None
rail_id = u"{0}".format(design["types"].get("railing") or u"none")
railing_type = RV.by_id(doc, rail_id) if rail_id not in (u"", u"none") else None
level = RV.first_of(levels, u"{0}".format(design.get("level") or u""), doc)
if level is None:
    below = [l for l in levels if l["elevation_m"] <= frame["z0"]]
    level = RV.by_id(doc, (below[-1] if below else levels[0])["id"])

registry_file = RV.registry_path(doc)
registry = BR.load_registry(registry_file)
old = registry["bridges"].get(name)
if old:
    if forms.alert(u"'{0}' was built before ({1}). Replace its elements with this design?".format(
            name, old.get("updated", "?")), title="Build Bridge",
            options=["Replace", "Cancel"]) != "Replace":
        script.exit()

shared_elev = RV.project_position(doc)["elev_m"]
mems = BR.world_members(design, frame, shared_elev)
deck = BR.world_deck(design, frame, shared_elev)
member_ids = {}
failed = []
rails_made = rails_hosted = 0
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
    if stiffener_sym is not None:
        RV.activate(stiffener_sym, doc)
    floor = None
    try:
        floor = RV.create_deck(doc, deck, floor_type, level)
        RV.set_mark_comments(floor, name)
        floor_id = id_value(floor.Id)
        doc.Regenerate()
    except Exception as ex:
        failed.append(({"key": "deck", "role": "deck"}, u"{0}".format(ex)))
    for m in mems:
        try:
            if m["role"] == "stiffener":
                if stiffener_sym is None:
                    continue
                el = RV.create_stiffener(doc, m, stiffener_sym, floor, level)
            elif m["role"] == "railing":
                if railing_type is None:
                    continue
                el = RV.create_railing(doc, m, railing_type, floor, level)
                rails_made += 1
                if RV.is_hosted_on(el, floor):
                    rails_hosted += 1
            else:
                sym = symbols.get(m["role"])
                if sym is None:
                    failed.append((m, u"no framing type"))
                    continue
                el = RV.create_member(doc, m, sym, level)
            RV.set_mark_comments(el, name)
            member_ids[m["key"]] = id_value(el.Id)
        except Exception as ex:
            failed.append((m, u"{0}".format(ex)))
    # the posts reach the top of steel of the top chords
    posts_note = u""
    try:
        eff = BR.effective_frame(design, frame, shared_elev)
        zt = BR.height(design, frame)
        rise = 0.0
        for m in mems:
            if m["role"] == "top_chord" and m["key"] in member_ids:
                rise = RV.rise_above_line(doc, doc.GetElement(make_id(member_ids[m["key"]])), m)
                break
        z_start = BR.to_world(eff, 0.0, 0.0, zt)[2] + rise
        z_end = BR.to_world(eff, frame["span"], 0.0, zt)[2] + rise
        n_posts = RV.set_post_tops(doc, source["start_ids"], z_start) + RV.set_post_tops(doc, source["end_ids"], z_end)
        posts_note = u"{0} post(s) set to the top of steel of the top chord ({1:.3f} / {2:.3f} m)".format(
            n_posts, z_start + shared_elev, z_end + shared_elev)
    except Exception as ex:
        posts_note = u"posts not adjusted: {0}".format(ex)
    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert(u"Failed, nothing built:\n\n{0}".format(ex), exitscript=True)
# the posts as they are now, for the tracking record
try:
    frame = BR.frame_from_posts(RV.posts_from_ids(doc, source["start_ids"]), RV.posts_from_ids(doc, source["end_ids"]))
except Exception:
    pass

stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
registry["bridges"][name] = BR.record(name, design, source, frame, member_ids, floor_id, stamp)
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
forms.alert(u"'{0}' built: {1} element(s){2} and the deck{3}.\n\nThe elements are selected; one "
            u"undo step removes them.".format(
                name, len(member_ids), u", {0} failed".format(len(failed)) if failed else u"",
                u"" if floor_id else u" (deck FAILED - see the output window)"),
            title="Build Bridge")
