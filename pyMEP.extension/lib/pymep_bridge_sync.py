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
    for role, base in BR.TYPE_ROLES.items():
        # unset: the type of the role it stands in for (the end cross beams: the cross beams')
        wanted = u"{0}".format(design["types"].get(role) or u"")
        symbols[role] = RV.first_of(framing, wanted, doc) if wanted and wanted != u"none" else symbols.get(base)
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


def sync(doc, name, design, source, frame, old_rec, types, shared_elev, others=()):
    """Inside an open transaction, bring the bridge to design on frame,
    keeping the ids of old_rec's elements (None: everything is new).
    Returns {"member_ids", "floor_id", counts, "deck", "posts", "failed",
    "rails_made", "rails_hosted"}."""
    res = {"recurved": 0, "retyped": 0, "created": 0, "deleted": 0, "hosted_kept": 0, "ends_moved": [], "not_ours": 0,
           "reseated": 0, "joins_left_off": [], "into_posts": [], "chord_extension_typed": False,
           "steel_unchecked": 0, "chord_ext_into": 0.0, "post_size": None,
           "hosted_made": 0, "rails_made": 0, "rails_hosted": 0, "failed": [], "deck": u"", "posts": u""}
    old_rec = old_rec or {}
    mems = BR.world_members(design, frame, shared_elev)
    deck = BR.world_deck(design, frame, shared_elev)
    member_ids = dict(old_rec.get("members", {}))
    floor_id = old_rec.get("floor")
    if old_rec:
        # a recorded id that is now another element (a post, or not marked as
        # this bridge) is left alone: the member is made new instead
        cls = RV.classify(doc, old_rec, name, others)
        for key in [k for k, v in member_ids.items() if cls.get(int(v)) == "other"]:
            del member_ids[key]
            res["not_ours"] += 1
        if floor_id is not None and cls.get(int(floor_id)) == "other":
            floor_id = None
            res["not_ours"] += 1
    level = types["level"]
    syms = types["symbols"]
    for sym in set(s for s in syms.values() if s is not None):
        RV.activate(sym, doc)
    if types["stiffener"] is not None:
        RV.activate(types["stiffener"], doc)

    # framing: re-curve / re-type the members that stay, make the new ones, drop the rest
    framing_mems = [m for m in mems if m["role"] not in HOSTED]
    diff = BR.diff_members([k for k in member_ids if k.split(":")[0] not in HOSTED], framing_mems)
    # Revit drags the ends joined to a member along when the member moves, so
    # moving one member can pull a neighbour that is already in place off its
    # node: every member that stays is unjoined at both ends first, and its
    # joins are given back once all of them are on their new lines
    for key, m in diff["update"]:
        el = doc.GetElement(make_id(member_ids[key]))
        if el is not None:
            RV.set_joins(el, (0, 1), False)
    for key, m in diff["update"]:
        el = doc.GetElement(make_id(member_ids[key]))
        sym = syms.get(BR.type_role(m))
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
        RV.set_mark_comments(el, name)     # the current name, so Delete Bridges knows it after a rename
        res["recurved"] += 1
    for m in diff["create"]:
        sym = syms.get(BR.type_role(m))
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
    # every member must end on its own points: put back any that are off (a few
    # passes, still unjoined), give the joins back - except at the ends that
    # meet the posts - and check once more; one that a join moves again is put
    # back and left unjoined, and any still off is reported
    kept_keys = set(k for k, _m in diff["update"])
    placed = [m for m in framing_mems if m["key"] in member_ids]

    def _el(m):
        return doc.GetElement(make_id(member_ids[m["key"]]))

    def _regen():
        try:
            doc.Regenerate()
        except Exception:
            pass

    def _off():
        _regen()
        out = []
        for m in placed:
            el = _el(m)
            if el is not None and not RV.line_matches(el, m):
                out.append((m, el))
        return out
    for _pass in range(3):
        off = _off()
        if not off:
            break
        for m, el in off:
            RV.set_joins(el, (0, 1), False)
            RV.set_member_curve(el, m)
            res["reseated"] += 1
    for m in placed:
        if m["key"] in kept_keys:
            el = _el(m)
            if el is not None:
                RV.set_joins(el, [e for e in (0, 1) if e not in (m.get("free_ends") or [])], True)
    for m, el in _off():
        RV.set_joins(el, (0, 1), False)
        RV.set_member_curve(el, m)
        res["joins_left_off"].append(m["key"])
    res["ends_moved"] = [m["key"] for m, el in _off()]
    # the deck: keep, move or (only when its outline, fall or level changed) rebuild
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
        if not rebuild:
            RV.set_mark_comments(floor, name)
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
            RV.set_mark_comments(el, name)
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
    # moving the posts' tops can drag a chord end joined to a post along with
    # it: every member is checked against its line once more and put back
    try:
        for m, el in _off():
            RV.set_joins(el, (0, 1), False)
            RV.set_member_curve(el, m)
            res["reseated"] += 1
        res["ends_moved"] = [m["key"] for m, el in _off()]
    except Exception:
        pass
    # the steel itself, not just its line: any member that meets a post and
    # still reaches past the post's inner face (a Revit join or extension, a
    # typed chord extension) is named
    for m in placed:
        if not m.get("free_ends"):
            continue
        if m["role"] in ("top_chord", "bottom_chord") and (design.get(m["role"] + "_end") or "face") != "face":
            continue                         # set to run into the posts on purpose
        try:
            el = _el(m)
            depth = RV.into_posts(el, frame) if el is not None else None
        except Exception:
            depth = None                     # measuring must never stop a build
        if depth is None:
            res["steel_unchecked"] += 1
        elif depth > 0.0:
            res["into_posts"].append((m["key"], depth))
    res["chord_extension_typed"] = design.get("chord_extension_m") is not None
    try:
        res["chord_ext_into"] = BR.chord_into_posts(design, frame)
    except Exception:
        res["chord_ext_into"] = 0.0
    res["post_size"] = (frame.get("post_size", 0.0), BR.post_half_t(frame) * 2.0,
                        frame.get("post_from", "parameters"))

    res["member_ids"] = member_ids
    res["floor_id"] = floor_id
    res["members"] = mems
    return res


