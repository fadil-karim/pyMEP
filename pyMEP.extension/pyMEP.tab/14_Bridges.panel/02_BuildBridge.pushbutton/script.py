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
OTHER = u"Choose another design file"


def choose_file(force_list):
    """The design file: the only one in Downloads, else (or when asked
    for another) one picked from the newest twelve or browsed for."""
    files = design_files()
    if len(files) == 1 and not force_list:
        return files[0]
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
        return path
    return recent[labels.index(pick)]


registry_file = RV.registry_path(doc)
registry = BR.load_registry(registry_file)
force_list = False
while True:
    path = choose_file(force_list)
    force_list = True
    with io.open(path, "r", encoding="utf-8") as f:
        text = f.read()
    try:
        design_file = BR.parse_design_file(text)
    except ValueError as ex:
        forms.alert(u"{0}:\n\n{1}".format(os.path.basename(path), ex), exitscript=True)
    name = design_file["name"]
    design = design_file["design"]
    source = design_file["source"]
    # a design saved before its bridge was deleted is not built again unasked
    dead = None if name in registry["bridges"] else BR.deleted_bridge(registry, name, os.path.getmtime(path))
    if dead:
        again = u"Build it again"
        ans = forms.alert(
            u"{0} is a design for '{1}', which was {2} on {3} - this file is from before that.\n\n"
            u"Build it again as a new bridge?".format(
                os.path.basename(path), name, u"deleted" if dead.get("elements_deleted") else u"forgotten",
                dead.get("when", u"?")),
            title="Build Bridge", options=[again, OTHER, u"Cancel"])
        if ans == OTHER:
            continue
        if ans != again:
            script.exit()
    # the posts as they are now; when one is gone, pick the four the bridge goes on
    problem = RV.posts_problem(doc, {"source": source})
    built = registry["bridges"].get(name)
    if problem and built and BR.post_ids(built) != BR.post_ids(source) and not RV.posts_problem(doc, built):
        # an older file for a bridge that has been moved onto other posts since: build on those
        output.print_md(u"{0} names posts {1}, but '{2}' is on posts {3} now ({4}) - built on those.".format(
            os.path.basename(path), u", ".join(u"{0}".format(i) for i in BR.post_ids(source)), name,
            u", ".join(u"{0}".format(i) for i in BR.post_ids(built)), problem))
        source = dict(built["source"])
        problem = u""
    if problem:
        repick = u"Pick the four posts"
        ans = forms.alert(
            u"{0} is for '{1}' on posts {2}, but {3}.\n\nPick the four posts to build it on - if it is "
            u"already built it is updated in place and keeps its element ids - or choose another "
            u"design file.".format(os.path.basename(path), name,
                                   u", ".join(u"{0}".format(i) for i in BR.post_ids(source)), problem),
            title="Build Bridge", options=[repick, OTHER, u"Cancel"])
        if ans == OTHER:
            continue
        if ans != repick:
            script.exit()
        try:
            pair = RV.pick_posts(doc, uidoc, forms.alert, use_selection=False)
        except ValueError as ex:
            forms.alert(u"{0}".format(ex), exitscript=True)
        if pair is None:
            script.exit()
        pair = RV.orient_like(built, pair[0], pair[1])      # a built bridge keeps its start at the same end
        source = {"start_ids": [id_value(e.Id) for e in pair[0]], "end_ids": [id_value(e.Id) for e in pair[1]],
                  "labels": [RV.element_label(e) for e in pair[0] + pair[1]]}
    break

try:
    start_posts = RV.posts_from_ids(doc, source["start_ids"])
    end_posts = RV.posts_from_ids(doc, source["end_ids"])
    frame = BR.frame_from_posts(start_posts, end_posts)
except ValueError as ex:
    forms.alert(u"The posts cannot carry a bridge: {0}".format(ex), exitscript=True)

# types and level (the values read from the types go into the design)
try:
    types = SYNC.resolve(doc, design, frame)
except ValueError as ex:
    forms.alert(u"{0}".format(ex), exitscript=True)
symbols = types["symbols"]
stiffener_sym = types["stiffener"]
railing_type = types["railing"]
level = types["level"]

old = registry["bridges"].get(name)
renamed_from = None
if old is None:
    # the same posts under another name: that bridge is the one being changed (renamed)
    other = BR.bridge_with_source(registry, source)
    if other:
        old = registry["bridges"][other]
        renamed_from = other
# a post carries one bridge: one of these posts already carries another
taken = BR.bridge_on_posts(registry, BR.post_ids(source), skip=renamed_from or name)
if taken and old is not None and renamed_from is None:
    forms.alert(u"'{0}' is built, and post {2} of this design carries '{1}'. A post carries one bridge - "
                u"delete one of them (Delete Bridges), or pick other posts for '{0}' in Bridge Builder.".format(
                    name, taken[0], taken[1]), exitscript=True)
if taken:
    replace = u"Replace '{0}' with this design".format(taken[0])
    ans = forms.alert(
        u"Post {1} already carries '{0}'. A post carries one bridge.\n\nReplace '{0}' with this design - "
        u"it is updated in place on these posts{2}, keeping its element ids - or cancel.".format(
            taken[0], taken[1], u" and renamed '{0}'".format(name) if taken[0] != name else u""),
        title="Build Bridge", options=[replace, u"Cancel"])
    if ans != replace:
        script.exit()
    old = registry["bridges"][taken[0]]
    renamed_from = taken[0]
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
    others = [n for n in registry["bridges"] if n not in (name, renamed_from)]
    if old and rebuild:
        # only the bridge's own elements (marked with its name), never its posts
        RV.delete_bridge(doc, old, renamed_from or name, others)
        base = None
    res = SYNC.sync(doc, name, design, source, frame, base, types, shared_elev, others)
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
registry.get("deleted", {}).pop(name, None)       # built again: no longer a deleted bridge
aliases = list((old or {}).get("aliases") or [])
if renamed_from and renamed_from != name and renamed_from not in aliases:
    aliases.append(renamed_from)
registry["bridges"][name] = BR.record(name, design, source, frame, member_ids, floor_id, stamp,
                                      extra={"design_file": path, "design_mtime": os.path.getmtime(path),
                                             "aliases": aliases})
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
                u"\n\n" + SYNC.summary_line(res) if old and not rebuild else
                (u"\n\n" + SYNC.problem_line(res) if SYNC.problem_line(res) else u"")),
            title="Build Bridge")
