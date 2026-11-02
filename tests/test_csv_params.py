#!/usr/bin/env python3
"""Unit tests for pymep_csv_params - reading a CSV, keys and values,
the lookup, the plan and the preview page.

Run:  python3 tests/test_csv_params.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "lib"))

import pymep_csv_params as CP


def elems(*pairs):
    """[(key, old), ...] -> element dicts with ids 1, 2, 3 ..."""
    return [{"id": i + 1, "label": "Pile {0}".format(i + 1), "key": k, "old": o}
            for i, (k, o) in enumerate(pairs)]


class Reading(unittest.TestCase):

    def test_decoding(self):
        self.assertEqual(CP.decode_bytes(b"\xef\xbb\xbfMark,Load\n"), u"Mark,Load\n")
        self.assertEqual(CP.decode_bytes(u"Mark\n".encode("utf-16")), u"Mark\n")
        self.assertEqual(CP.decode_bytes(u"café".encode("utf-8")), u"café")
        self.assertEqual(CP.decode_bytes(b"12\xb0"), u"12°")     # cp1252 degree

    def test_sniffing(self):
        self.assertEqual(CP.sniff_delimiter(u"a,b,c\n1,2,3"), u",")
        self.assertEqual(CP.sniff_delimiter(u"a;b;c\n1;2;3"), u";")
        self.assertEqual(CP.sniff_delimiter(u"a\tb\n1\t2"), u"\t")
        self.assertEqual(CP.sniff_delimiter(u"\n\nMark\n"), u",")
        self.assertEqual(CP.sniff_delimiter(u""), u",")

    def test_parsing(self):
        rows = CP.parse_text(u'Mark,Note\r\nP1,"a, b"\r\nP2,"say ""hi"""\r\n'
                             u'P3,"two\nlines"\r\n\r\n,\r\nP4,x')
        self.assertEqual(rows, [[u"Mark", u"Note"], [u"P1", u"a, b"],
                                [u"P2", u'say "hi"'], [u"P3", u"two\nlines"],
                                [u"P4", u"x"]])
        self.assertEqual(CP.parse_text(u"a;b\n1;2\n"), [[u"a", u"b"], [u"1", u"2"]])
        self.assertEqual(CP.parse_text(u"﻿a,b"), [[u"a", u"b"]])
        self.assertEqual(CP.parse_text(u""), [])

    def test_headers(self):
        self.assertEqual(CP.unique_headers([u" Mark ", u"", u"mark", u"Load"]),
                         [u"Mark", u"Column 2", u"mark (2)", u"Load"])

    def test_table(self):
        h, rows = CP.table_from_text(u"Mark,Peak Load\nP1,100\nP2\nP3,300,extra\n")
        self.assertEqual(h, [u"Mark", u"Peak Load"])
        self.assertEqual(rows, [[u"P1", u"100"], [u"P2", u""], [u"P3", u"300"]])
        with self.assertRaises(ValueError):
            CP.table_from_text(u"\n\n")

    def test_read_file(self):
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, "loads.csv")
        with open(p, "wb") as f:
            f.write(b"\xef\xbb\xbfMark;Peak Load (kN)\r\nP1604;1250\r\nP1607;980,5\r\n")
        h, rows = CP.read_table(p)
        self.assertEqual(h, [u"Mark", u"Peak Load (kN)"])
        self.assertEqual(rows, [[u"P1604", u"1250"], [u"P1607", u"980,5"]])

    def test_columns(self):
        h = [u"Mark", u"Peak Load (kN)", u"Note"]
        self.assertEqual(CP.column_index(h, u"mark"), 0)
        self.assertEqual(CP.column_index(h, u"Nope"), -1)
        self.assertEqual(CP.column_index(h, None), -1)
        self.assertEqual(CP.guess_column(h, u"Mark"), u"Mark")
        self.assertEqual(CP.guess_column(h, u"Peak Load"), u"Peak Load (kN)")
        self.assertEqual(CP.guess_column(h, u"Load_Peak"), u"Peak Load (kN)")
        self.assertEqual(CP.guess_column(h, u"Comments", avoid=[u"Mark"]), u"Peak Load (kN)")
        self.assertEqual(CP.guess_column(h, u""), u"Mark")
        self.assertIsNone(CP.guess_column([], u"Mark"))


