# -*- coding: utf-8 -*-
"""Bring a bridge in the model to a design on its posts, keeping element
ids - the one path Build Bridge and Update Bridges both take, so the
dimensions and tags on a bridge survive design changes as well as post
moves.

Every tracked framing member whose key is still in the design is put on
its new line, re-typed, re-justified and rolled in place; members the
design no longer has are deleted and new ones made. The deck floor is
kept when its outline and fall are unchanged (re-typed in place if the
floor type changed), moved with its hosted elements when it only slid or
turned, and rebuilt only when its outline, fall or level changed - its
stiffeners and railings are then made again on it; otherwise they are
kept, moved onto their new lines and re-typed. The posts are set to the
top chord's top of steel.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

import pymep_bridge as BR
import pymep_bridge_revit as RV
from pymep_revit import id_value, make_id

HOSTED = ("stiffener", "railing")


def resolve(doc, design, frame):
    """The model's types and level for a design, with the values read from
    the types (floor thickness, chord and cross beam depths) written into
    design. ValueError when the model lacks what a bridge needs."""
    framing = RV.framing_types(doc)
    floors = RV.floor_types(doc)
    levels = RV.levels(doc)
    if not framing:
        raise ValueError(u"No structural framing family is loaded.")
    if not floors or not levels:
        raise ValueError(u"The model needs a floor type and a level for the deck.")
    symbols = {}
    for role in BR.FRAMING_ROLES:
        symbols[role] = RV.first_of(framing, u"{0}".format(design["types"].get(role) or u""), doc)
    floor_type = RV.first_of(floors, u"{0}".format(design["types"].get("floor") or u""), doc)
    design["floor_thickness_m"] = RV.floor_thickness_m(floor_type) or design["floor_thickness_m"]
    design["bottom_chord_depth_m"] = RV.section_depth_m(symbols.get("bottom_chord"))
    design["crossbeam_depth_m"] = RV.section_depth_m(symbols.get("crossbeam"))
    stiff_id = u"{0}".format(design["types"].get("stiffener") or u"none")
    rail_id = u"{0}".format(design["types"].get("railing") or u"none")
    level = RV.first_of(levels, u"{0}".format(design.get("level") or u""), doc)
    if level is None:
        below = [l for l in levels if l["elevation_m"] <= frame["z0"]]
        level = RV.by_id(doc, (below[-1] if below else levels[0])["id"])
    return {"symbols": symbols, "floor_type": floor_type, "level": level,
            "stiffener": RV.by_id(doc, stiff_id) if stiff_id not in (u"", u"none") else None,
            "railing": RV.by_id(doc, rail_id) if rail_id not in (u"", u"none") else None}


def _old_deck(old_rec, shared_elev):
    try:
        return BR.world_deck(BR.normalize_design(old_rec["design"]), BR.frame_from_record(old_rec), shared_elev)
    except Exception:
        return None


def sync(doc, name, design, source, frame, old_rec, types, shared_elev):
    """Inside an open transaction, bring the bridge to design on frame,
    keeping the ids of old_rec's elements (None: everything is new).
    Returns {"member_ids", "floor_id", counts, "deck", "posts", "failed",
    "rails_made", "rails_hosted"}."""
    res = {"recurved": 0, "retyped": 0, "created": 0, "deleted": 0, "hosted_kept": 0,
           "hosted_made": 0, "rails_made": 0, "rails_hosted": 0, "failed": [], "deck": u"", "posts": u""}
    old_rec = old_rec or {}
    mems = BR.world_members(design, frame, shared_elev)
    deck = BR.world_deck(design, frame, shared_elev)
    member_ids = dict(old_rec.get("members", {}))
    level = types["level"]
    syms = types["symbols"]
    for sym in set(s for s in syms.values() if s is not None):
        RV.activate(sym, doc)
    if types["stiffener"] is not None:
        RV.activate(types["stiffener"], doc)

    # framing: re-curve / re-type the members that stay, make the new ones, drop the rest
    framing_mems = [m for m in mems if m["role"] not in HOSTED]
    diff = BR.diff_members([k for k in member_ids if k.split(":")[0] not in HOSTED], framing_mems)
    for key, m in diff["update"]:
        el = doc.GetElement(make_id(member_ids[key]))
        sym = syms.get(m["role"])
        if el is None or sym is None or not RV.set_member_curve(el, m):
            if el is not None:
                RV.delete_quiet(doc, [member_ids[key]])
            del member_ids[key]
            if sym is not None:
                diff["create"].append(m)
            continue
        if RV.retype(el, sym):
            res["retyped"] += 1
        if m.get("zjust") == "top":
            RV.set_top_justified(el)
        res["recurved"] += 1
    for m in diff["create"]:
        sym = syms.get(m["role"])
        if sym is None:
            res["failed"].append((m, u"no framing type"))
            continue
        try:
            el = RV.create_member(doc, m, sym, level)
            RV.set_mark_comments(el, name)
            member_ids[m["key"]] = id_value(el.Id)
            res["created"] += 1
        except Exception as ex:
            res["failed"].append((m, u"{0}".format(ex)))
    for key in diff["delete"]:
        res["deleted"] += RV.delete_quiet(doc, [member_ids.pop(key)])

    # the deck: keep, move or (only when its outline, fall or level changed) rebuild
    floor_id = old_rec.get("floor")
    floor = doc.GetElement(make_id(floor_id)) if floor_id else None
    rebuild = floor is None
    if floor is not None:
        same_level = u"{0}".format(BR.normalize_design(old_rec["design"]).get("level") or u"") == \
            u"{0}".format(design.get("level") or u"")
        motion = BR.deck_motion(_old_deck(old_rec, shared_elev), deck) if same_level else None
        if motion == "same":
            res["deck"] = u"deck kept"
        elif motion is not None:
            dx, dy, dz, da, about = motion
            RV.move_elements(doc, [floor_id], dx, dy, dz, da, about)
            res["deck"] = u"deck moved with its stiffeners and railings"
        else:
            rebuild = True
        if not rebuild and RV.retype(floor, types["floor_type"]):
            res["deck"] += u", re-typed"
    if rebuild:
        gone = [k for k in member_ids if k.split(":")[0] in HOSTED]
        RV.delete_quiet(doc, [member_ids.pop(k) for k in gone])
        had_floor = floor is not None
        if had_floor:
            RV.delete_quiet(doc, [floor_id])
        try:
            floor = RV.create_deck(doc, deck, types["floor_type"], level)
            RV.set_mark_comments(floor, name)
            floor_id = id_value(floor.Id)
            doc.Regenerate()
            res["deck"] = u"deck rebuilt (its outline, fall or level changed)" if had_floor else u"deck made"
        except Exception as ex:
            floor = None
            floor_id = None
            res["deck"] = u"deck FAILED: {0}".format(ex)
            res["failed"].append(({"key": "deck", "role": "deck"}, u"{0}".format(ex)))

    # stiffeners and railings: kept and moved / re-typed where the deck stayed, else made
    hosted_mems = [m for m in mems if m["role"] in HOSTED]
    hd = BR.diff_members([k for k in member_ids if k.split(":")[0] in HOSTED], hosted_mems)
    for key, m in hd["update"]:
        el = doc.GetElement(make_id(member_ids[key]))
        if el is not None and RV.update_hosted(doc, el, m, types["stiffener"], types["railing"], level):
            res["hosted_kept"] += 1
            continue
        if el is not None:
            RV.delete_quiet(doc, [member_ids[key]])
        del member_ids[key]
        hd["create"].append(m)
    for m in hd["create"]:
        try:
            if m["role"] == "stiffener":
                if types["stiffener"] is None:
                    continue
                el = RV.create_stiffener(doc, m, types["stiffener"], floor, level)
            else:
                if types["railing"] is None:
                    continue
                el = RV.create_railing(doc, m, types["railing"], floor, level)
                res["rails_made"] += 1
                if RV.is_hosted_on(el, floor):
                    res["rails_hosted"] += 1
            RV.set_mark_comments(el, name)
            member_ids[m["key"]] = id_value(el.Id)
            res["hosted_made"] += 1
        except Exception as ex:
            res["failed"].append((m, u"{0}".format(ex)))
    for key in hd["delete"]:
        res["deleted"] += RV.delete_quiet(doc, [member_ids.pop(key)])

    # the posts reach the top of steel of the top chords
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
        n = RV.set_post_tops(doc, source["start_ids"], z_start) + RV.set_post_tops(doc, source["end_ids"], z_end)
        res["posts"] = u"{0} post(s) set to the top of steel of the top chord ({1:.3f} / {2:.3f} m)".format(
            n, z_start + shared_elev, z_end + shared_elev)
    except Exception as ex:
        res["posts"] = u"posts not adjusted: {0}".format(ex)
    res["member_ids"] = member_ids
    res["floor_id"] = floor_id
    res["members"] = mems
    return res


def summary_line(res):
    """One line for a report."""
    return (u"{0} kept and re-aligned ({1} re-typed), {2} added, {3} removed; {4}; "
            u"{5} stiffener(s)/railing(s) kept, {6} made").format(
        res["recurved"], res["retyped"], res["created"], res["deleted"], res["deck"],
        res["hosted_kept"], res["hosted_made"])


def design_candidates(folders):
    """[(path, mtime, parsed design file)] for every readable bridge_*.json
    in folders."""
    import glob
    import io
    import os
    out = []
    seen = set()
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        for p in glob.glob(os.path.join(folder, "bridge_*.json")):
            key = os.path.normcase(os.path.abspath(p))
            if key in seen:
                continue
            seen.add(key)
            try:
                with io.open(p, "r", encoding="utf-8") as f:
                    parsed = BR.parse_design_file(f.read())
                out.append((p, os.path.getmtime(p), parsed))
            except Exception:
                continue
    return out


def design_folders(registry):
    """Where design files are looked for: Downloads and the folders of the
    design files the bridges were last built from."""
    import os
    folders = []
    try:
        from pymep_config import get_downloads_folder
        folders.append(get_downloads_folder())
    except Exception:
        pass
    for rec in registry.get("bridges", {}).values():
        p = rec.get("design_file")
        if p:
            folders.append(os.path.dirname(p))
    return folders


def bridge_rows(doc, registry, candidates):
    """The Bridges dialog rows, the status also saying when a newer design
    has been saved for a bridge than the one built."""
    rows = RV.bridge_rows(doc, registry)
    for r in rows:
        rec = registry.get("bridges", {}).get(r["name"], {})
        if newer_design(rec, candidates) is not None:
            r["status"] = u"newer design saved" if r["status"] == u"unchanged" else r["status"] + u", newer design saved"
    return rows


def newer_design(rec, candidates):
    """The design file saved for this bridge since it was last built or
    updated, when it differs from the built design: (path, mtime, parsed)
    or None."""
    pick = BR.pick_design_file(candidates, rec.get("source", {}), float(rec.get("design_mtime") or 0.0))
    if pick is None or not BR.design_differs(pick[2]["design"], rec.get("design") or {}):
        return None
    return pick