def problem_line(res):
    """The warnings of a build or update alone (u"" when none)."""
    parts = []
    if res.get("joins_left_off"):
        parts.append(u"{0} member(s) left unjoined because a Revit join moved them again: {1}".format(
            len(res["joins_left_off"]), u", ".join(res["joins_left_off"])))
    if res.get("ends_moved"):
        parts.append(u"STILL OFF THEIR LINES - check these: {0}".format(u", ".join(res["ends_moved"])))
    if res.get("into_posts") or res.get("chord_ext_into"):
        parts.append(_into_text(res))
    if res.get("post_size") and res["post_size"][2] != "geometry":
        parts.append(u"the posts' geometry could not be read: their size came from the type's parameters "
                     u"and the steel at the posts was not checked")
    elif res.get("steel_unchecked"):
        parts.append(u"the steel of {0} member(s) at the posts could not be read, so was not checked".format(
            res["steel_unchecked"]))
    return u"; ".join(parts)


def post_line(res):
    """The posts' size along x across the span and where it came from."""
    ps = res.get("post_size")
    if not ps:
        return u""
    return u"posts {0:.0f} x {1:.0f} mm ({2})".format(
        ps[0] * 1000.0, ps[1] * 1000.0,
        u"measured from their geometry" if ps[2] == "geometry" else u"from the type's size parameters")


def _into_text(res):
    parts = []
    if res.get("into_posts"):
        parts.append(u"INTO THE POSTS - steel past the post faces: {0}".format(u", ".join(
            u"{0} ({1:.0f} mm)".format(k, d * 1000.0) for k, d in res["into_posts"])))
    if res.get("chord_ext_into"):
        parts.append(u"the chord extension is typed (the 'ext' value on the elevation), running the chords "
                     u"{0:.0f} mm into the posts: blank it to stop them at the post faces".format(
                         res["chord_ext_into"] * 1000.0))
    elif res.get("into_posts") and res.get("chord_extension_typed"):
        parts.append(u"the chord extension is typed (the 'ext' value on the elevation): blank it to stop "
                     u"the chords at the post faces")
    return u" - ".join(parts)