class Values(unittest.TestCase):

    def test_keys(self):
        self.assertEqual(CP.norm_key(u"  P 1604 "), u"p 1604")
        self.assertEqual(CP.norm_key(u"P1604", ignore_case=False), u"P1604")
        self.assertEqual(CP.norm_key(u"1604.0"), u"1604")
        self.assertEqual(CP.norm_key(u"1604.5"), u"1604.5")
        self.assertEqual(CP.norm_key(None), u"")
        self.assertEqual(CP.norm_key(1604), u"1604")

    def test_numbers(self):
        self.assertEqual(CP.to_number(u"1250"), 1250.0)
        self.assertEqual(CP.to_number(u"1,250.5"), 1250.5)
        self.assertEqual(CP.to_number(u"150 kN"), 150.0)
        self.assertEqual(CP.to_number(u"1,250.00 kN"), 1250.0)
        self.assertEqual(CP.to_number(u"12,5"), 12.5)
        self.assertEqual(CP.to_number(u"-3.2e2"), -320.0)
        self.assertEqual(CP.to_number(u".5"), 0.5)
        self.assertIsNone(CP.to_number(u""))
        self.assertIsNone(CP.to_number(u"N/A"))
        self.assertIsNone(CP.to_number(None))

    def test_integers_and_yesno(self):
        self.assertEqual(CP.to_integer(u"12"), (True, 12, u""))
        self.assertEqual(CP.to_integer(u"12.0"), (True, 12, u""))
        self.assertEqual(CP.to_integer(u"12.5")[0], False)
        self.assertEqual(CP.to_integer(u"x")[2], u"not a number")
        self.assertEqual(CP.to_yesno(u"Yes"), (True, 1, u""))
        self.assertEqual(CP.to_yesno(u"0"), (True, 0, u""))
        self.assertEqual(CP.to_yesno(u"maybe")[0], False)
        self.assertEqual(CP.to_yesno(u"")[0], False)

    def test_same_value(self):
        self.assertTrue(CP.same_value(u"1,250.00 kN", u"1250"))
        self.assertTrue(CP.same_value(u"P1", u" p1 "))
        self.assertTrue(CP.same_value(u"", u""))
        self.assertFalse(CP.same_value(u"1250", u"1251"))
        self.assertFalse(CP.same_value(u"abc", u"abd"))
        self.assertFalse(CP.same_value(u"", u"1"))


HEAD = [u"Mark", u"Peak Load", u"Note"]
ROWS = [[u"P1604", u"1250", u""], [u"P1607", u"980", u""], [u"P1608", u"", u""],
        [u"p1609", u"700", u""], [u"P1609", u"700", u"again"],
        [u"P1610", u"500", u""], [u"P1610", u"550", u"changed"],
        [u"", u"1", u"no key"], [u"P9999", u"1", u"no pile"]]


class Lookup(unittest.TestCase):

    def test_lookup(self):
        lk = CP.build_lookup(HEAD, ROWS, u"Mark", u"Peak Load")
        self.assertEqual(lk["map"][u"p1604"], u"1250")
        self.assertEqual(lk["map"][u"p1610"], u"550")           # last row wins
        self.assertEqual(lk["blank_keys"], 1)
        self.assertEqual(lk["rows"], 9)
        dups = dict((d["key"], d) for d in lk["duplicates"])
        self.assertEqual(sorted(dups), [u"P1610", u"p1609"])
        self.assertFalse(dups[u"p1609"]["conflict"])
        self.assertTrue(dups[u"P1610"]["conflict"])

    def test_case_sensitive(self):
        lk = CP.build_lookup(HEAD, ROWS, u"Mark", u"Peak Load", ignore_case=False)
        self.assertIn(u"p1609", lk["map"])
        self.assertIn(u"P1609", lk["map"])
        self.assertEqual(lk["duplicates"][0]["key"], u"P1610")

    def test_missing_column(self):
        with self.assertRaises(ValueError):
            CP.build_lookup(HEAD, ROWS, u"Mark", u"Load")
        with self.assertRaises(ValueError):
            CP.build_lookup(HEAD, ROWS, u"Nope", u"Peak Load")


