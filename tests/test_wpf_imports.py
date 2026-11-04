#!/usr/bin/env python3
"""Every button script must import WPF types (System.Windows.*, not
System.Windows.Forms) only once WPF is loaded into its IronPython
engine: after 'from pyrevit import ...' (pyrevit.forms references the
WPF assemblies), or after clr.AddReference of PresentationCore,
PresentationFramework and WindowsBase. Bulk Change shipped with
'from System.Windows.Media import BrushConverter' ahead of the pyrevit
import and failed with "Cannot import name BrushConverter".

Run:  python3 tests/test_wpf_imports.py
"""

import ast
import io
import os
import unittest

TAB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "pyMEP.extension", "pyMEP.tab")
WPF = {"PresentationCore", "PresentationFramework", "WindowsBase"}


def early_wpf_imports(path):
    """[(line, module)] of module-level WPF imports that run before WPF
    is loaded."""
    with io.open(path, encoding="utf-8", errors="replace") as f:
        tree = ast.parse(f.read(), path)
    refs = set()
    pyrevit = False
    bad = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            fn = node.value.func
            if (isinstance(fn, ast.Attribute) and fn.attr == "AddReference"
                    and node.value.args and isinstance(node.value.args[0], ast.Constant)):
                refs.add(node.value.args[0].value)
        mods = []
        if isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        elif isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        for mod in mods:
            if mod.startswith("pyrevit"):
                pyrevit = True
            elif (mod.startswith("System.Windows") and not mod.startswith("System.Windows.Forms")
                  and not pyrevit and not WPF <= refs):
                bad.append((node.lineno, mod))
    return bad


class WpfImports(unittest.TestCase):

    def test_no_button_imports_wpf_before_it_is_loaded(self):
        problems = []
        for dp, _dn, files in os.walk(TAB):
            for f in files:
                if f == "script.py":
                    p = os.path.join(dp, f)
                    for line, mod in early_wpf_imports(p):
                        problems.append(u"{0}:{1} imports {2}".format(
                            os.path.relpath(p, TAB), line, mod))
        self.assertEqual(problems, [], u"\n".join(problems))


if __name__ == "__main__":
    unittest.main()
