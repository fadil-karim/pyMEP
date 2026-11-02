# -*- coding: utf-8 -*-
"""CSV to Parameter - the pure part (no Revit): reading the CSV file,
matching its rows to elements on a key, planning the values to write
and the preview page.

The Revit side (pyMEP.tab/05_Parameters.panel/CsvToParameter) collects
the elements of a category, reads the match parameter and the current
value of the parameter to set from each, and hands them here as plain
dicts; what comes back is a plan of rows it then writes in one
transaction.

Runs under IronPython 2.7 and CPython 3: .format() only, no csv module
(its byte/text behaviour differs between the two), LF endings.
"""

import io
import re
import sys

DELIMITERS = (u",", u";", u"\t", u"|")

# ---------------------------------------------------------------------------
# 1. Reading the file
# ---------------------------------------------------------------------------


def decode_bytes(data):
    """Text from the raw bytes of a CSV: BOM-marked UTF-8 / UTF-16, then
    plain UTF-8, then Windows-1252 (what Excel writes without a BOM)."""
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", "replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        pass
    try:
        return data.decode("cp1252")
    except (UnicodeDecodeError, ValueError, LookupError):
        return data.decode("latin-1", "replace")


def sniff_delimiter(text):
    """The delimiter the first line uses most - comma when in doubt."""
    for line in text.splitlines():
        if line.strip():
            counts = [(line.count(d), -i, d) for i, d in enumerate(DELIMITERS)]
            counts.sort(reverse=True)
            return counts[0][2] if counts[0][0] > 0 else u","
    return u","


def parse_text(text, delimiter=None):
    """Rows (lists of unicode cells) from CSV text: quoted cells may hold
    the delimiter, doubled quotes and line breaks; CR, LF and CRLF all
    end a row; rows that are blank throughout are dropped."""
    text = u"{0}".format(text)
    if text[:1] == u"﻿":
        text = text[1:]
    delim = delimiter or sniff_delimiter(text)
    rows = []
    row = []
    cell = []
    quoted = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quoted:
            if ch == u'"':
                if i + 1 < n and text[i + 1] == u'"':
                    cell.append(u'"')
                    i += 1
                else:
                    quoted = False
            else:
                cell.append(ch)
        elif ch == u'"':
            quoted = True
        elif ch == delim:
            row.append(u"".join(cell))
            cell = []
        elif ch == u"\r" or ch == u"\n":
            if ch == u"\r" and i + 1 < n and text[i + 1] == u"\n":
                i += 1
            row.append(u"".join(cell))
            rows.append(row)
            row = []
            cell = []
        else:
            cell.append(ch)
        i += 1
    if cell or row:
        row.append(u"".join(cell))
        rows.append(row)
    return [r for r in rows if any(c.strip() for c in r)]


def unique_headers(cells):
    """Header names made usable: stripped, blanks named by position and
    repeats numbered - 'Mark', 'Mark (2)'."""
    out = []
    seen = {}
    for i, c in enumerate(cells):
        name = u"{0}".format(c).strip() or u"Column {0}".format(i + 1)
        base = name
        k = seen.get(base.lower(), 0)
        if k:
            name = u"{0} ({1})".format(base, k + 1)
        seen[base.lower()] = k + 1
        out.append(name)
    return out


def read_table(path):
    """(headers, rows) from a CSV file; every row is padded or trimmed to
    the header width. ValueError when the file has no header row."""
    with io.open(path, "rb") as f:
        data = f.read()
    return table_from_text(decode_bytes(data))


def table_from_text(text, delimiter=None):
    rows = parse_text(text, delimiter)
    if not rows:
        raise ValueError("the file is empty")
    headers = unique_headers(rows[0])
    width = len(headers)
    body = []
    for r in rows[1:]:
        r = [u"{0}".format(c).strip() for c in r]
        if len(r) < width:
            r = r + [u""] * (width - len(r))
        body.append(r[:width])
    return headers, body


def column_index(headers, name):
    """Index of a header, matched exactly then without case; -1 if none."""
    if name is None:
        return -1
    name = u"{0}".format(name)
    if name in headers:
        return headers.index(name)
    low = name.strip().lower()
    for i, h in enumerate(headers):
        if h.strip().lower() == low:
            return i
    return -1


def guess_column(headers, wanted, avoid=None):
    """The header that best matches a parameter name (same text, then one
    containing it, then containing any of its words), else the first
    header not in avoid, else the first; None with no headers."""
    if not headers:
        return None
    avoid = set(avoid or [])
    i = column_index(headers, wanted)
    if i >= 0:
        return headers[i]
    low = u"{0}".format(wanted or u"").strip().lower()
    if low:
        for h in headers:
            if low in h.lower() or h.lower() in low:
                return h
        words = [w for w in re.split(r"[^a-z0-9]+", low) if len(w) > 2]
        for h in headers:
            hl = h.lower()
            if any(w in hl for w in words):
                return h
    for h in headers:
        if h not in avoid:
            return h
    return headers[0]


