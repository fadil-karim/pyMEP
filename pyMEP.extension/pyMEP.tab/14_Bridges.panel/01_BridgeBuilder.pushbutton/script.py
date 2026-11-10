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

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

from pyrevit import revit, forms, script

import pymep_bridge as BR
import pymep_bridge_revit as RV
from pymep_config import get_export_folder
from pymep_dashboard_launch import launch_html
from pymep_revit import id_value, make_id

output = script.get_output()
doc = revit.doc
uidoc = revit.uidoc

HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(BR.__file__)), "pymep_bridge_builder.html")
POST_CATEGORIES = ("Structural Framing", "Structural Columns", "Columns")


class PostFilter(ISelectionFilter):
    def AllowElement(self, el):
        try:
            return u"{0}".format(el.Category.Name) in POST_CATEGORIES
        except Exception:
            return False

    def AllowReference(self, ref, pt):
        return False


def pick_pair(which, preselected):
    """Two posts: the preselected ones when exactly two were given, else
    picked in the model. None when cancelled."""
    if preselected is not None:
        return preselected
    while True:
        try:
            refs = uidoc.Selection.PickObjects(
                ObjectType.Element, PostFilter(),
                "Pick the TWO posts at the {0} (one each side), then Finish".format(which))
        except Exception:
            return None
        els = [doc.GetElement(r.ElementId) for r in refs]
        if len(els) == 2:
            return els
        forms.alert("Pick exactly two posts for the {0} - you picked {1}.".format(which, len(els)))


# edit a bridge already built here, or build a new one on four posts
registry = BR.load_registry(RV.registry_path(doc))
names = sorted(registry["bridges"].keys())
editing = None
if names:
    NEW = u"Build a new bridge (pick its four posts)"
    opts = [NEW] + [u"Edit {0}   ({1} members, {2})".format(
        n, len(registry["bridges"][n].get("members", {})), registry["bridges"][n].get("updated", u"")) for n in names]
    pick = forms.SelectFromList.show(opts, title="Bridge Builder - edit an existing bridge or build a new one?",
                                     button_name="Open", multiselect=False)
    if not pick:
        script.exit()
    if pick != NEW:
        editing = names[opts.index(pick) - 1]

if editing:
    rec = registry["bridges"][editing]
    src = rec.get("source", {})
    start_els = [doc.GetElement(make_id(int(i))) for i in src.get("start_ids", [])]
    end_els = [doc.GetElement(make_id(int(i))) for i in src.get("end_ids", [])]
    if len(start_els) != 2 or len(end_els) != 2 or any(e is None for e in start_els + end_els):
        forms.alert(u"The posts of {0} are no longer all in the model, so it cannot be edited. "
                    u"Run Update Bridges to see what is left of it.".format(editing), exitscript=True)
else:
    # a selection of four posts made before the click is taken as start pair then end pair,
    # the two nearest each other first
    pre_start = pre_end = None
    try:
        sel = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
        sel = [e for e in sel if e is not None and PostFilter().AllowElement(e)]
        if len(sel) == 4:
            infos = [RV.post_info(e) for e in sel]
            best = None
            for i in range(4):
                for j in range(i + 1, 4):
                    d = (infos[i]["x"] - infos[j]["x"]) ** 2 + (infos[i]["y"] - infos[j]["y"]) ** 2
                    if best is None or d < best[0]:
                        best = (d, i, j)
            a = [sel[best[1]], sel[best[2]]]
            b = [sel[k] for k in range(4) if k not in (best[1], best[2])]
            pre_start, pre_end = a, b
    except Exception:
        pre_start = pre_end = None

    start_els = pick_pair("START", pre_start)
    if start_els is None:
        script.exit()
    end_els = pick_pair("END", pre_end)
    if end_els is None:
        script.exit()
    if set(id_value(e.Id) for e in start_els) & set(id_value(e.Id) for e in end_els):
        forms.alert("A post was picked at both ends.", exitscript=True)
    # a bridge's posts cannot take another bridge
    taken = BR.bridge_on_posts(registry, [id_value(e.Id) for e in start_els + end_els])
    if taken:
        forms.alert(u"Post {0} already belongs to {1}. A bridge's posts cannot be used for another "
                    u"bridge - open Bridge Builder again and choose 'Edit {1}'.".format(taken[1], taken[0]),
                    exitscript=True)

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
        "design": existing["design"] if existing else BR.latest_design(registry)}

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
    output.print_md("These posts already carry **{0}** - its design is loaded; saving and "
                    "building replaces it.".format(existing_name))
output.print_md("Builder page: `{0}`. Save the design there, then run **Build Bridge**.".format(page))
