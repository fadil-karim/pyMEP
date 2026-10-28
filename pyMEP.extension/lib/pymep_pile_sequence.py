# -*- coding: utf-8 -*-
"""Add to Pile Sequence - PURE PYTHON (no Revit or WPF imports) so the
CPython suite exercises it. The button does the Revit side: reading
Marks, the dialog, writing the renames.

THE SEQUENCE
  A mark is a letter prefix, a number and an optional suffix: P1604 is
  prefix 'P' number 1604; P1604-1 carries the suffix '-1'. The letters
  are ignored for ordering - the number is the sequence - but kept when
  a mark is rewritten, as is zero padding (P0012 -> P0013). Marks are
  grouped by prefix (case-insensitive) so a model with P and TP piles
  gives two sequences. A mark with a suffix, or without a number, is
  left alone and listed as ignored.

SETS
  Each set of foundations starts at a new leading digit: P1001.. is set
  1 (1000-1999), P2001.. set 2, P6001.. set 6. The tool works on ONE set
  at a time: the numbers typed must all lie in the chosen set, only that
  set's piles move, and a move that would run past the set's last number
  (P1999 -> P2000) is refused rather than spilling into the next set.

INSERTING NUMBERS
  Adding N frees the number N for a new pile:
    * N not in use (a gap): it is simply reserved - nothing moves.
    * N in use: the piles at N and above move up by one. With
      fill_gaps on (the default) the move runs up to the first unused
      number and stops there, so an existing gap absorbs it and nothing
      beyond the gap is renamed; off, every higher number moves.
  Several numbers are handled lowest first, each against the result of
  the previous one. Duplicates (two piles with the same mark) move
  together and stay flagged.

  Example: P1604, P1606, P1607, P1608 exist; add 1605 and 1606.
  1605 is a gap and is reserved; 1606 is in use, so 1606-1608 become
  1607-1609. Result: P1605 and P1606 free, P1607..P1609 renamed.
"""

import re

MARK_RE = re.compile(r"^\s*([A-Za-z]*[-_ .]?)\s*(\d+)(.*?)\s*$")
MAX_RANGE = 5000


# ---------------------------------------------------------------------------
# marks
# ---------------------------------------------------------------------------
def parse_mark(text):
    """{'prefix', 'number', 'width', 'suffix'} for a mark with a number
    in it, else None."""
    m = MARK_RE.match(u"{0}".format(text if text is not None else u""))
    if not m:
        return None
    return {"prefix": m.group(1), "number": int(m.group(2)),
            "width": len(m.group(2)), "suffix": m.group(3).strip()}


def format_mark(prefix, number, width):
    """The mark text for a number, keeping the prefix and zero padding."""
    digits = u"{0}".format(int(number))
    if len(digits) < width:
        digits = u"0" * (width - len(digits)) + digits
    return u"{0}{1}".format(prefix or u"", digits)


def parse_numbers(text):
    """'1605, 1606' / 'P1605 P1606' / '1605-1608' -> sorted unique
    numbers (letters ignored). Raises ValueError naming a token it
    cannot read."""
    out = set()
    for tok in re.split(r"[,\s;]+", u"{0}".format(text or u"").strip()):
        if not tok:
            continue
        bare = re.sub(r"[A-Za-z]", u"", tok)
        m = re.match(r"^(\d+)(?:-(\d+))?$", bare)
        if not m:
            raise ValueError(tok)
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if b < a:
            a, b = b, a
        if b - a > MAX_RANGE:
            raise ValueError(tok)
        out.update(range(a, b + 1))
    return sorted(out)


def group_marks(items):
    """items: [(id, mark)] -> (groups, ignored). groups maps the upper-
    case prefix to its entries {id, mark, prefix, number, width,
    suffix}; ignored lists {id, mark, reason} for marks with a suffix or
    no number."""
    groups = {}
    ignored = []
    for eid, mark in items:
        p = parse_mark(mark)
        if p is None:
            ignored.append({"id": eid, "mark": u"{0}".format(mark or u""),
                            "reason": u"no number"})
            continue
        if p["suffix"]:
            ignored.append({"id": eid, "mark": mark,
                            "reason": u"suffix '{0}'".format(p["suffix"])})
            continue
        e = dict(p)
        e["id"] = eid
        e["mark"] = mark
        groups.setdefault(p["prefix"].strip().upper(), []).append(e)
    return groups, ignored


