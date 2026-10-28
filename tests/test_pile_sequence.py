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
    """{old mark: new mark} for the piles, plus 'NEW' entries."""
    out = {}
    for r in plan["rows"]:
        if r["kind"] == "new":
            out.setdefault("NEW", []).append(r["new"])
        else:
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
        # P1604 and P1606 exist (1605 is a gap); add 1605 and 1606
        e = entries("P1604", "P1606", "P1607", "P1608")
        plan = PS.plan_insertions(e, [1605, 1606])
        self.assertEqual(final(plan), {"P1604": "P1604", "P1606": "P1607",
                                       "P1607": "P1608", "P1608": "P1609",
                                       "NEW": ["P1605", "P1606"]})
        self.assertEqual(plan["new_numbers"], [1605, 1606])
        self.assertEqual(plan["renamed"], 3)
        self.assertEqual(plan["gaps_after"], [])
        self.assertEqual(plan["duplicates_after"], {})

    def test_a_gap_is_simply_reserved(self):
        plan = PS.plan_insertions(entries("P1", "P3"), [2])
        self.assertEqual(final(plan), {"P1": "P1", "P3": "P3", "NEW": ["P2"]})
        self.assertEqual(plan["renamed"], 0)

    def test_fill_gaps_stops_at_the_first_free_number(self):
        e = entries("P1", "P2", "P3", "P5", "P6")        # gap at 4
        plan = PS.plan_insertions(e, [2], fill_gaps=True)
        self.assertEqual(final(plan), {"P1": "P1", "P2": "P3", "P3": "P4",
                                       "P5": "P5", "P6": "P6", "NEW": ["P2"]})
        self.assertEqual(plan["gaps_after"], [])

    def test_shift_everything_keeps_the_gap(self):
        e = entries("P1", "P2", "P3", "P5", "P6")
        plan = PS.plan_insertions(e, [2], fill_gaps=False)
        self.assertEqual(final(plan), {"P1": "P1", "P2": "P3", "P3": "P4",
                                       "P5": "P6", "P6": "P7", "NEW": ["P2"]})
        self.assertEqual(plan["gaps_after"], [5])

    def test_duplicates_move_together_and_stay_flagged(self):
        e = entries("P1", "P2", "P2", "P3")
        plan = PS.plan_insertions(e, [2])
        news = [r for r in plan["rows"] if r["number_new"] == 3]
        self.assertEqual(sorted(r["old"] for r in news), ["P2", "P2"])
        self.assertTrue(all(r["duplicate"] for r in news))
        self.assertEqual(plan["duplicates_before"], {2: 2})
        self.assertEqual(plan["duplicates_after"], {3: 2})
        self.assertEqual(final(plan)["P3"], "P4")

    def test_padding_and_case_survive_a_rename(self):
        plan = PS.plan_insertions(entries("p0009", "P0010"), [9])
        self.assertEqual(final(plan), {"p0009": "p0010", "P0010": "P0011",
                                       "NEW": ["P0009"]})

    def test_number_beyond_the_end_and_several_numbers(self):
        e = entries("P1", "P2")
        plan = PS.plan_insertions(e, [1, 5])
        self.assertEqual(final(plan), {"P1": "P2", "P2": "P3",
                                       "NEW": ["P1", "P5"]})
        self.assertEqual(plan["gaps_after"], [4])

    def test_freed_numbers_are_not_pushed_by_a_later_insertion(self):
        # 2 is reserved first; adding 3 then moves P3 and P4 only
        e = entries("P1", "P3", "P4")
        plan = PS.plan_insertions(e, [2, 3])
        self.assertEqual(final(plan), {"P1": "P1", "P3": "P4", "P4": "P5",
                                       "NEW": ["P2", "P3"]})

    def test_apply_order_is_highest_first(self):
        plan = PS.plan_insertions(entries("P1", "P2", "P3"), [1])
        self.assertEqual([r["old"] for r in PS.apply_order(plan["rows"])],
                         ["P3", "P2", "P1"])

    def test_no_numbers_changes_nothing(self):
        plan = PS.plan_insertions(entries("P1", "P2"), [])
        self.assertEqual(plan["renamed"], 0)
        self.assertEqual(plan["new_numbers"], [])