class Plan(unittest.TestCase):

    def setUp(self):
        self.lk = CP.build_lookup(HEAD, ROWS, u"Mark", u"Peak Load")

    def test_the_example_from_the_brief(self):
        """Foundations with a Mark and a Peak Load; the CSV has both."""
        els = elems((u"P1604", u"1,250.00 kN"), (u"P1607", u"900 kN"),
                    (u"P1608", u"300 kN"), (u"P1609", u""), (u"P1610", u"500 kN"),
                    (u"P1611", u"100 kN"), (u"", u"7 kN"))
        plan = CP.plan_updates(els, self.lk)
        kinds = [r["kind"] for r in plan["rows"]]
        self.assertEqual(kinds, ["same", "set", "blank", "set", "set", "nomatch", "nokey"])
        self.assertEqual(plan["rows"][1]["new"], u"980")
        self.assertEqual(plan["rows"][4]["new"], u"550")        # last CSV row for P1610
        self.assertEqual(plan["to_set"], 3)
        self.assertEqual(plan["unused"], [u"P9999"])
        self.assertEqual(plan["counts"]["nomatch"], 1)
        text = CP.summary_text(plan, u"foundation(s)")
        self.assertIn(u"5 of 7 foundation(s) matched", text)
        self.assertIn(u"3 to change", text)
        self.assertIn(u"1 already equal", text)
        self.assertIn(u"1 blank in the CSV", text)
        self.assertIn(u"1 without a CSV row", text)
        self.assertIn(u"1 with nothing to match on", text)
        self.assertIn(u"1 CSV row(s) without an element", text)

    def test_blank_cells_clear_when_asked(self):
        els = elems((u"P1608", u"300 kN"))
        plan = CP.plan_updates(els, self.lk, skip_blank=False)
        self.assertEqual(plan["rows"][0]["kind"], "set")
        self.assertEqual(plan["rows"][0]["new"], u"")
        plan2 = CP.plan_updates(elems((u"P1608", u"")), self.lk, skip_blank=False)
        self.assertEqual(plan2["rows"][0]["kind"], "same")

    def test_validation(self):
        lk = CP.build_lookup([u"Mark", u"Count"], [[u"A", u"3"], [u"B", u"2.5"]],
                             u"Mark", u"Count")
        validate = lambda t: CP.to_integer(t)[2]
        plan = CP.plan_updates(elems((u"A", u"1"), (u"B", u"1")), lk, validate=validate)
        self.assertEqual([r["kind"] for r in plan["rows"]], ["set", "invalid"])
        self.assertEqual(plan["rows"][1]["why"], u"not a whole number")

    def test_keys_match_like_excel_writes_them(self):
        lk = CP.build_lookup([u"Mark", u"V"], [[u"1604.0", u"x"]], u"Mark", u"V")
        plan = CP.plan_updates(elems((u"1604", u"")), lk)
        self.assertEqual(plan["rows"][0]["kind"], "set")

    def test_same_key_on_two_elements(self):
        plan = CP.plan_updates(elems((u"P1604", u""), (u"P1604", u"")), self.lk)
        self.assertEqual([r["new"] for r in plan["rows"]], [u"1250", u"1250"])
        self.assertNotIn(u"P1604", plan["unused"])


class Preview(unittest.TestCase):

    def test_page(self):
        lk = CP.build_lookup(HEAD, ROWS, u"Mark", u"Peak Load")
        els = elems((u"P1604", u"1250"), (u"P1607", u"900"), (u"P1608", u"300"),
                    (u"P1611", u"100"), (u"", u"7"))
        plan = CP.plan_updates(els, lk)
        html = CP.preview_html(u"CSV to Parameter <t>", [(u"Model", u"A.rvt")], plan, lk,
                               match_label=u"Mark", set_label=u"Peak Load")
        self.assertTrue(html.startswith(u"<!DOCTYPE html>"))
        self.assertIn(u"&lt;t&gt;", html)
        self.assertIn(u"Model: A.rvt", html)
        self.assertIn(u"<b>1</b>will change", html)
        self.assertIn(u"<tr class='set'>", html)
        self.assertIn(u"<tr class='same'>", html)
        self.assertIn(u"<tr class='blank'>", html)
        self.assertIn(u"<tr class='nomatch'>", html)
        self.assertIn(u"<tr class='nokey'>", html)
        self.assertIn(u"CSV rows without an element (3)", html)   # p1609, P1610, P9999
        self.assertIn(u"<tr class='unused'><td>P9999</td><td>1</td></tr>", html)
        self.assertIn(u"<tr class='unused'><td>P1610</td><td>550</td></tr>", html)
        self.assertIn(u"Keys on more than one CSV row (2)", html)
        self.assertIn(u"DIFFERENT VALUES", html)
        self.assertIn(u"1 CSV row(s) have no Mark", html)
        self.assertIn(u"<th>Peak Load now</th><th>Peak Load from CSV</th>", html)
        self.assertNotIn(u"src=", html)
        hidden = CP.preview_html(u"t", [], plan, lk, show_unchanged=False)
        self.assertNotIn(u"<tr class='same'>", hidden)
        self.assertIn(u"1 element(s) already equal are hidden", hidden)


if __name__ == "__main__":
    unittest.main()
