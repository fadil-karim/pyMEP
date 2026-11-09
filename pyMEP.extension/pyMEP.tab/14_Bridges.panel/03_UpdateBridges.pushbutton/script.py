# -*- coding: utf-8 -*-
"""Update Bridges - re-align tracked bridges to their end points.

For every bridge in the tracking file (or the ones you pick) the start
and end instances are read again - position and deck level - and the
members are re-curved onto the new geometry, keeping their element ids;
members a changed span adds or drops are created or deleted. The deck
floor moves with the bridge when the span and the level difference are
unchanged, else it is rebuilt. One transaction per bridge.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

__title__ = "Update\nBridges"
__author__ = "Glent Group"

import datetime
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import Transaction

from pyrevit import revit, forms, script

import pymep_bridge as BR
import pymep_bridge_revit as RV
from pymep_revit import id_value, make_id, quiet

output = script.get_output()
doc = revit.doc

registry_file = RV.registry_path(doc)
registry = BR.load_registry(registry_file)
bridges = registry["bridges"]
if not bridges:
    forms.alert("No bridge has been built in this model yet (nothing tracked in {0}).".format(
        registry_file), exitscript=True)
names = sorted(bridges)
chosen = names
if len(names) > 1:
    chosen = forms.SelectFromList.show(names, title="Which bridges to update?",
                                       button_name="Update", multiselect=True)
    if not chosen:
        script.exit()

position = RV.project_position(doc)
report = []
for name in chosen:
    rec = bridges[name]
    source = rec["source"]
    design = BR.normalize_design(rec["design"])
    start_el = doc.GetElement(make_id(int(source["start_id"])))
    end_el = doc.GetElement(make_id(int(source["end_id"])))
    if start_el is None or end_el is None:
        report.append((name, u"SKIPPED - a point is gone (ids {0}, {1})".format(
            source["start_id"], source["end_id"])))
        continue
    try:
        p_start = RV.end_point(doc, start_el, source["level_param"], source["level_mode"], position)
        p_end = RV.end_point(doc, end_el, source["level_param"], source["level_mode"], position)
        new_frame = BR.frame_from_points(p_start, p_end)
    except ValueError as ex:
        report.append((name, u"SKIPPED - {0}".format(ex)))
        continue
    old_frame = BR.frame_from_record(rec)
    if not BR.frame_changed(old_frame, new_frame):
        report.append((name, u"unchanged - the points have not moved"))
        continue
    mems = BR.world_members(design, new_frame)
    deck = BR.world_deck(design, new_frame)
    diff = BR.diff_members(list(rec.get("members", {}).keys()), mems)
    member_ids = dict(rec.get("members", {}))
    framing = RV.framing_types(doc)
    floors = RV.floor_types(doc)
    levels = RV.levels(doc)
    level = RV.first_of(levels, u"{0}".format(design.get("level") or u""), doc)
    floor_type = RV.first_of(floors, u"{0}".format(design["types"].get("floor") or u""), doc)
    moved = recurved = created = deleted = 0
    deck_note = u""
    t = Transaction(doc, u"pyMEP: Update bridge '{0}'".format(name))
    quiet(t)
    t.Start()
    try:
        for key, m in diff["update"]:
            el = doc.GetElement(make_id(member_ids[key]))
            if el is None:
                diff["create"].append(m)
                del member_ids[key]
                continue
            if RV.set_member_curve(el, m):
                recurved += 1
            else:
                RV.delete_quiet(doc, [member_ids[key]])
                del member_ids[key]
                diff["create"].append(m)
        for m in diff["create"]:
            sym = RV.first_of(framing, u"{0}".format(design["types"].get(m["role"]) or u""), doc)
            if sym is None:
                continue
            RV.activate(sym, doc)
            try:
                el = RV.create_member(doc, m, sym, level)
                RV.set_mark_comments(el, name)
                member_ids[m["key"]] = id_value(el.Id)
                created += 1
            except Exception:
                continue
        for key in diff["delete"]:
            deleted += RV.delete_quiet(doc, [member_ids.pop(key)])
        floor_id = rec.get("floor")
        floor = doc.GetElement(make_id(floor_id)) if floor_id else None
        motion = BR.rigid_motion(old_frame, new_frame)
        if floor is not None and motion is not None:
            dx, dy, dz, da = motion
            RV.move_elements(doc, [floor_id], dx, dy, dz, da, new_frame["origin"])
            deck_note = u"deck moved"
        else:
            if floor is not None:
                RV.delete_quiet(doc, [floor_id])
            try:
                floor = RV.create_deck(doc, deck, floor_type, level)
                RV.set_mark_comments(floor, name)
                floor_id = id_value(floor.Id)
                deck_note = u"deck rebuilt (span or fall changed)" if floor is not None else u""
            except Exception as ex:
                floor_id = None
                deck_note = u"deck FAILED: {0}".format(ex)
        t.Commit()
    except Exception as ex:
        t.RollBack()
        report.append((name, u"FAILED, nothing changed - {0}".format(ex)))
        continue
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    bridges[name] = BR.record(name, design, source, new_frame, member_ids, floor_id, stamp)
    report.append((name, u"{0} member(s) re-aligned, {1} added, {2} removed; {3}; span now "
                         u"{4:.3f} m, rise {5:+.0f} mm".format(
                             recurved, created, deleted, deck_note, new_frame["span"],
                             (p_end[2] - p_start[2]) * 1000.0)))
try:
    BR.save_registry(registry_file, registry)
except Exception as ex:
    report.append((u"tracking file", u"could not be saved: {0}".format(ex)))

output.print_md("### Update Bridges")
output.print_table(table_data=[[n, r] for n, r in report], columns=["Bridge", "Result"])
forms.alert(u"\n".join(u"{0}: {1}".format(n, r) for n, r in report), title="Update Bridges")
