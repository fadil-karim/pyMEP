# -*- coding: utf-8 -*-
"""Update Bridges - bring tracked bridges to their posts and their newest
saved design, in place.

For the bridges ticked in the Bridges dialog the four posts are read
again and the newest design file saved for each bridge (if it differs
from the one built) is taken, then pymep_bridge_sync brings the model
to it keeping element ids, so dimensions and tags survive: members are
re-curved and re-typed in place, the deck kept, moved or (only when its
outline, fall or level changed) rebuilt. One transaction per bridge.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

__title__ = "Update\nBridges"
__author__ = "Glent Group"

import datetime
import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import Transaction

from pyrevit import revit, forms, script

import pymep_bridge as BR
import pymep_bridge_dialog as DLG
import pymep_bridge_revit as RV
import pymep_bridge_sync as SYNC
from pymep_revit import id_value, make_id, quiet

output = script.get_output()
doc = revit.doc

registry_file = RV.registry_path(doc)
registry = BR.load_registry(registry_file)
bridges = registry["bridges"]
if not bridges:
    forms.alert("No bridge has been built in this model yet (nothing tracked in {0}).".format(
        registry_file), exitscript=True)
cands = SYNC.design_candidates(SYNC.design_folders(registry))
rows = SYNC.bridge_rows(doc, registry, cands)
choice = DLG.choose(
    rows, "update",
    intro=u"Tick the bridges to update. Each is brought to its posts as they are now and to the newest "
          u"design saved for it, in place: its elements keep their ids, so dimensions and tags stay.",
    hint=u"'posts moved': the posts are not where the bridge was built. 'newer design saved': Bridge "
         u"Builder saved changes that are not built yet. 'unchanged': nothing to do. 'posts missing': "
         u"a post is gone, so that bridge cannot be updated.")
if choice is None:
    script.exit()
chosen = [n for n in choice[1] if n in bridges]

report = []
for name in chosen:
    rec = bridges[name]
    source = rec["source"]
    newer = SYNC.newer_design(rec, cands)
    design = BR.normalize_design(newer[2]["design"] if newer else rec["design"])
    try:
        start_posts = RV.posts_from_ids(doc, source["start_ids"])
        end_posts = RV.posts_from_ids(doc, source["end_ids"])
        new_frame = BR.frame_from_posts(start_posts, end_posts)
    except ValueError as ex:
        report.append((name, u"SKIPPED - {0}".format(ex)))
        continue
    if newer is None and not BR.frame_changed(BR.frame_from_record(rec), new_frame):
        report.append((name, u"unchanged - the posts have not moved and no newer design has been saved"))
        continue
    try:
        types = SYNC.resolve(doc, design, new_frame)
    except ValueError as ex:
        report.append((name, u"SKIPPED - {0}".format(ex)))
        continue
    shared_elev = RV.project_position(doc)["elev_m"]
    t = Transaction(doc, u"pyMEP: Update bridge '{0}'".format(name))
    quiet(t)
    t.Start()
    try:
        res = SYNC.sync(doc, name, design, source, new_frame, rec, types, shared_elev)
        t.Commit()
    except Exception as ex:
        t.RollBack()
        report.append((name, u"FAILED, nothing changed - {0}".format(ex)))
        continue
    try:
        new_frame = BR.frame_from_posts(RV.posts_from_ids(doc, source["start_ids"]), RV.posts_from_ids(doc, source["end_ids"]))
    except Exception:
        pass
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    extra = {"design_file": newer[0], "design_mtime": newer[1]} if newer else \
        {"design_file": rec.get("design_file"), "design_mtime": rec.get("design_mtime")}
    bridges[name] = BR.record(name, design, source, new_frame, res["member_ids"], res["floor_id"], stamp, extra)
    note = SYNC.summary_line(res)
    if newer:
        note = u"design from {0}; ".format(os.path.basename(newer[0])) + note
    if res["failed"]:
        note += u"; {0} failed".format(len(res["failed"]))
    report.append((name, note + u"; span {0:.3f} m".format(new_frame["span"])))
try:
    BR.save_registry(registry_file, registry)
except Exception as ex:
    report.append((u"tracking file", u"could not be saved: {0}".format(ex)))

output.print_md("### Update Bridges")
output.print_table(table_data=[[n, r] for n, r in report], columns=["Bridge", "Result"])
forms.alert(u"\n".join(u"{0}: {1}".format(n, r) for n, r in report), title="Update Bridges")