# ---------------------------------------------------------------------------
# 2. Keys and values
# ---------------------------------------------------------------------------

_INT_LIKE = re.compile(r"^-?\d+(?:\.0+)?$")
_NUMBER = re.compile(r"^\s*([-+]?(?:\d[\d,]*)?(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*"
                     r"([^\d\s,.][^\d]*)?\s*$")
_WS = re.compile(r"\s+")


def norm_key(text, ignore_case=True):
    """A match key: trimmed, inner runs of whitespace collapsed, a whole
    number written as 1604.0 (Excel) read as 1604, case folded when
    ignore_case."""
    if text is None:
        return u""
    s = _WS.sub(u" ", u"{0}".format(text)).strip()
    if _INT_LIKE.match(s):
        s = s.split(u".")[0]
        if s in (u"-0",):
            s = u"0"
    return s.lower() if ignore_case else s


def to_number(text):
    """A float from a cell such as '1250', '1,250.5', '150 kN', '-3.2e2'
    or '12,5' (a lone comma followed by 1-2 digits is a decimal comma);
    None when there is no number in it."""
    if text is None:
        return None
    s = u"{0}".format(text).strip()
    if not s:
        return None
    if re.match(r"^[-+]?\d+,\d{1,2}$", s):
        s = s.replace(u",", u".")
    m = _NUMBER.match(s)
    if not m or not m.group(1) or m.group(1) in (u"-", u"+", u"."):
        return None
    num = m.group(1).replace(u",", u"")
    try:
        return float(num)
    except ValueError:
        return None


def to_integer(text):
    """(ok, value, why) for a whole-number parameter."""
    v = to_number(text)
    if v is None:
        return False, None, u"not a number"
    if abs(v - round(v)) > 1e-9:
        return False, None, u"not a whole number"
    return True, int(round(v)), u""


_YES = (u"yes", u"y", u"true", u"t", u"1", u"on", u"x", u"checked")
_NO = (u"no", u"n", u"false", u"f", u"0", u"off", u"", u"unchecked")


def to_yesno(text):
    """(ok, 1 or 0, why) for a Yes/No parameter."""
    s = u"{0}".format(text if text is not None else u"").strip().lower()
    if s in _YES:
        return True, 1, u""
    if s in _NO and s != u"":
        return True, 0, u""
    return False, None, u"not yes/no"


def same_value(old, new):
    """True when the current value already reads as the CSV cell: same
    text (whitespace and case aside) or the same number - '1,250.00 kN'
    on the element against '1250' in the file."""
    if norm_key(old) == norm_key(new):
        return True
    a = to_number(old)
    b = to_number(new)
    if a is None or b is None:
        return False
    scale = max(abs(a), abs(b), 1.0)
    return abs(a - b) <= 1e-6 * scale


# ---------------------------------------------------------------------------
# 3. The lookup and the plan
# ---------------------------------------------------------------------------


def build_lookup(headers, rows, key_col, value_col, ignore_case=True):
    """The CSV as {key: value} plus what was odd about it:
    duplicates - keys on more than one row (a conflict when the values
    differ; the LAST row wins), blank_keys - rows with no key."""
    ki = column_index(headers, key_col)
    vi = column_index(headers, value_col)
    if ki < 0:
        raise ValueError(u"no column called '{0}'".format(key_col))
    if vi < 0:
        raise ValueError(u"no column called '{0}'".format(value_col))
    mapping = {}
    shown = {}
    seen = {}
    blank = 0
    for r in rows:
        key = norm_key(r[ki], ignore_case)
        if not key:
            blank += 1
            continue
        seen.setdefault(key, []).append(r[vi])
        mapping[key] = r[vi]
        shown.setdefault(key, u"{0}".format(r[ki]).strip())
    duplicates = []
    for key in sorted(seen):
        vals = seen[key]
        if len(vals) > 1:
            duplicates.append({"key": shown[key], "values": vals,
                               "conflict": len(set(vals)) > 1})
    return {"map": mapping, "shown": shown, "duplicates": duplicates,
            "blank_keys": blank, "rows": len(rows), "key_col": key_col,
            "value_col": value_col, "ignore_case": ignore_case}