def _dominant(values):
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    best = None
    for v, n in counts.items():
        if best is None or n > counts[best] or (n == counts[best] and
                                                u"{0}".format(v) < u"{0}".format(best)):
            best = v
    return best


def sequence_summary(entries):
    """{'count', 'low', 'high', 'gaps': [numbers], 'duplicates':
    {number: count}} for one prefix group ([] / None when empty)."""
    numbers = sorted(e["number"] for e in entries)
    if not numbers:
        return {"count": 0, "low": None, "high": None, "gaps": [],
                "duplicates": {}}
    have = set(numbers)
    dups = {}
    for n in numbers:
        dups[n] = dups.get(n, 0) + 1
    return {"count": len(numbers), "low": numbers[0], "high": numbers[-1],
            "gaps": [n for n in range(numbers[0], numbers[-1] + 1)
                     if n not in have],
            "duplicates": dict((n, c) for n, c in dups.items() if c > 1)}


# ---------------------------------------------------------------------------
# sets - the leading-digit blocks
# ---------------------------------------------------------------------------
def set_bounds(number, width):
    """(low, high) of the set a number sits in: the block of numbers
    sharing its leading digit at that digit width. 1604 at width 4 ->
    (1000, 1999); 2 at width 1 -> (0, 9)."""
    number = int(number)
    width = max(int(width or 1), len(u"{0}".format(number)))
    if width <= 1:
        return (0, 9)
    block = 10 ** (width - 1)
    low = (number // block) * block
    return (low, low + block - 1)


def split_sets(entries):
    """[(bounds, [entries])] of one prefix group, lowest set first."""
    sets = {}
    for e in entries:
        sets.setdefault(set_bounds(e["number"], e["width"]), []).append(e)
    return sorted(sets.items(), key=lambda kv: kv[0])


def set_label(bounds, entries):
    s = sequence_summary(entries)
    head = u"{0}-{1}  (set {2})".format(bounds[0], bounds[1],
                                        u"{0}".format(bounds[0])[0])
    if not s["count"]:
        return head + u"  -  empty"
    return u"{0}  -  {1} pile(s), {2} to {3}{4}{5}".format(
        head, s["count"], s["low"], s["high"],
        u", {0} gap(s)".format(len(s["gaps"])) if s["gaps"] else u"",
        u", {0} duplicate number(s)".format(len(s["duplicates"]))
        if s["duplicates"] else u"")


def numbers_set(numbers, width):
    """The one set every typed number falls in, or None when they span
    several sets (or there are none)."""
    found = set(set_bounds(n, width) for n in (numbers or []))
    return found.pop() if len(found) == 1 else None


def group_label(key, entries):
    s = sequence_summary(entries)
    if not s["count"]:
        return u"{0} (empty)".format(key or u"(no prefix)")
    return u"{0}  -  {1} mark(s), {2} to {3}{4}{5}".format(
        key or u"(no prefix)", s["count"], s["low"], s["high"],
        u", {0} gap(s)".format(len(s["gaps"])) if s["gaps"] else u"",
        u", {0} duplicate number(s)".format(len(s["duplicates"]))
        if s["duplicates"] else u"")


# ---------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------
def plan_insertions(entries, numbers, fill_gaps=True, bounds=None):
    """Where every pile ends up after the numbers are freed.

    entries are ONE set's piles. With bounds (low, high) given, a typed
    number outside the set, or a move that would carry a pile past the
    set's last number, raises ValueError saying so - nothing ever spills
    into the next set.

    Returns {'rows', 'new_numbers', 'renamed', 'duplicates_before',
    'duplicates_after', 'gaps_after'}. A row is {id, old, new,
    number_old, number_new, changed, kind, duplicate} with kind 'kept',
    'renamed' or 'new' (a freed number, id None, old '')."""
    wanted = sorted(set(int(n) for n in (numbers or [])))
    if bounds is not None:
        low, high = bounds
        for n in wanted:
            if n < low or n > high:
                raise ValueError(u"{0} is outside this set ({1}-{2}) - do "
                                 u"one set at a time".format(n, low, high))
    occ = {}
    for e in entries:
        occ.setdefault(e["number"], []).append(dict(e))
    before = sequence_summary(entries)
    prefix = _dominant([e["prefix"] for e in entries]) if entries else u""
    width = _dominant([e["width"] for e in entries]) if entries else 1
    for n in wanted:
        if n not in occ:
            occ[n] = [{"reserved": True}]
            continue
        if fill_gaps:
            keys = []
            k = n
            while k in occ:
                keys.append(k)
                k += 1
        else:
            keys = sorted(k for k in occ if k >= n)
        for k in reversed(keys):
            occ[k + 1] = occ.pop(k)
        occ[n] = [{"reserved": True}]
    if bounds is not None and occ and max(occ) > bounds[1]:
        pushed = [o["mark"] for o in occ[max(occ)] if not o.get("reserved")]
        raise ValueError(u"the move would carry {0} past the end of this "
                         u"set ({1} is its last number) - the set is full "
                         u"there; free a gap or use a smaller insertion"
                         .format(u", ".join(pushed) or u"a pile", bounds[1]))
    rows = []
    dup_after = {}
    for k in sorted(occ):
        occupants = occ[k]
        real = [o for o in occupants if not o.get("reserved")]
        if len(real) > 1:
            dup_after[k] = len(real)
        for o in occupants:
            if o.get("reserved"):
                rows.append({"id": None, "old": u"", "number_old": None,
                             "new": format_mark(prefix, k, width),
                             "number_new": k, "changed": True,
                             "kind": "new", "duplicate": False})
                continue
            changed = (k != o["number"])
            rows.append({"id": o["id"], "old": o["mark"],
                         "number_old": o["number"],
                         "new": (format_mark(o["prefix"], k, o["width"])
                                 if changed else o["mark"]),
                         "number_new": k, "changed": changed,
                         "kind": "renamed" if changed else "kept",
                         "duplicate": len(real) > 1})
    all_nums = sorted(occ)
    gaps_after = ([n for n in range(all_nums[0], all_nums[-1] + 1)
                   if n not in occ] if all_nums else [])
    return {"rows": rows,
            "new_numbers": [r["number_new"] for r in rows if r["kind"] == "new"],
            "renamed": len([r for r in rows if r["kind"] == "renamed"]),
            "duplicates_before": before["duplicates"],
            "duplicates_after": dup_after,
            "gaps_after": gaps_after}


def apply_order(rows):
    """The renames, highest new number first, so no two piles share a
    mark part-way through the write."""
    return sorted([r for r in rows if r["kind"] == "renamed"],
                  key=lambda r: -r["number_new"])


# ---------------------------------------------------------------------------
# the preview page
# ---------------------------------------------------------------------------
def _esc(text):
    return (u"{0}".format(text if text is not None else u"")
            .replace(u"&", u"&amp;").replace(u"<", u"&lt;")
            .replace(u">", u"&gt;").replace(u'"', u"&quot;"))


_CSS = u"""
body{font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#222;margin:0;background:#fff}
header{background:#f4f4f4;padding:14px 20px;border-bottom:1px solid #ddd}
h1{font-size:18px;margin:0 0 4px 0}
.meta{color:#666;font-size:12px}
.sum{display:flex;flex-wrap:wrap;gap:10px;padding:12px 20px}
.sum div{border:1px solid #ddd;border-radius:4px;padding:6px 10px;min-width:110px}
.sum b{display:block;font-size:18px}
.legend{padding:0 20px 8px 20px;font-size:12px;color:#444}
.legend span{display:inline-block;padding:2px 8px;margin-right:8px;border-radius:3px}
main{padding:0 20px 30px 20px}
table{border-collapse:collapse;width:100%;margin-top:10px}
th,td{border:1px solid #e3e3e3;padding:4px 8px;text-align:left;white-space:nowrap}
th{background:#f7f7f7;position:sticky;top:0}
tr.renamed td{background:#fff3b0}
tr.new td{background:#d9f2d9;font-weight:600}
tr.dup td{color:#b00020;font-weight:600}
tr.dup td.mark{border-left:4px solid #b00020}
tr.gap td{color:#999;font-style:italic;background:#fafafa}
tr.ignored td{color:#777}
td.num{text-align:right;font-variant-numeric:tabular-nums}
h2{font-size:14px;margin:22px 0 4px 0}
"""


def preview_html(title, meta, plan, ignored=None, show_unchanged=True):
    """A self-contained page: the sequence after the plan, one row per
    pile, changed rows highlighted, duplicates in red, freed numbers in
    green, remaining gaps as muted rows, ignored marks listed below."""
    rows = plan["rows"]
    renamed = plan["renamed"]
    kept = len([r for r in rows if r["kind"] == "kept"])
    newn = len(plan["new_numbers"])
    dups = plan["duplicates_after"]
    gaps = set(plan["gaps_after"])
    out = [u"<!DOCTYPE html><html><head><meta charset='utf-8'>",
           u"<title>{0}</title><style>{1}</style></head><body>".format(
               _esc(title), _CSS),
           u"<header><h1>{0}</h1><div class='meta'>{1}</div></header>".format(
               _esc(title),
               u" &nbsp;|&nbsp; ".join(u"{0}: {1}".format(_esc(k), _esc(v))
                                        for k, v in (meta or []))),
           u"<div class='sum'>"
           u"<div><b>{0}</b>renamed</div><div><b>{1}</b>unchanged</div>"
           u"<div><b>{2}</b>number(s) freed</div><div><b>{3}</b>duplicate "
           u"number(s)</div><div><b>{4}</b>gap(s) left</div><div><b>{5}</b>"
           u"ignored</div></div>".format(renamed, kept, newn, len(dups),
                                         len(gaps), len(ignored or [])),
           u"<div class='legend'><span style='background:#fff3b0'>renamed</span>"
           u"<span style='background:#d9f2d9'>freed for a new pile</span>"
           u"<span style='color:#b00020;border:1px solid #b00020'>duplicate "
           u"mark</span><span style='color:#999;border:1px solid #ddd'>gap"
           u"</span></div><main>"]
    if plan["new_numbers"]:
        out.append(u"<p>Freed: <b>{0}</b></p>".format(
            _esc(u", ".join(u"{0}".format(n) for n in plan["new_numbers"]))))
    out.append(u"<table><tr><th>Number</th><th>Old mark</th><th>New mark</th>"
               u"<th>Change</th><th>Element id</th></tr>")
    by_num = {}
    for r in rows:
        by_num.setdefault(r["number_new"], []).append(r)
    nums = sorted(set(list(by_num.keys()) + list(gaps)))
    for n in nums:
        if n in gaps:
            out.append(u"<tr class='gap'><td class='num'>{0}</td><td>-</td>"
                       u"<td>-</td><td>gap</td><td></td></tr>".format(n))
            continue
        for r in by_num[n]:
            if r["kind"] == "kept" and not show_unchanged:
                continue
            cls = [r["kind"]] if r["kind"] != "kept" else []
            if r["duplicate"]:
                cls.append(u"dup")
            if r["kind"] == "new":
                change = u"freed for a new pile"
            elif r["kind"] == "renamed":
                change = u"renamed ({0:+d})".format(r["number_new"] - r["number_old"])
            else:
                change = u"unchanged"
            if r["duplicate"]:
                change += u" - DUPLICATE x{0}".format(dups.get(n, 2))
            out.append(u"<tr class='{0}'><td class='num'>{1}</td><td class='mark'>"
                       u"{2}</td><td class='mark'>{3}</td><td>{4}</td><td>{5}"
                       u"</td></tr>".format(
                           u" ".join(cls), n, _esc(r["old"]), _esc(r["new"]),
                           _esc(change),
                           _esc(r["id"]) if r["id"] is not None else u""))
    out.append(u"</table>")
    if not show_unchanged:
        out.append(u"<p class='meta'>Unchanged piles are hidden.</p>")
    if ignored:
        out.append(u"<h2>Ignored ({0})</h2><p class='meta'>Left exactly as "
                   u"they are: marks with a suffix such as -1, and marks "
                   u"without a number.</p><table><tr><th>Mark</th><th>Why"
                   u"</th><th>Element id</th></tr>".format(len(ignored)))
        for g in sorted(ignored, key=lambda g: u"{0}".format(g["mark"])):
            out.append(u"<tr class='ignored'><td>{0}</td><td>{1}</td><td>{2}"
                       u"</td></tr>".format(_esc(g["mark"]), _esc(g["reason"]),
                                            _esc(g["id"])))
        out.append(u"</table>")
    out.append(u"</main></body></html>")
    return u"".join(out)