def summary_line(res):
    """One line for a report."""
    line = (u"{0} kept and re-aligned ({1} re-typed), {2} added, {3} removed; {4}; "
            u"{5} stiffener(s)/railing(s) kept, {6} made").format(
        res["recurved"], res["retyped"], res["created"], res["deleted"], res["deck"],
        res["hosted_kept"], res["hosted_made"])
    if res.get("not_ours"):
        line += (u"; {0} recorded id(s) pointed at elements that are not this bridge's (not marked with "
                 u"its name) - left alone, those members made new").format(res["not_ours"])
    if res.get("reseated"):
        line += u"; {0} member(s) Revit had pulled off their lines put back".format(res["reseated"])
    if res.get("joins_left_off"):
        line += u"; {0} member(s) left unjoined because a Revit join moved them again: {1}".format(
            len(res["joins_left_off"]), u", ".join(res["joins_left_off"]))
    if res.get("ends_moved"):
        line += u"; STILL OFF THEIR LINES - check these: {0}".format(u", ".join(res["ends_moved"]))
    if res.get("into_posts") or res.get("chord_ext_into"):
        line += u"; " + _into_text(res)
    if post_line(res):
        line += u"; " + post_line(res)
    if res.get("post_size") and res["post_size"][2] != "geometry":
        line += u" - the steel at the posts was not checked"
    elif res.get("steel_unchecked"):
        line += u"; the steel of {0} member(s) at the posts could not be read".format(res["steel_unchecked"])
    return line


def removal_text(doc, registry, names, delete_elements=True):
    """What deleting (or forgetting) the named bridges will do, for the
    confirmation."""
    lines = []
    for name in names:
        rec = registry.get("bridges", {}).get(name)
        if rec is None:
            continue
        ours, gone, other = RV.bridge_elements(doc, rec, name, [n for n in registry.get("bridges", {}) if n != name])
        if delete_elements:
            line = u"{0}: {1} element(s) deleted".format(name, len(ours))
            if gone:
                line += u" ({0} already gone)".format(gone)
            if other:
                line += u"; {0} left alone - not marked '{1}' in Comments".format(len(other), name)
        else:
            line = u"{0}: no longer tracked; its {1} element(s) stay in the model".format(name, len(ours) + len(other))
        lines.append(line)
    if delete_elements:
        tail = (u"The posts are not touched. One undo step per bridge - but undo brings the elements "
                u"back without the tracking: build the bridge again (or use Forget instead) if unsure.")
    else:
        tail = (u"Nothing in the model changes. Its posts are free for a new bridge, and Update "
                u"Bridges no longer moves it.")
    return u"\n".join(lines) + u"\n\n" + tail


def remove(doc, registry, names, delete_elements, stamp, ts):
    """Delete the named bridges - their elements, one undo step each, and
    their records - or, with delete_elements False, only forget them (the
    elements stay, no longer tracked). Only elements still carrying the
    bridge's name are deleted, never its posts. The records are kept
    under registry["deleted"]; the caller saves the registry. Returns
    [(name, note)]."""
    from Autodesk.Revit.DB import Transaction
    from pymep_revit import quiet
    out = []
    for name in names:
        rec = registry.get("bridges", {}).get(name)
        if rec is None:
            out.append((name, u"not tracked - nothing to do"))
            continue
        if not delete_elements:
            BR.forget_bridge(registry, name, stamp, ts, False)
            out.append((name, u"forgotten - its elements stay in the model, no longer tracked"))
            continue
        t = Transaction(doc, u"pyMEP: Delete bridge '{0}'".format(name))
        quiet(t)
        t.Start()
        try:
            r = RV.delete_bridge(doc, rec, name, [n for n in registry.get("bridges", {}) if n != name])
            t.Commit()
        except Exception as ex:
            t.RollBack()
            out.append((name, u"FAILED, nothing deleted - {0}".format(ex)))
            continue
        BR.forget_bridge(registry, name, stamp, ts, True)
        note = u"{0} element(s) deleted".format(r["deleted"])
        if r["gone"]:
            note += u", {0} were already gone".format(r["gone"])
        if r["kept"]:
            shown = u", ".join(u"{0}".format(i) for i in r["kept"][:8])
            note += u"; {0} left alone - not marked '{1}' in Comments ({2}{3})".format(
                len(r["kept"]), name, shown, u", ..." if len(r["kept"]) > 8 else u"")
        out.append((name, note + u"; the posts are untouched"))
    return out


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