class Sets(unittest.TestCase):

    def test_bounds(self):
        self.assertEqual(PS.set_bounds(1604, 4), (1000, 1999))
        self.assertEqual(PS.set_bounds(2001, 4), (2000, 2999))
        self.assertEqual(PS.set_bounds(6350, 4), (6000, 6999))
        self.assertEqual(PS.set_bounds(12, 3), (0, 99))      # P012
        self.assertEqual(PS.set_bounds(7, 1), (0, 9))
        self.assertEqual(PS.set_bounds(1604, 2), (1000, 1999))  # width never shrinks a number

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
        e = entries("P1998", "P1999", "P2000", "P2001")
        sets = dict(PS.split_sets(e))
        one = sets[(1000, 1999)]
        with self.assertRaises(ValueError):      # P1999 -> 2000 refused
            PS.plan_insertions(one, [1998], bounds=(1000, 1999))
        two = sets[(2000, 2999)]
        plan2 = PS.plan_insertions(two, [2000], bounds=(2000, 2999))
        self.assertEqual(final(plan2), {"P2000": "P2001", "P2001": "P2002",
                                        "NEW": ["P2000"]})

    def test_refusals(self):
        e = entries("P1998", "P1999")
        with self.assertRaises(ValueError) as cm:
            PS.plan_insertions(e, [1998], bounds=(1000, 1999))
        self.assertIn("past the end of this set", str(cm.exception))
        self.assertIn("1999", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            PS.plan_insertions(e, [2001], bounds=(1000, 1999))
        self.assertIn("outside this set", str(cm.exception))
        # with a gap to absorb it, the same insertion is fine
        plan = PS.plan_insertions(entries("P1997", "P1999"), [1997],
                                  bounds=(1000, 1999))
        self.assertEqual(final(plan), {"P1997": "P1998", "P1999": "P1999",
                                       "NEW": ["P1997"]})
        # shift-all into a full set end is refused too
        with self.assertRaises(ValueError):
            PS.plan_insertions(entries("P1997", "P1999"), [1997],
                               fill_gaps=False, bounds=(1000, 1999))


class Preview(unittest.TestCase):

    def test_page_marks_up_changes_duplicates_gaps_and_ignored(self):
        e = entries("P1", "P2", "P2", "P5")
        plan = PS.plan_insertions(e, [1])
        html = PS.preview_html("Piles", [("Model", "A.rvt"), ("Prefix", "P")],
                               plan, ignored=[{"id": 9, "mark": "P7-1",
                                               "reason": "suffix '-1'"}])
        self.assertIn("<tr class='new'>", html)
        self.assertIn("<tr class='renamed dup'>", html)
        self.assertIn("<tr class='gap'>", html)
        self.assertIn("P7-1", html)
        self.assertIn("suffix &#39;-1&#39;".replace("&#39;", "'"), html)
        self.assertIn("freed for a new pile", html)
        self.assertIn("DUPLICATE x2", html)
        self.assertIn("Model: A.rvt", html)
        self.assertNotIn("<script", html)
        # escaping
        html2 = PS.preview_html("<b>", [], PS.plan_insertions(
            entries("P1"), []))
        self.assertIn("&lt;b&gt;", html2)

    def test_hide_unchanged(self):
        plan = PS.plan_insertions(entries("P1", "P2", "P9"), [2])
        html = PS.preview_html("t", [], plan, show_unchanged=False)
        self.assertNotIn(">P1<", html)       # unchanged, hidden
        self.assertIn("Unchanged piles are hidden", html)
        self.assertIn(">P3<", html)          # the renamed P2


if __name__ == "__main__":
    unittest.main()
