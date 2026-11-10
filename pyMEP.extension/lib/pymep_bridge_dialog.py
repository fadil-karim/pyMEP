# -*- coding: utf-8 -*-
"""The Bridges list dialog, shared by Bridge Builder (edit a built bridge
or build a new one) and Update Bridges (which bridges to update).

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
    """mode "edit": pick a bridge to edit or ask for a new one;
    mode "update": tick the bridges to update."""

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
        self.Title = u"Bridge Builder" if edit else u"Update Bridges"
        self.ColPick.Visibility = Visibility.Collapsed if edit else Visibility.Visible
        self.BtnNew.Visibility = Visibility.Visible if edit else Visibility.Collapsed
        self.BtnEdit.Visibility = Visibility.Visible if edit else Visibility.Collapsed
        self.BtnUpdate.Visibility = Visibility.Collapsed if edit else Visibility.Visible
        (self.BtnEdit if edit else self.BtnUpdate).IsDefault = True
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

    def on_update(self, sender, args):
        names = [u"{0}".format(row["Name"]) for row in self.table.Rows if bool(row["Pick"])]
        if not names:
            forms.alert("Tick at least one bridge to update.")
            return
        self.result = ("update", names)
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


def choose(rows, mode, intro=u"", hint=u""):
    """Show the dialog. ("new", None), ("edit", name), ("update", [names])
    or None when cancelled."""
    win = BridgeListWindow(rows, mode, intro, hint)
    win.ShowDialog()
    return win.result
