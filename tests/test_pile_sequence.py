#!/usr/bin/env python3
"""Unit tests for pymep_pile_sequence - mark parsing, the number box,
grouping, the insertion plan and the preview page.

Run:  python3 tests/test_pile_sequence.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "lib"))

import pymep_pile_sequence as PS


def marks(*texts):
    return [(i + 1, t) for i, t in enumerate(texts)]


def entries(*texts):
    groups, _ignored = PS.group_marks(marks(*texts))
    assert len(groups) == 1, groups
    return list(groups.values())[0]


def final(plan):
    """{old mark: new mark} for the piles - freed numbers are not piles
    and are left out (they are in plan["freed"])."""
    out = {}
    for r in plan["rows"]:
        if r["kind"] == "freed":
            continue
        out[r["old"]] = r["new"]
    return out


class Marks(unittest.TestCase):

    def test_parse(self):
        self.assertEqual(PS.parse_mark("P1604"),
                         {"prefix": "P", "number": 1604, "width": 4, "suffix": ""})
        self.assertEqual(PS.parse_mark("P1604-1")["suffix"], "-1")
        self.assertEqual(PS.parse_mark("p0012")["width"], 4)
        self.assertEqual(PS.parse_mark("1604")["prefix"], "")
        self.assertEqual(PS.parse_mark("TP-07")["prefix"], "TP-")
        self.assertEqual(PS.parse_mark(" P 1604 ")["number"], 1604)
        self.assertEqual(PS.parse_mark("P1604 A")["suffix"], "A")
        self.assertIsNone(PS.parse_mark("ABC"))
        self.assertIsNone(PS.parse_mark(""))
        self.assertIsNone(PS.parse_mark(None))

    def test_format_keeps_prefix_and_padding(self):
        self.assertEqual(PS.format_mark("P", 1605, 4), "P1605")
        self.assertEqual(PS.format_mark("p", 13, 4), "p0013")
        self.assertEqual(PS.format_mark("TP-", 8, 2), "TP-08")
        self.assertEqual(PS.format_mark("", 10000, 4), "10000")

    def test_numbers_box(self):
        self.assertEqual(PS.parse_numbers("1605, 1606"), [1605, 1606])
        self.assertEqual(PS.parse_numbers("P1606 p1605 1605"), [1605, 1606])
        self.assertEqual(PS.parse_numbers("1605-1608"), [1605, 1606, 1607, 1608])
        self.assertEqual(PS.parse_numbers("P1608-P1606;1"), [1, 1606, 1607, 1608])
        self.assertEqual(PS.parse_numbers(""), [])
        with self.assertRaises(ValueError):
            PS.parse_numbers("1605, next")
        with self.assertRaises(ValueError):
            PS.parse_numbers("1-99999")

    def test_grouping_and_ignored(self):
        groups, ignored = PS.group_marks(marks("P1604", "p1606", "TP01",
                                               "P1604-1", "Pile", "", None))
        self.assertEqual(sorted(groups), ["P", "TP"])
        self.assertEqual([e["number"] for e in groups["P"]], [1604, 1606])
        self.assertEqual([(g["mark"], g["reason"]) for g in ignored],
                         [("P1604-1", "suffix '-1'"), ("Pile", "no number"),
                          ("", "no number"), ("", "no number")])
        self.assertIn("P  -  2 mark(s), 1604 to 1606, 1 gap(s)",
                      PS.group_label("P", groups["P"]))

    def test_summary(self):
        s = PS.sequence_summary(entries("P1", "P2", "P2", "P5"))
        self.assertEqual((s["count"], s["low"], s["high"]), (4, 1, 5))
        self.assertEqual(s["gaps"], [3, 4])
        self.assertEqual(s["duplicates"], {2: 2})
        self.assertEqual(PS.sequence_summary([])["count"], 0)


class Plan(unittest.TestCase):

    def test_the_example_from_the_brief(self):
        # 1605 and 1606 are missing; 1608 is duplicated
        e = entries("P1604", "P1607", "P1608", "P1608", "P1609", "P1610")
        plan = PS.plan_fill(e, [1605, 1606])
        self.assertEqual(final(plan), {"P1604": "P1604", "P1607": "P1605",
                                       "P1608": "P1606", "P1609": "P1607",
                                       "P1610": "P1608"})
        self.assertEqual(plan["filled"], [1605, 1606])
        self.assertEqual(plan["freed"], [1609, 1610])
        self.assertEqual(plan["renamed"], 5)
        self.assertEqual(plan["duplicates_after"], {1606: 2})
        self.assertEqual(plan["gaps_after"], [])
        both = [r for r in plan["rows"] if r["old"] == "P1608"]
        self.assertEqual([r["new"] for r in both], ["P1606", "P1606"])
        self.assertTrue(all(r["duplicate"] for r in both))
        freed_rows = [r for r in plan["rows"] if r["kind"] == "freed"]
        self.assertEqual([r["new"] for r in freed_rows], ["P1609", "P1610"])

    def test_then_the_duplicates_get_suffixes(self):
        groups, _ig = PS.group_marks([(1, "P1604"), (2, "P1607"), (9, "P1608"),
                                      (3, "P1608"), (4, "P1609")])
        plan = PS.plan_fill(groups["P"], [1605, 1606], suffix_duplicates=True)
        got = dict((r["id"], r["new"]) for r in plan["rows"] if r["id"])
        self.assertEqual(got, {1: "P1604", 2: "P1605", 3: "P1606-1",
                               9: "P1606-2", 4: "P1607"})
        self.assertEqual(plan["suffixed"], 2)
        self.assertEqual(plan["duplicates_after"], {})

    def test_piles_below_a_filled_number_do_not_move(self):
        plan = PS.plan_fill(entries("P1", "P2", "P4", "P5"), [3])
        self.assertEqual(final(plan), {"P1": "P1", "P2": "P2", "P4": "P3",
                                       "P5": "P4"})
        self.assertEqual(plan["freed"], [5])

    def test_each_pile_moves_by_the_count_of_filled_numbers_below_it(self):
        plan = PS.plan_fill(entries("P1", "P3", "P5", "P7"), [2, 4, 6])
        self.assertEqual(final(plan), {"P1": "P1", "P3": "P2", "P5": "P3",
                                       "P7": "P4"})
        self.assertEqual(plan["freed"], [5, 6, 7])

    def test_other_gaps_move_down_but_stay(self):
        plan = PS.plan_fill(entries("P1", "P3", "P4", "P7"), [2])
        self.assertEqual(final(plan), {"P1": "P1", "P3": "P2", "P4": "P3",
                                       "P7": "P6"})
        self.assertEqual(plan["gaps_after"], [4, 5])

    def test_filling_below_the_first_pile_renumbers_from_there(self):
        plan = PS.plan_fill(entries("P1604", "P1605"), [1601, 1602, 1603])
        self.assertEqual(final(plan), {"P1604": "P1601", "P1605": "P1602"})

    def test_padding_and_case_survive(self):
        plan = PS.plan_fill(entries("p0009", "P0011"), [10])
        self.assertEqual(final(plan), {"p0009": "p0009", "P0011": "P0010"})

    def test_refusals(self):
        e = entries("P1604", "P1607")
        with self.assertRaises(ValueError) as cm:
            PS.plan_fill(e, [1607])
        self.assertIn("in use", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            PS.plan_fill(e, [1620])
        self.assertIn("above the last pile", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            PS.plan_fill(e, [2001], bounds=(1000, 1999))
        self.assertIn("outside this set", str(cm.exception))

    def test_apply_order_is_lowest_first(self):
        plan = PS.plan_fill(entries("P1", "P3", "P4", "P5"), [2])
        self.assertEqual([r["old"] for r in PS.apply_order(plan["rows"])],
                         ["P3", "P4", "P5"])

    def test_no_numbers_changes_nothing(self):
        plan = PS.plan_fill(entries("P1", "P2"), [])
        self.assertEqual(plan["renamed"], 0)
        self.assertEqual(plan["freed"], [])
        self.assertEqual(plan["filled"], [])


class Sets(unittest.TestCase):

    def test_bounds(self):
        self.assertEqual(PS.set_bounds(1604, 4), (1000, 1999))
        self.assertEqual(PS.set_bounds(2001, 4), (2000, 2999))
        self.assertEqual(PS.set_bounds(6350, 4), (6000, 6999))
        self.assertEqual(PS.set_bounds(12, 3), (0, 99))
        self.assertEqual(PS.set_bounds(7, 1), (0, 9))
        self.assertEqual(PS.set_bounds(1604, 2), (1000, 1999))

    def test_split_and_labels(self):
        e = entries("P1001", "P1002", "P2001", "P2003", "P6001")
        sets = PS.split_sets(e)
        self.assertEqual([b for b, _ in sets], [(1000, 1999), (2000, 2999),
                                                (6000, 6999)])
        self.assertEqual([len(x) for _, x in sets], [2, 2, 1])
        self.assertEqual(PS.set_label((2000, 2999), sets[1][1]),
                         "2000-2999  (set 2)  -  2 pile(s), 2001 to 2003, 1 gap(s)")
        self.assertEqual(PS.set_label((3000, 3999), []),
                         "3000-3999  (set 3)  -  empty")

    def test_numbers_set(self):
        self.assertEqual(PS.numbers_set([1605, 1606], 4), (1000, 1999))
        self.assertIsNone(PS.numbers_set([1605, 2001], 4))
        self.assertIsNone(PS.numbers_set([], 4))

    def test_only_the_chosen_set_moves(self):
        e = entries("P1001", "P1003", "P2000", "P2002")
        sets = dict(PS.split_sets(e))
        plan = PS.plan_fill(sets[(1000, 1999)], [1002], bounds=(1000, 1999))
        self.assertEqual(final(plan), {"P1001": "P1001", "P1003": "P1002"})
        plan2 = PS.plan_fill(sets[(2000, 2999)], [2001], bounds=(2000, 2999))
        self.assertEqual(final(plan2), {"P2000": "P2000", "P2002": "P2001"})


class Duplicates(unittest.TestCase):

    def test_suffixes_in_id_order_including_the_first(self):
        groups, _ig = PS.group_marks([(50, "P1607"), (12, "P1607"), (7, "P1608"),
                                      (31, "P1607")])
        plan = PS.plan_fill(groups["P"], [], suffix_duplicates=True)
        got = dict((r["id"], r["new"]) for r in plan["rows"])
        self.assertEqual(got, {12: "P1607-1", 31: "P1607-2", 50: "P1607-3",
                               7: "P1608"})
        self.assertEqual(plan["suffixed"], 3)
        self.assertEqual(plan["duplicates_after"], {})

    def test_taken_suffixes_are_skipped(self):
        ignored = [{"id": 1, "mark": "P1607-1", "reason": "suffix '-1'"},
                   {"id": 2, "mark": "p1607-3", "reason": "suffix '-3'"},
                   {"id": 3, "mark": "TP1607-2", "reason": "suffix '-2'"},
                   {"id": 4, "mark": "P1607-A", "reason": "suffix '-A'"}]
        taken = PS.taken_suffixes(ignored, "P")
        self.assertEqual(taken, {1607: set([1, 3])})
        plan = PS.plan_fill(entries("P1607", "P1607", "P1607"), [],
                            suffix_duplicates=True, taken=taken)
        self.assertEqual(sorted(r["new"] for r in plan["rows"]),
                         ["P1607-2", "P1607-4", "P1607-5"])

    def test_off_by_default_keeps_flagging(self):
        plan = PS.plan_fill(entries("P1", "P1"), [])
        self.assertEqual(plan["suffixed"], 0)
        self.assertEqual(plan["duplicates_after"], {1: 2})

    def test_apply_order_includes_suffixed(self):
        plan = PS.plan_fill(entries("P1", "P3", "P3"), [2], suffix_duplicates=True)
        self.assertEqual([r["new"] for r in PS.apply_order(plan["rows"])],
                         ["P2-1", "P2-2"])

    def test_preview_marks_suffixed_rows(self):
        plan = PS.plan_fill(entries("P1", "P1"), [], suffix_duplicates=True)
        html = PS.preview_html("t", [], plan)
        self.assertIn("<tr class='suffixed'>", html)
        self.assertIn("given the suffix -1", html)
        self.assertIn("given the suffix -2", html)
        self.assertNotIn("DUPLICATE x", html)


class Preview(unittest.TestCase):

    def test_page_marks_up_changes_duplicates_gaps_and_ignored(self):
        e = entries("P2", "P3", "P3", "P6")
        plan = PS.plan_fill(e, [1])
        html = PS.preview_html("Piles", [("Model", "A.rvt"), ("Prefix", "P")],
                               plan, ignored=[{"id": 9, "mark": "P7-1",
                                               "reason": "suffix '-1'"}])
        self.assertIn("<tr class='freed'>", html)
        self.assertIn("<tr class='renamed dup'>", html)
        self.assertIn("<tr class='gap'>", html)
        self.assertIn("P7-1", html)
        self.assertIn("suffix '-1'", html)
        self.assertIn("Filled: <b>1</b>", html)
        self.assertIn("freed at the top: <b>6</b>", html)
        self.assertIn("DUPLICATE x2", html)
        self.assertIn("Model: A.rvt", html)
        self.assertNotIn("Plan</button>", html)       # no points, no plan tab
        self.assertNotIn("src=", html)                # nothing loaded from outside
        self.assertNotIn("http", html.replace("http://www.w3.org/2000/svg", ""))
        # escaping
        html2 = PS.preview_html("<b>", [], PS.plan_fill(entries("P1"), []))
        self.assertIn("&lt;b&gt;", html2)

    def test_plan_tab(self):
        groups, _ig = PS.group_marks([(1, "P1"), (2, "P3"), (3, "P3"), (4, "P6")])
        plan = PS.plan_fill(groups["P"], [2])      # P1 stays, the rest move down
        points = {1: (100.0, 200.0), 2: (110.0, 200.0), 3: (110.0, 205.0),
                  4: (120.0, 260.0)}
        html = PS.preview_html("t", [], plan, points=points)
        self.assertIn("Plan</button>", html)
        self.assertIn("<svg id='plan'", html)
        self.assertEqual(html.count("<circle"), 4)
        self.assertIn("class='plan-renamed plan-dup'", html)
        self.assertIn("class='plan-renamed'", html)
        self.assertIn("class='plan-kept'", html)
        self.assertIn("data-old='P1' data-nw='P1' data-change='unchanged'", html)
        self.assertIn("data-old='P6' data-nw='P5' data-change='renamed (-1)'", html)
        self.assertIn("data-old='P3' data-nw='P2' data-change='renamed (-1) - DUPLICATE'", html)
        self.assertIn("<title>P5  (P6) - renamed (-1)</title>", html)
        import re
        cys = dict((m.group(2), float(m.group(1))) for m in
                   re.finditer(r"cy='([\d.]+)' r='[\d.]+' data-old='([^']*)'", html))
        self.assertLess(cys["P6"], cys["P1"])          # north up
        self.assertIn("freed at the top have no pile yet", html)
        self.assertIn("plan-labels", html)
        html2 = PS.preview_html("t", [], plan, points={1: (0.0, 0.0)})
        self.assertEqual(html2.count("<circle"), 1)
        self.assertEqual(PS.plan_markup(plan["rows"], {}), "")

    def test_plan_tab_suffixed(self):
        plan = PS.plan_fill(entries("P1", "P1"), [], suffix_duplicates=True)
        html = PS.preview_html("t", [], plan, points={1: (0, 0), 2: (1, 0)})
        self.assertEqual(html.count("class='plan-suffixed'"), 2)
        self.assertNotIn("plan-dup'", html.split("<svg")[1])
        self.assertIn("given the suffix -2", html)

    def test_hide_unchanged(self):
        plan = PS.plan_fill(entries("P1", "P3", "P9"), [2])
        html = PS.preview_html("t", [], plan, show_unchanged=False)
        self.assertNotIn(">P1<", html)       # unchanged, hidden
        self.assertIn("Unchanged piles are hidden", html)
        self.assertIn(">P2<", html)          # the renamed P3


if __name__ == "__main__":
    unittest.main()
