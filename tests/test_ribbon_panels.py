#!/usr/bin/env python3
"""Every panel on the pyMEP tab can be turned off and on from Settings:
its bundle title must match an entry of pymep_ribbon.HIDEABLE_PANELS
(the versioned Setup panel is the one exception - it carries Settings
itself), and no entry may be left over for a panel that no longer
exists. Titles are matched the way the ribbon code does it, by the
longest prefix, so a title must not match two entries.

Run:  python3 tests/test_ribbon_panels.py
"""

import ast
import io
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pyMEP.extension")
TAB = os.path.join(ROOT, "pyMEP.tab")
RIBBON = os.path.join(ROOT, "lib", "pymep_ribbon.py")


def hideable():
    """HIDEABLE_PANELS without importing the module (it imports clr)."""
    tree = ast.parse(io.open(RIBBON, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "HIDEABLE_PANELS" for t in node.targets):
            return [e.value for e in node.value.elts]
    raise AssertionError("HIDEABLE_PANELS not found")


def panel_titles():
    out = {}
    for name in sorted(os.listdir(TAB)):
        if not name.endswith(".panel"):
            continue
        bundle = os.path.join(TAB, name, "bundle.yaml")
        title = None
        if os.path.isfile(bundle):
            m = re.search(r"^title:\s*(.+?)\s*$", io.open(bundle, encoding="utf-8").read(), re.M)
            if m:
                title = m.group(1).strip().strip("'\"")
        out[name] = title or name.split("_", 1)[-1].replace(".panel", "")
    return out


def key_for(title, prefixes):
    best = None
    for p in prefixes:
        if title.startswith(p) and (best is None or len(p) > len(best)):
            best = p
    return best


class RibbonPanels(unittest.TestCase):

    def test_every_panel_is_hideable(self):
        prefixes = hideable()
        missing = []
        for folder, title in panel_titles().items():
            if title.startswith("pyMEP"):
                continue                         # the Setup panel stays
            if key_for(title, prefixes) is None:
                missing.append(u"{0} ('{1}')".format(folder, title))
        self.assertEqual(missing, [], u"panels Settings cannot hide: " + u", ".join(missing))

    def test_no_stale_entries(self):
        titles = [t for t in panel_titles().values() if not t.startswith("pyMEP")]
        stale = [p for p in hideable() if not any(t.startswith(p) for t in titles)]
        self.assertEqual(stale, [], u"HIDEABLE_PANELS entries with no panel: " + u", ".join(stale))

    def test_one_entry_per_panel(self):
        prefixes = hideable()
        for folder, title in panel_titles().items():
            if title.startswith("pyMEP"):
                continue
            hits = [p for p in prefixes if title.startswith(p)]
            longest = key_for(title, prefixes)
            # a shorter prefix may also match (Networks in Pipe Networks) but the
            # longest must be the panel's own title
            self.assertEqual(longest, title, u"{0}: '{1}' resolves to '{2}'".format(folder, title, longest))
            self.assertTrue(hits, folder)

    def test_entries_are_unique(self):
        prefixes = hideable()
        self.assertEqual(len(prefixes), len(set(prefixes)))

    def test_settings_dialog_builds_its_boxes_from_the_list(self):
        """The Ribbon Panels page must not carry its own copy of the
        panel names - it once stopped at 13 of 18 panels."""
        folder = os.path.join(TAB, "00_Setup.panel", "SetupTools.stack", "Settings.pushbutton")
        script = io.open(os.path.join(folder, "script.py"), encoding="utf-8").read()
        xaml = io.open(os.path.join(folder, "SettingsWindow.xaml"), encoding="utf-8").read()
        self.assertIn("pymep_ribbon.HIDEABLE_PANELS", script)
        self.assertNotIn("ChkPan", script)
        self.assertNotIn("ChkPan", xaml)
        self.assertIn('x:Name="PnlPanelChecks"', xaml)
        for title in hideable():
            self.assertIsNone(re.search(r'<CheckBox[^>]*Content="{0}"'.format(re.escape(title)), xaml),
                              u"a checkbox for '{0}' is hard-coded in the Settings XAML".format(title))


if __name__ == "__main__":
    unittest.main()