def plan_updates(elements, lookup, skip_blank=True, validate=None):
    """What the run would write.

    elements: dicts with id, label, key (the match parameter as text)
              and old (the parameter to set, as it reads now).
    validate: optional callable(text) -> error text or '' / None, for
              the parameter's type (whole number, yes/no).

    Rows get a kind: set (a new value), same (already reads that),
    blank (CSV cell empty and skip_blank), invalid (validate refused),
    nokey (the element has nothing to match on), nomatch (no CSV row).
    unused lists the CSV keys no element claimed."""
    mapping = lookup["map"]
    ignore_case = lookup.get("ignore_case", True)
    rows = []
    claimed = set()
    for e in elements:
        key = norm_key(e.get("key"), ignore_case)
        row = {"id": e.get("id"), "label": e.get("label") or u"",
               "key": u"{0}".format(e.get("key") or u"").strip(),
               "old": u"{0}".format(e.get("old") if e.get("old") is not None else u""),
               "new": u"", "kind": "", "why": u""}
        if not key:
            row["kind"] = "nokey"
        elif key not in mapping:
            row["kind"] = "nomatch"
        else:
            claimed.add(key)
            new = u"{0}".format(mapping[key] if mapping[key] is not None else u"").strip()
            row["new"] = new
            if not new and skip_blank:
                row["kind"] = "blank"
            elif same_value(row["old"], new):
                row["kind"] = "same"
            else:
                why = validate(new) if validate else None
                if why:
                    row["kind"] = "invalid"
                    row["why"] = u"{0}".format(why)
                else:
                    row["kind"] = "set"
        rows.append(row)
    unused = [lookup["shown"][k] for k in sorted(mapping) if k not in claimed]
    counts = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    return {"rows": rows, "unused": unused, "counts": counts,
            "to_set": counts.get("set", 0), "elements": len(rows)}


def summary_text(plan, noun=u"element(s)"):
    c = plan["counts"]
    bits = [u"{0} of {1} {2} matched".format(
        plan["elements"] - c.get("nomatch", 0) - c.get("nokey", 0),
        plan["elements"], noun)]
    bits.append(u"{0} to change".format(c.get("set", 0)))
    if c.get("same"):
        bits.append(u"{0} already equal".format(c["same"]))
    if c.get("blank"):
        bits.append(u"{0} blank in the CSV (left alone)".format(c["blank"]))
    if c.get("invalid"):
        bits.append(u"{0} with a value the parameter cannot take".format(c["invalid"]))
    if c.get("nomatch"):
        bits.append(u"{0} without a CSV row".format(c["nomatch"]))
    if c.get("nokey"):
        bits.append(u"{0} with nothing to match on".format(c["nokey"]))
    if plan["unused"]:
        bits.append(u"{0} CSV row(s) without an element".format(len(plan["unused"])))
    return u"; ".join(bits) + u"."


# ---------------------------------------------------------------------------
# 4. The preview page
# ---------------------------------------------------------------------------


def _esc(s):
    return (u"{0}".format(s if s is not None else u"")
            .replace(u"&", u"&amp;").replace(u"<", u"&lt;")
            .replace(u">", u"&gt;").replace(u'"', u"&quot;"))


_CSS = u"""
body{font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#222;margin:0;background:#fff}
header{background:#f4f4f4;padding:14px 20px;border-bottom:1px solid #ddd}
h1{font-size:18px;margin:0 0 4px 0}
h2{font-size:14px;margin:22px 0 6px 0}
.meta{color:#666;font-size:12px}
.sum{display:flex;flex-wrap:wrap;gap:10px;padding:12px 20px}
.sum div{border:1px solid #ddd;border-radius:4px;padding:6px 10px;min-width:110px}
.sum b{display:block;font-size:18px}
.legend{padding:0 20px 8px 20px;font-size:12px;color:#444}
.legend span{display:inline-block;padding:2px 8px;margin-right:8px;border-radius:3px}
main{padding:0 20px 30px 20px}
table{border-collapse:collapse;width:100%;margin-top:6px}
th,td{border:1px solid #e3e3e3;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#f7f7f7;position:sticky;top:0}
tr.set td{background:#fff3b0}
tr.same td{color:#555}
tr.blank td{color:#777;font-style:italic;background:#fafafa}
tr.invalid td{background:#fde2e4;color:#b00020;font-weight:600}
tr.nomatch td{color:#999;font-style:italic}
tr.nokey td{color:#b00020;font-style:italic}
tr.unused td{color:#555}
tr.dupsame td{color:#555}
td.num{text-align:right}
p.note{color:#666;font-size:12px}
"""

_KINDS = (("set", u"will change"), ("same", u"already equal"),
          ("blank", u"blank in the CSV - left alone"),
          ("invalid", u"value the parameter cannot take"),
          ("nomatch", u"no CSV row"), ("nokey", u"nothing to match on"))


