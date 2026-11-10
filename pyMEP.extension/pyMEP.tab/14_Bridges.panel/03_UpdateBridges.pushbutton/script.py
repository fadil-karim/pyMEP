# -*- coding: utf-8 -*-
"""Update Bridges - re-align tracked bridges to their posts.

For every bridge in the tracking file (or the ones you pick) the four
posts are read again and the framing members are re-curved onto the
new geometry, keeping their element ids; members a changed span adds or
drops are created or deleted. When the bridge only moved (same span,
width, height and fall) the deck moves with it and its stiffeners and
railings follow their host; otherwise the deck is rebuilt and the
stiffeners and railings made again. One transaction per bridge.

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

HOSTED = ("stiffener", "railing")
report = []
for name in chosen:
    rec = bridges[name]
    source = rec["source"]
    design = BR.normalize_design(rec["design"])
    try:
        start_posts = RV.posts_from_ids(doc, source["start_ids"])
        end_posts = RV.posts_from_ids(doc, source["end_ids"])
        new_frame = BR.frame_from_posts(start_posts, end_posts)
    except ValueError as ex:
        report.append((name, u"SKIPPED - {0}".format(ex)))
        continue
    old_frame = BR.frame_from_record(rec)
    if not BR.frame_changed(old_frame, new_frame):
        report.append((name, u"unchanged - the posts have not moved"))
        continue
    framing = RV.framing_types(doc)
    floors = RV.floor_types(doc)
    levels = RV.levels(doc)
    level = RV.first_of(levels, u"{0}".format(design.get("level") or u""), doc)
    floor_type = RV.first_of(floors, u"{0}".format(design["types"].get("floor") or u""), doc)
    design["floor_thickness_m"] = RV.floor_thickness_m(floor_type) or design["floor_thickness_m"]
    design["bottom_chord_depth_m"] = RV.section_depth_m(RV.first_of(framing, u"{0}".format(design["types"].get("bottom_chord") or u""), doc))
    stiff_id = u"{0}".format(design["types"].get("stiffener") or u"none")
    stiffener_sym = RV.by_id(doc, stiff_id) if stiff_id not in (u"", u"none") else None
    rail_id = u"{0}".format(design["types"].get("railing") or u"none")
    railing_type = RV.by_id(doc, rail_id) if rail_id not in (u"", u"none") else None
    shared_elev = RV.project_position(doc)["elev_m"]
    mems = BR.world_members(design, new_frame, shared_elev)
    deck = BR.world_deck(design, new_frame, shared_elev)
    member_ids = dict(rec.get("members", {}))
    motion = BR.rigid_motion(old_frame, new_frame)
    framing_mems = [m for m in mems if m["role"] not in HOSTED]
    hosted_mems = [m for m in mems if m["role"] in HOSTED]
    framing_keys = [k for k in member_ids if k.split(":")[0] not in HOSTED]
    diff = BR.diff_members(framing_keys, framing_mems)
    recurved = created = deleted = 0
    deck_note = u""
    t = Transaction(doc, u"pyMEP: Update bridge '{0}'".format(name))
    quiet(t)
    t.Start()
    try:
        for key, m in diff["update"]:
            el = doc.GetElement(make_id(member_ids[key]))
            if el is None or not RV.set_member_curve(el, m):
                if el is not None:
                    RV.delete_quiet(doc, [member_ids[key]])
                del member_ids[key]
                diff["create"].append(m)
                continue
            recurved += 1
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
        if floor is not None and motion is not None:
            dx, dy, dz, da = motion
            RV.move_elements(doc, [floor_id], dx, dy, dz, da, new_frame["origin"])
            deck_note = u"deck moved with its stiffeners and railings"
        else:
            # the hosted elements go with the old deck; make them again on the new one
            hosted_keys = [k for k in member_ids if k.split(":")[0] in HOSTED]
            RV.delete_quiet(doc, [member_ids.pop(k) for k in hosted_keys])
            if floor is not None:
                RV.delete_quiet(doc, [floor_id])
            try:
                floor = RV.create_deck(doc, deck, floor_type, level)
                RV.set_mark_comments(floor, name)
                floor_id = id_value(floor.Id)
                doc.Regenerate()
                deck_note = u"deck rebuilt (span, width, height or fall changed)"
            except Exception as ex:
                floor_id = None
                floor = None
                deck_note = u"deck FAILED: {0}".format(ex)
            made = 0
            for m in hosted_mems:
                try:
                    if m["role"] == "stiffener" and stiffener_sym is not None:
                        el = RV.create_stiffener(doc, m, stiffener_sym, floor, level)
                    elif m["role"] == "railing" and railing_type is not None:
                        el = RV.create_railing(doc, m, railing_type, floor, level)
                        if not RV.is_hosted_on(el, floor):
                            deck_note += u"; a railing could not be hosted on the deck"
                    else:
                        continue
                    RV.set_mark_comments(el, name)
                    member_ids[m["key"]] = id_value(el.Id)
                    made += 1
                except Exception:
                    continue
            if made:
                deck_note += u", {0} stiffener(s) / railing(s) made again".format(made)
        # the posts follow the top of steel of the top chords
        try:
            eff = BR.effective_frame(design, new_frame, shared_elev)
            zt = BR.height(design, new_frame)
            rise = 0.0
            for m in mems:
                if m["role"] == "top_chord" and m["key"] in member_ids:
                    rise = RV.rise_above_line(doc, doc.GetElement(make_id(member_ids[m["key"]])), m)
                    break
            RV.set_post_tops(doc, source["start_ids"], BR.to_world(eff, 0.0, 0.0, zt)[2] + rise)
            RV.set_post_tops(doc, source["end_ids"], BR.to_world(eff, new_frame["span"], 0.0, zt)[2] + rise)
        except Exception as ex:
            deck_note += u"; posts not adjusted: {0}".format(ex)
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
    bridges[name] = BR.record(name, design, source, new_frame, member_ids, floor_id, stamp)
    report.append((name, u"{0} member(s) re-aligned, {1} added, {2} removed; {3}; span now "
                         u"{4:.3f} m, rise {5:+.0f} mm".format(
                             recurved, created, deleted, deck_note, new_frame["span"],
                             (new_frame["z1"] - new_frame["z0"]) * 1000.0)))
try:
    BR.save_registry(registry_file, registry)
except Exception as ex:
    report.append((u"tracking file", u"could not be saved: {0}".format(ex)))

output.print_md("### Update Bridges")
output.print_table(table_data=[[n, r] for n, r in report], columns=["Bridge", "Result"])
forms.alert(u"\n".join(u"{0}: {1}".format(n, r) for n, r in report), title="Update Bridges")
