# -*- coding: utf-8 -*-
"""Bridge Builder - pick the four posts, then open the builder page.

The two posts at the start and the two at the end are vertical
structural framing (or columns) already in the model. Their midpoints
give the deck centreline and the direction, their spacing the width,
their bases the deck level at each end and their length the default
truss height. The page gets all of that plus the model's framing, floor,
stiffener and railing types and levels, and saves a design file for
Build Bridge.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

__title__ = "Bridge\nBuilder"
__author__ = "Glent Group"

import datetime
import io
import os
import sys
import time

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")

from pyrevit import revit, forms, script

import pymep_bridge as BR
import pymep_bridge_dialog as DLG
import pymep_bridge_revit as RV
import pymep_bridge_sync as SYNC
from pymep_config import get_export_folder
from pymep_dashboard_launch import launch_html
from pymep_revit import id_value, make_id

output = script.get_output()
doc = revit.doc
uidoc = revit.uidoc

HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(BR.__file__)), "pymep_bridge_builder.html")

registry_file = RV.registry_path(doc)
registry = BR.load_registry(registry_file)
cands = SYNC.design_candidates(SYNC.design_folders(registry))


def delete_bridges(names):
    """Confirm, then delete the bridges' elements and records. True when
    deleted."""
    text = SYNC.removal_text(doc, registry, names)
    if forms.alert(text, title="Delete bridge", options=["Delete", "Cancel"]) != "Delete":
        return False
    notes = SYNC.remove(doc, registry, names, True,
                        datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), time.time())
    BR.save_registry(registry_file, registry)
    output.print_md("### Deleted")
    output.print_table(table_data=[[n, r] for n, r in notes], columns=["Bridge", "Result"])
    return True


def pick_four(skip):
    """(start posts, end posts, (bridge, post) when one of them already
    carries a bridge other than skip) - the four posts selected before
    the click, else picked pair by pair."""
    try:
        pair = RV.pick_posts(doc, uidoc, forms.alert)
    except ValueError as ex:
        forms.alert(u"{0}".format(ex), exitscript=True)
    if pair is None:
        script.exit()
    ids = [id_value(e.Id) for e in pair[0] + pair[1]]
    return pair[0], pair[1], BR.bridge_on_posts(registry, ids, skip=skip)


# edit a bridge already built here, delete one, or build a new one on four posts
editing = None
while True:
    rows = SYNC.bridge_rows(doc, registry, cands)
    if not rows:
        break
    choice = DLG.choose(
        rows, "edit",
        intro=u"Bridges built in this model. Edit one - the page opens on its posts with its saved "
              u"design - delete one, or build a new bridge on four other posts.",
        hint=u"A post carries one bridge. 'posts moved': the posts are not where the bridge was built - "
             u"editing lays its design out on them as they are now, or run Update Bridges to move the "
             u"bridge. 'post ... gone': edit it to pick its posts again (it keeps its element ids), or "
             u"delete it.")
    if choice is None:
        script.exit()
    if choice[0] == "delete":
        delete_bridges(choice[1])
        continue
    if choice[0] == "edit":
        editing = choice[1]
    break

start_els = end_els = None
if editing:
    rec = registry["bridges"][editing]
    problem = RV.posts_problem(doc, rec)
    if problem:
        again = u"Pick its posts again"
        drop = u"Delete '{0}'".format(editing)
        ans = forms.alert(
            u"{0}: {1}.\n\nPick its four posts again - the bridge keeps its design and, when you build "
            u"it, its element ids, so dimensions and tags on it stay. Or delete it.".format(editing, problem),
            title="Bridge Builder", options=[again, drop, u"Cancel"])
        if ans == drop:
            delete_bridges([editing])
            script.exit()
        if ans != again:
            script.exit()
        start_els, end_els, taken = pick_four(editing)
        start_els, end_els = RV.orient_like(rec, start_els, end_els)   # its start stays its start
    else:
        src = rec.get("source", {})
        start_els = [doc.GetElement(make_id(int(i))) for i in src.get("start_ids", [])]
        end_els = [doc.GetElement(make_id(int(i))) for i in src.get("end_ids", [])]
        taken = None
else:
    start_els, end_els, taken = pick_four(None)

# a post carries one bridge: edit that bridge on these posts, or delete it and build here
if taken:
    other = taken[0]
    other_problem = RV.posts_problem(doc, registry["bridges"][other])
    use = u"Edit '{0}' on these posts".format(other)
    drop = u"Delete '{0}', then build here".format(other)
    ans = forms.alert(
        u"Post {1} already carries '{0}'{2}. A post carries one bridge.\n\n"
        u"Edit '{0}' on these four posts - it keeps its design and, when you build it, its element "
        u"ids - or delete '{0}' and build {3} here.".format(
            other, taken[1], u" ({0})".format(other_problem) if other_problem else u"",
            u"'{0}'".format(editing) if editing else u"a new bridge"),
        title="Bridge Builder", options=[use, drop, u"Cancel"])
    if ans == use:
        editing = other
        start_els, end_els = RV.orient_like(registry["bridges"][other], start_els, end_els)
    elif ans == drop:
        if not delete_bridges([other]):
            script.exit()
    else:
        script.exit()

try:
    start_posts = [RV.post_info(e) for e in start_els]
    end_posts = [RV.post_info(e) for e in end_els]
    frame = BR.frame_from_posts(start_posts, end_posts)
except ValueError as ex:
    forms.alert(u"{0}".format(ex), exitscript=True)

source = {"start_ids": [p["id"] for p in start_posts], "end_ids": [p["id"] for p in end_posts],
          "labels": [p["label"] for p in start_posts + end_posts]}
existing_name = editing
existing = registry["bridges"].get(existing_name) if existing_name else None
# a design saved for this bridge since it was built opens instead of the built one
newer = SYNC.newer_design(existing, cands) if existing else None
frame_out = dict((k, frame[k]) for k in BR.FRAME_KEYS)
frame_out["origin"] = list(frame["origin"])
frame_out["gradient"] = frame["gradient"]
position = RV.project_position(doc)
data = {"name": existing_name or BR.next_name(registry), "model": doc.Title,
        "frame": frame_out, "posts": {"start": start_posts, "end": end_posts},
        "shared_elev_m": position["elev_m"],
        "source": source,
        "framing_types": RV.framing_types(doc), "floor_types": RV.floor_types(doc),
        "stiffener_types": RV.stiffener_types(doc), "railing_types": RV.railing_types(doc),
        "levels": RV.levels(doc),
        "design": (newer[2]["design"] if newer else existing["design"]) if existing else BR.latest_design(registry)}

with io.open(HTML_PATH, "r", encoding="utf-8") as f:
    template = f.read()
folder = get_export_folder(doc)
if not os.path.isdir(folder):
    os.makedirs(folder)
page = os.path.join(folder, u"bridge_builder_{0}.html".format(
    datetime.datetime.now().strftime("%Y%m%d_%H%M%S")))
with io.open(page, "w", encoding="utf-8", newline="\n") as f:
    f.write(BR.fill_page(template, data))
launch_html(page)

output.print_md("### Bridge Builder")
output.print_md("**{0}**: start posts {1}, end posts {2}; span **{3:.3f} m** between the pair "
                "midpoints, posts {4:.3f} m apart, {5:.0f} mm, {6:.3f} m tall; bases {7:.3f} -> "
                "{8:.3f} m (rise {9:+.0f} mm).".format(
                    data["name"], ", ".join(str(i) for i in source["start_ids"]),
                    ", ".join(str(i) for i in source["end_ids"]), frame["span"],
                    frame["tL"] - frame["tR"], frame["post_size"] * 1000.0, frame["post_height"],
                    frame["z0"], frame["z1"], (frame["z1"] - frame["z0"]) * 1000.0))
if existing_name:
    moved_to = sorted(BR.post_ids(source)) != sorted(BR.post_ids(existing))
    output.print_md("**{0}** - its design is loaded{1}; saving and building updates it in place "
                    "(element ids kept).".format(
                        existing_name, " onto these posts (it was on {0})".format(
                            ", ".join(str(i) for i in BR.post_ids(existing))) if moved_to else ""))
output.print_md("Builder page: `{0}`. Save the design there, then run **Build Bridge**.".format(page))
