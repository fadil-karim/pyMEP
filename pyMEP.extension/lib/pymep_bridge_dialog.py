# -*- coding: utf-8 -*-
"""The Bridges list dialog, shared by Bridge Builder (edit a built bridge,
delete one, or build a new one), Update Bridges (which bridges to
update) and Delete Bridges (which bridges to delete or forget).

A WPF window from lib/pymep_bridges.xaml, as the other pyMEP dialogs:
a grid of the bridges tracked in the model with their posts, member
count, last build and whether their posts have moved.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

import os

from pyrevit import forms

XAML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pymep_bridges.xaml")
_Base = getattr(forms, "WPFWindow", object)


class BridgeListWindow(_Base):
    """mode "edit": pick a bridge to edit or delete, or ask for a new one;
    mode "update": tick the bridges to update;
    mode "delete": tick the bridges to delete or forget."""

    def __init__(self, rows, mode, intro, hint):
        forms.WPFWindow.__init__(self, XAML_PATH)
        import clr
        clr.AddReference("System.Data")
        from System.Data import DataTable
        from System.Windows import Visibility
        self.mode = mode
        self.result = None
        table = DataTable("bridges")
        for col, typ in (("Pick", bool), ("Name", str), ("Posts", str), ("Members", int),
                         ("Updated", str), ("Status", str)):
            table.Columns.Add(col, clr.GetClrType(typ))
        for r in rows:
            row = table.NewRow()
            row["Pick"] = bool(r.get("checked", True))
            row["Name"] = u"{0}".format(r.get("name", u""))
            row["Posts"] = u"{0}".format(r.get("posts", u""))
            row["Members"] = int(r.get("members", 0))
            row["Updated"] = u"{0}".format(r.get("updated", u""))
            row["Status"] = u"{0}".format(r.get("status", u""))
            table.Rows.Add(row)
        self.table = table
        self.GridBridges.ItemsSource = table.DefaultView
        self.TxtIntro.Text = intro
        self.TxtHint.Text = hint
        edit = mode == "edit"
        delete = mode == "delete"

        def show(ctrl, on):
            ctrl.Visibility = Visibility.Visible if on else Visibility.Collapsed
        self.Title = {"edit": u"Bridge Builder", "update": u"Update Bridges"}.get(mode, u"Delete Bridges")
        show(self.ColPick, not edit)
        show(self.BtnNew, edit)
        show(self.BtnEdit, edit)
        show(self.BtnDelete, edit)
        show(self.BtnUpdate, mode == "update")
        show(self.BtnForget, delete)
        show(self.BtnDeleteTicked, delete)
        {"edit": self.BtnEdit, "update": self.BtnUpdate}.get(mode, self.BtnDeleteTicked).IsDefault = True
        if rows and edit:
            self.GridBridges.SelectedIndex = 0

    def _selected_name(self):
        item = self.GridBridges.SelectedItem
        if item is None:
            return None
        try:
            return u"{0}".format(item["Name"])
        except Exception:
            return None

    def on_new(self, sender, args):
        self.result = ("new", None)
        self.Close()

    def on_edit(self, sender, args):
        name = self._selected_name()
        if name is None:
            forms.alert("Pick a bridge in the list first, or build a new one.")
            return
        self.result = ("edit", name)
        self.Close()

    def on_row_double_click(self, sender, args):
        if self.mode == "edit":
            self.on_edit(sender, args)

    def _ticked(self):
        return [u"{0}".format(row["Name"]) for row in self.table.Rows if bool(row["Pick"])]

    def on_update(self, sender, args):
        names = self._ticked()
        if not names:
            forms.alert("Tick at least one bridge to update.")
            return
        self.result = ("update", names)
        self.Close()

    def on_delete_one(self, sender, args):
        name = self._selected_name()
        if name is None:
            forms.alert("Pick a bridge in the list first.")
            return
        self.result = ("delete", [name])
        self.Close()

    def on_delete_ticked(self, sender, args):
        names = self._ticked()
        if not names:
            forms.alert("Tick at least one bridge to delete.")
            return
        self.result = ("delete", names)
        self.Close()

    def on_forget(self, sender, args):
        names = self._ticked()
        if not names:
            forms.alert("Tick at least one bridge to forget.")
            return
        self.result = ("forget", names)
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


def choose(rows, mode, intro=u"", hint=u""):
    """Show the dialog. ("new", None), ("edit", name), ("update", [names]),
    ("delete", [names]), ("forget", [names]) or None when cancelled."""
    win = BridgeListWindow(rows, mode, intro, hint)
    win.ShowDialog()
    return win.result
