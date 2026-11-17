# -*- coding: utf-8 -*-
"""Delete Bridges - remove built bridges, or stop tracking them.

Lists the bridges tracked in this model with the state of their posts and
elements. The ticked ones are either deleted - every recorded element
still in the model and still carrying the bridge's name in Comments (the
deck first, its stiffeners and railings going with it), never the posts,
one undo step per bridge - or forgotten: the record goes, the elements
stay. Either way the posts are free for a new bridge, and a design file
saved before the deletion is not built again unasked.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

__title__ = "Delete\nBridges"
__author__ = "Glent Group"

import datetime
import sys
import time

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

import pymep_bridge as BR
import pymep_bridge_dialog as DLG
import pymep_bridge_revit as RV
import pymep_bridge_sync as SYNC

output = script.get_output()
doc = revit.doc

registry_file = RV.registry_path(doc)
registry = BR.load_registry(registry_file)
if not registry["bridges"]:
    forms.alert("No bridge is tracked in this model (nothing in {0}).".format(registry_file), exitscript=True)
cands = SYNC.design_candidates(SYNC.design_folders(registry))
rows = SYNC.bridge_rows(doc, registry, cands)
for r in rows:
    r["checked"] = False
choice = DLG.choose(
    rows, "delete",
    intro=u"Tick the bridges to remove. Delete removes their elements from the model; Forget only stops "
          u"tracking them and leaves the elements where they are.",
    hint=u"Only elements still marked with the bridge's name in Comments are deleted - never its posts. "
         u"Either way the posts are free for a new bridge. 'post ... gone': that post is no longer in the "
         u"model; the rest of the bridge can still be deleted.")
if choice is None:
    script.exit()
action, names = choice
delete = action == "delete"
verb = u"Delete" if delete else u"Forget"
if forms.alert(SYNC.removal_text(doc, registry, names, delete), title=u"{0} bridges".format(verb),
               options=[verb, u"Cancel"]) != verb:
    script.exit()
notes = SYNC.remove(doc, registry, names, delete, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                    time.time())
try:
    BR.save_registry(registry_file, registry)
except Exception as ex:
    notes.append((u"tracking file", u"could not be saved: {0}".format(ex)))

output.print_md("### {0} Bridges".format(verb))
output.print_table(table_data=[[n, r] for n, r in notes], columns=["Bridge", "Result"])
forms.alert(u"\n".join(u"{0}: {1}".format(n, r) for n, r in notes), title=u"{0} Bridges".format(verb))