def preview_html(title, meta, plan, lookup, show_unchanged=True,
                 match_label=u"key", set_label=u"value"):
    """A self-contained page: summary tiles, every element with its key,
    current and new value, then the CSV rows no element claimed and the
    duplicate keys in the file."""
    c = plan["counts"]
    out = [u"<!DOCTYPE html><html><head><meta charset='utf-8'><title>",
           _esc(title), u"</title><style>", _CSS, u"</style></head><body>",
           u"<header><h1>", _esc(title), u"</h1><div class='meta'>",
           u" &nbsp;|&nbsp; ".join(u"{0}: {1}".format(_esc(k), _esc(v))
                                   for k, v in (meta or [])),
           u"</div></header>"]
    tiles = [(c.get("set", 0), u"will change"), (c.get("same", 0), u"already equal"),
             (c.get("blank", 0), u"blank in CSV"), (c.get("invalid", 0), u"cannot take"),
             (c.get("nomatch", 0), u"no CSV row"), (c.get("nokey", 0), u"no key"),
             (len(plan["unused"]), u"CSV rows unused"),
             (len(lookup.get("duplicates", [])), u"duplicate keys in CSV")]
    out.append(u"<div class='sum'>" + u"".join(
        u"<div><b>{0}</b>{1}</div>".format(n, _esc(lbl)) for n, lbl in tiles)
        + u"</div>")
    out.append(u"<div class='legend'>"
               u"<span style='background:#fff3b0'>will change</span>"
               u"<span style='background:#fde2e4;color:#b00020'>cannot take the value</span>"
               u"<span style='color:#999;font-style:italic'>no CSV row</span>"
               u"<span style='color:#b00020;font-style:italic'>nothing to match on</span>"
               u"</div><main>")
    out.append(u"<h2>Elements</h2><table><tr><th>Element</th><th>{0}</th>"
               u"<th>{1} now</th><th>{1} from CSV</th><th>Change</th>"
               u"<th>Element id</th></tr>".format(_esc(match_label), _esc(set_label)))
    labels = dict(_KINDS)
    shown = 0
    for r in plan["rows"]:
        if r["kind"] == "same" and not show_unchanged:
            continue
        shown += 1
        why = labels.get(r["kind"], r["kind"])
        if r["kind"] == "invalid" and r["why"]:
            why = u"{0} - {1}".format(why, r["why"])
        out.append(u"<tr class='{0}'><td>{1}</td><td>{2}</td><td>{3}</td><td>{4}</td>"
                   u"<td>{5}</td><td class='num'>{6}</td></tr>".format(
                       r["kind"], _esc(r["label"]), _esc(r["key"]), _esc(r["old"]),
                       _esc(r["new"]) if r["kind"] not in ("nomatch", "nokey") else u"",
                       _esc(why), _esc(r["id"] if r["id"] is not None else u"")))
    out.append(u"</table>")
    if not show_unchanged and c.get("same"):
        out.append(u"<p class='note'>{0} element(s) already equal are hidden.</p>".format(
            c["same"]))
    if shown == 0:
        out.append(u"<p class='note'>No elements.</p>")
    if plan["unused"]:
        out.append(u"<h2>CSV rows without an element ({0})</h2><p class='note'>"
                   u"No element's {1} reads any of these.</p><table><tr><th>{1}</th>"
                   u"<th>{2}</th></tr>".format(len(plan["unused"]), _esc(match_label),
                                                _esc(set_label)))
        mp = lookup["map"]
        for k in plan["unused"]:
            out.append(u"<tr class='unused'><td>{0}</td><td>{1}</td></tr>".format(
                _esc(k), _esc(mp.get(norm_key(k, lookup.get("ignore_case", True)), u""))))
        out.append(u"</table>")
    dups = lookup.get("duplicates") or []
    if dups:
        out.append(u"<h2>Keys on more than one CSV row ({0})</h2><p class='note'>"
                   u"The last row wins. Rows whose values differ are marked.</p>"
                   u"<table><tr><th>{1}</th><th>Values</th><th></th></tr>".format(
                       len(dups), _esc(match_label)))
        for d in dups:
            out.append(u"<tr class='{0}'><td>{1}</td><td>{2}</td><td>{3}</td></tr>".format(
                "invalid" if d["conflict"] else "dupsame", _esc(d["key"]),
                _esc(u", ".join(u"'{0}'".format(v) for v in d["values"])),
                u"DIFFERENT VALUES" if d["conflict"] else u"same value"))
        out.append(u"</table>")
    if lookup.get("blank_keys"):
        out.append(u"<p class='note'>{0} CSV row(s) have no {1} and were skipped.</p>".format(
            lookup["blank_keys"], _esc(match_label)))
    out.append(u"</main></body></html>")
    return u"".join(out)


__all__ = ["read_table", "table_from_text", "parse_text", "decode_bytes",
           "sniff_delimiter", "unique_headers", "column_index", "guess_column",
           "norm_key", "to_number", "to_integer", "to_yesno", "same_value",
           "build_lookup", "plan_updates", "summary_text", "preview_html"]
