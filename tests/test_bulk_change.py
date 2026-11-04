#!/usr/bin/env python3
"""Unit tests for pymep_bulk_change - the filter, the parameter list,
the one-column check and the change plan.

Run:  python3 tests/test_bulk_change.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "lib"))

import pymep_bulk_change as BC

ROWS = [
    {"id": 1, "category": u"Structural Foundations", "family": u"Pile Cap", "type": u"600 dia", "type_id": 100},
    {"id": 2, "category": u"Structural Foundations", "family": u"Pile Cap", "type": u"600 dia", "type_id": 100},
    {"id": 3, "category": u"Structural Foundations", "family": u"Pile Cap", "type": u"900 dia", "type_id": 101},
    {"id": 4, "category": u"Structural Foundations", "family": u"Pad Footing", "type": u"1200", "type_id": 102},
    {"id": 5, "category": u"Rooms", "family": BC.NO_TYPE, "type": BC.NO_TYPE, "type_id": None},
]


class Filter(unittest.TestCase):

    def test_categories(self):
        self.assertEqual(BC.categories(ROWS), [(u"Structural Foundations", 4), (u"Rooms", 1)])

    def test_options(self):
        self.assertEqual(BC.family_options(ROWS, u"Structural Foundations"),
                         [BC.ALL_FAMILIES, u"Pad Footing", u"Pile Cap"])
        self.assertEqual(BC.type_options(ROWS, u"Structural Foundations"),
                         [BC.ALL_TYPES, u"1200", u"600 dia", u"900 dia"])
        self.assertEqual(BC.type_options(ROWS, u"Structural Foundations", u"Pile Cap"),
                         [BC.ALL_TYPES, u"600 dia", u"900 dia"])
        self.assertEqual(BC.type_options(ROWS, u"Structural Foundations", BC.ALL_FAMILIES)[0],
                         BC.ALL_TYPES)

    def test_filter(self):
        ids = lambda rs: [r["id"] for r in rs]
        self.assertEqual(ids(BC.filter_rows(ROWS, u"Structural Foundations")), [1, 2, 3, 4])
        self.assertEqual(ids(BC.filter_rows(ROWS, u"Structural Foundations", u"Pile Cap")), [1, 2, 3])
        self.assertEqual(ids(BC.filter_rows(ROWS, u"Structural Foundations", u"Pile Cap",
                                            u"900 dia")), [3])
        self.assertEqual(ids(BC.filter_rows(ROWS, u"Structural Foundations", BC.ALL_FAMILIES,
                                            BC.ALL_TYPES, ids={2, 4, 5})), [2, 4])
        self.assertEqual(ids(BC.filter_rows(ROWS, u"Rooms")), [5])

    def test_sample_takes_one_of_each_type_first(self):
        s = BC.sample_rows(ROWS[:4], cap=3)
        self.assertEqual([r["id"] for r in s], [1, 3, 4])


INST = {u"Mark": {"storage": "String", "readonly": False, "yesno": False},
        u"Peak Load": {"storage": "Double", "readonly": False, "yesno": False},
        u"Count": {"storage": "Integer", "readonly": False, "yesno": False},
        u"Founded": {"storage": "Integer", "readonly": False, "yesno": True},
        u"Volume": {"storage": "Double", "readonly": True, "yesno": False},
        u"Level": {"storage": "ElementId", "readonly": False, "yesno": False}}
TYPE = {u"Type Mark": {"storage": "String", "readonly": False, "yesno": False}}


class Params(unittest.TestCase):

    def test_build_and_keys(self):
        ps = BC.build_params(INST, TYPE)
        self.assertEqual([p["label"] for p in ps],
                         [u"Count", u"Founded", u"Level", u"Mark", u"Peak Load", u"Volume",
                          u"Type Mark [type]"])
        by = dict((p["name"], p) for p in ps)
        self.assertTrue(by[u"Mark"]["editable"])
        self.assertFalse(by[u"Volume"]["editable"])
        self.assertFalse(by[u"Level"]["editable"])
        self.assertEqual(by[u"Type Mark"]["key"], u"t:Type Mark")
        self.assertEqual(BC.split_key(u"t:Type Mark"), (u"t", u"Type Mark"))
        self.assertEqual(BC.split_key(u"i:Note: a"), (u"i", u"Note: a"))
        self.assertEqual(BC.default_keys(ps), [u"i:Mark"])

    def test_search(self):
        ps = BC.build_params(INST, TYPE)
        self.assertEqual([p["name"] for p in BC.search_params(ps, u"mark")],
                         [u"Mark", u"Type Mark"])
        self.assertEqual([p["name"] for p in BC.search_params(ps, u"type mark")], [u"Type Mark"])
        self.assertEqual(len(BC.search_params(ps, u"")), 7)


class Selection(unittest.TestCase):

    def test_one_column(self):
        self.assertEqual(BC.selection_column([(1, u"i:Mark"), (2, u"i:Mark")]), (u"i:Mark", u""))
        key, why = BC.selection_column([(1, u"i:Mark"), (2, u"i:Peak Load")])
        self.assertIsNone(key)
        self.assertIn(u"spans 2 columns", why)
        key, why = BC.selection_column([(1, None), (1, u"i:Mark")])
        self.assertIsNone(key)
        self.assertIn(u"cannot be changed", why)
        self.assertIsNone(BC.selection_column([])[0])

    def test_check_value(self):
        by = dict((p["name"], p) for p in BC.build_params(INST, TYPE))
        self.assertEqual(BC.check_value(by[u"Mark"], u""), u"")
        self.assertEqual(BC.check_value(by[u"Peak Load"], u"150 kN"), u"")
        self.assertEqual(BC.check_value(by[u"Peak Load"], u"abc"), u"type a number")
        self.assertEqual(BC.check_value(by[u"Count"], u"2.5"), u"not a whole number")
        self.assertEqual(BC.check_value(by[u"Founded"], u"yes"), u"")
        self.assertEqual(BC.check_value(by[u"Founded"], u"perhaps"), u"not yes/no")
        self.assertIn(u"read-only", BC.check_value(by[u"Volume"], u"1"))
        self.assertIn(u"another element", BC.check_value(by[u"Level"], u"L1"))

    def test_unchanged(self):
        self.assertTrue(BC.unchanged(u"P1", u"P1", "String"))
        self.assertFalse(BC.unchanged(u"p1", u"P1", "String"))      # case can be fixed
        self.assertTrue(BC.unchanged(u"1,250.00 kN", u"1250", "Double"))
        self.assertFalse(BC.unchanged(None, u"1", "Double"))


class Plan(unittest.TestCase):

    def setUp(self):
        self.ps = dict((p["name"], p) for p in BC.build_params(INST, TYPE))
        self.rows = dict((r["id"], r) for r in ROWS)
        self.values = {(1, u"i:Mark"): u"P1", (2, u"i:Mark"): u"P2", (3, u"i:Mark"): u"NEW",
                       (4, u"i:Mark"): None,
                       (1, u"t:Type Mark"): u"A", (2, u"t:Type Mark"): u"A",
                       (3, u"t:Type Mark"): u"B", (4, u"t:Type Mark"): u"C",
                       (1, u"i:Peak Load"): u"100 kN", (2, u"i:Peak Load"): u"200 kN"}

    def test_instance_column(self):
        cells = [(1, u"i:Mark"), (2, u"i:Mark"), (3, u"i:Mark"), (4, u"i:Mark")]
        plan = BC.plan_change(cells, self.rows, self.ps[u"Mark"], u"NEW", self.values)
        self.assertEqual([t["owner"] for t in plan["targets"]], [1, 2])
        self.assertEqual(plan["counts"], {"set": 2, "same": 1, "missing": 1, "invalid": 0})
        text = BC.plan_text(plan, self.ps[u"Mark"])
        self.assertIn(u"4 cell(s) selected in 'Mark'", text)
        self.assertIn(u"2 to change", text)
        self.assertIn(u"1 already that value", text)
        self.assertIn(u"1 without the parameter", text)

    def test_text_keeps_spaces_numbers_are_trimmed(self):
        plan = BC.plan_change([(1, u"i:Mark")], self.rows, self.ps[u"Mark"], u" A ", self.values)
        self.assertEqual(plan["targets"][0]["new"], u" A ")
        plan = BC.plan_change([(1, u"i:Peak Load")], self.rows, self.ps[u"Peak Load"], u" 150 ",
                              self.values)
        self.assertEqual(plan["targets"][0]["new"], u"150")

    def test_type_column_writes_once_per_type(self):
        cells = [(1, u"t:Type Mark"), (2, u"t:Type Mark"), (3, u"t:Type Mark")]
        plan = BC.plan_change(cells, self.rows, self.ps[u"Type Mark"], u"Z", self.values)
        self.assertEqual([(t["owner"], t["rows"]) for t in plan["targets"]],
                         [(100, [1, 2]), (101, [3])])
        self.assertEqual(plan["types_hit"], 2)
        self.assertIn(u"2 type(s) to change - every instance of them changes",
                      BC.plan_text(plan, self.ps[u"Type Mark"], rows_in_table=3))

    def test_invalid_value(self):
        cells = [(1, u"i:Peak Load"), (2, u"i:Peak Load")]
        plan = BC.plan_change(cells, self.rows, self.ps[u"Peak Load"], u"lots", self.values)
        self.assertEqual(plan["targets"], [])
        self.assertEqual(plan["counts"]["invalid"], 2)
        self.assertIn(u"2 refused - type a number", BC.plan_text(plan, self.ps[u"Peak Load"]))


class Sorting(unittest.TestCase):

    def test_numbers_by_value_text_naturally_blanks_last(self):
        cells = [u"100 kN", u"", u"P10", u"20 kN", u"P2", None, u"-5", u"1,250.00 kN", u"abc"]
        ordered = sorted(cells, key=BC.sort_key)
        self.assertEqual(ordered[:4], [u"-5", u"20 kN", u"100 kN", u"1,250.00 kN"])
        self.assertEqual(ordered[4:7], [u"abc", u"P2", u"P10"])
        self.assertEqual(set(ordered[7:]), set([u"", None]))


class Filters(unittest.TestCase):

    def test_text_conditions(self):
        f = BC.make_filter(u"i:Mark", "contains", u"16; 17")
        self.assertEqual(f["values"], [u"16", u"17"])
        self.assertTrue(BC.matches(u"P1605", f))
        self.assertTrue(BC.matches(u"p1702", f))
        self.assertFalse(BC.matches(u"P1805", f))
        self.assertFalse(BC.matches(None, f))
        nf = BC.make_filter(u"i:Mark", "notcontains", u"16")
        self.assertTrue(BC.matches(u"P1805", nf))
        self.assertTrue(BC.matches(None, nf))
        sf = BC.make_filter(u"i:Mark", "starts", u"p")
        self.assertTrue(BC.matches(u"P1605", sf))
        self.assertFalse(BC.matches(u"XP1", sf))

    def test_equals_reads_numbers_too(self):
        f = BC.make_filter(u"i:Peak Load", "equals", u"1250")
        self.assertTrue(BC.matches(u"1,250.00 kN", f))
        self.assertFalse(BC.matches(u"1251 kN", f))
        e = BC.make_filter(u"i:Mark", "equals", values=[u"600 dia", u"900 dia"])
        self.assertTrue(BC.matches(u"900 DIA", e))
        self.assertFalse(BC.matches(u"900 dia x", e))
        ne = BC.make_filter(u"i:Mark", "notequals", u"P1")
        self.assertFalse(BC.matches(u"p1", ne))
        self.assertTrue(BC.matches(u"P2", ne))

    def test_numbers_and_blanks(self):
        gt = BC.make_filter(u"i:Peak Load", "gt", u"500")
        self.assertTrue(BC.matches(u"1,250.00 kN", gt))
        self.assertFalse(BC.matches(u"500 kN", gt))
        self.assertFalse(BC.matches(u"n/a", gt))
        self.assertTrue(BC.matches(u"500 kN", BC.make_filter(u"k", "ge", u"500")))
        self.assertTrue(BC.matches(u"-1", BC.make_filter(u"k", "lt", u"0")))
        self.assertTrue(BC.matches(u"0", BC.make_filter(u"k", "le", u"0")))
        self.assertTrue(BC.matches(u"  ", BC.make_filter(u"k", "blank")))
        self.assertTrue(BC.matches(None, BC.make_filter(u"k", "blank")))
        self.assertTrue(BC.matches(u"x", BC.make_filter(u"k", "notblank")))

    def test_refusals(self):
        with self.assertRaises(ValueError):
            BC.make_filter(u"k", "gt", u"lots")
        with self.assertRaises(ValueError):
            BC.make_filter(u"k", "contains", u" ; ")
        with self.assertRaises(ValueError):
            BC.make_filter(u"k", "bogus", u"1")

    def test_labels(self):
        self.assertEqual(BC.filter_label(BC.make_filter(u"k", "contains", u"16;17"), u"Mark"),
                         u"Mark contains '16' or '17'")
        self.assertEqual(BC.filter_label(BC.make_filter(u"k", "ge", u"500"), u"Peak Load"),
                         u"Peak Load >= 500")
        self.assertEqual(BC.filter_label(BC.make_filter(u"k", "blank"), u"Comments"),
                         u"Comments is blank")
        many = BC.make_filter(u"k", "equals", values=[u"a", u"b", u"c", u"d", u"e"])
        self.assertEqual(BC.filter_label(many, u"Type"), u"Type equals 'a' or 'b' or 'c' (+2 more)")

    def test_apply_all_filters(self):
        cells = {(1, u"m"): u"P1605", (2, u"m"): u"P1606", (3, u"m"): u"P1805",
                 (1, u"l"): u"100", (2, u"l"): u"900", (3, u"l"): u"950"}
        get = lambda r, k: cells.get((r, k))
        filters = [BC.make_filter(u"m", "contains", u"16"), BC.make_filter(u"l", "gt", u"500")]
        self.assertEqual(BC.apply_filters([1, 2, 3], get, filters), [2])
        self.assertEqual(BC.apply_filters([1, 2, 3], get, []), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
