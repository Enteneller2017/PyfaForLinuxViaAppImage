# =============================================================================
# Copyright (C) 2010 Diego Duclos
#
# This file is part of pyfa.
#
# pyfa is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# pyfa is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with pyfa.  If not, see <http://www.gnu.org/licenses/>.
# =============================================================================

# noinspection PyPackageRequirements
import wx
# noinspection PyPackageRequirements
import wx.lib.newevent
from logbook import Logger

import gui.builtinViews.emptyView
import gui.display as d
import gui.fitCommands as cmd
import gui.globalEvents as GE
import gui.mainFrame
import gui.multiSwitch
from eos.saveddata.mode import Mode
from eos.saveddata.module import Module, Rack
from eos.const import FittingSlot
from gui.bitmap_loader import BitmapLoader
from gui.builtinMarketBrowser.events import ITEM_SELECTED
from gui.builtinShipBrowser.events import EVT_FIT_REMOVED, EVT_FIT_RENAMED, EVT_FIT_SELECTED, FitSelected
from gui.builtinViewColumns.state import State
from gui.chrome_tabs import EVT_NOTEBOOK_PAGE_CHANGED
from gui.contextMenu import ContextMenu
from gui.utils.staticHelpers import DragDropHelper
from service.fit import Fit
from service.market import Market
from config import slotColourMap

pyfalog = Logger(__name__)


# Tab spawning handler
class FitSpawner(gui.multiSwitch.TabSpawner):
    def __init__(self, multiSwitch):
        self.multiSwitch = multiSwitch
        self.mainFrame = mainFrame = gui.mainFrame.MainFrame.getInstance()
        mainFrame.Bind(EVT_FIT_SELECTED, self.fitSelected)
        self.multiSwitch.tabs_container.handleDrag = self.handleDrag

    def fitSelected(self, event):
        count = -1
        # @todo pheonix: _pages is supposed to be private?
        for index, page in enumerate(self.multiSwitch._pages):
            if not isinstance(page, gui.builtinViews.emptyView.BlankPage):  # Don't try and process it if it's a blank page.
                try:
                    if page.activeFitID == event.fitID:
                        count += 1
                        self.multiSwitch.SetSelection(index)
                        wx.PostEvent(self.mainFrame, GE.FitChanged(fitID=event.fitID))
                        break
                except Exception as e:
                    pyfalog.critical("Caught exception in fitSelected")
                    pyfalog.critical(e)
        if count < 0:
            startup = getattr(event, "startup", False)  # see OpenFitsThread in gui.mainFrame
            from_import = getattr(event, "from_import", False)  # always open imported into a new tab
            sFit = Fit.getInstance()
            openFitInNew = sFit.serviceFittingOptions["openFitInNew"]
            mstate = wx.GetMouseState()

            if from_import or (not openFitInNew and mstate.CmdDown()) or startup or (openFitInNew and not mstate.CmdDown()):
                self.multiSwitch.AddPage()

            view = self.multiSwitch.GetSelectedPage()

            if not isinstance(view, FittingView):
                view = FittingView(self.multiSwitch)
                pyfalog.debug("###################### Created new view:" + repr(view))
                self.multiSwitch.ReplaceActivePage(view)

            view.fitSelected(event)

    def handleDrag(self, type, fitID):
        if type == "fit":
            for page in self.multiSwitch._pages:
                if isinstance(page, FittingView) and page.activeFitID == fitID:
                    index = self.multiSwitch.GetPageIndex(page)
                    self.multiSwitch.SetSelection(index)
                    wx.PostEvent(self.mainFrame, GE.FitChanged(fitID=fitID))
                    return
                elif isinstance(page, gui.builtinViews.emptyView.BlankPage):
                    view = FittingView(self.multiSwitch)
                    self.multiSwitch.ReplaceActivePage(view)
                    view.handleDrag(type, fitID)
                    return

            view = FittingView(self.multiSwitch)
            self.multiSwitch.AddPage(view)
            view.handleDrag(type, fitID)


FitSpawner.register()


# Drag'n'drop handler
class FittingViewDrop(wx.DropTarget):
    def __init__(self, dropFn, *args, **kwargs):
        super(FittingViewDrop, self).__init__(*args, **kwargs)
        self.dropFn = dropFn
        # this is really transferring an EVE itemID
        self.dropData = wx.TextDataObject()
        self.SetDataObject(self.dropData)

    def OnData(self, x, y, t):
        if self.GetData():
            dragged_data = DragDropHelper.data
            # pyfalog.debug("fittingView: recieved drag: " + self.dropData.GetText())
            data = dragged_data.split(':')
            self.dropFn(x, y, data)
        return t


class FittingView(d.Display):
    DEFAULT_COLS = ["State",
                    "Ammo Icon",
                    "Base Icon",
                    "Base Name",
                    "attr:power",
                    "attr:cpu",
                    "Capacitor Usage",
                    "Max Range",
                    "Miscellanea",
                    "Price",
                    "Ammo",
                    ]

    def __init__(self, parent):
        d.Display.__init__(self, parent, size=(0, 0), style=wx.BORDER_NONE)
        self.Show(False)
        self.parent = parent
        self.mainFrame.Bind(GE.FIT_CHANGED, self.fitChanged)
        self.mainFrame.Bind(EVT_FIT_RENAMED, self.fitRenamed)
        self.mainFrame.Bind(EVT_FIT_REMOVED, self.fitRemoved)
        self.mainFrame.Bind(ITEM_SELECTED, self.appendItem)
        self.font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)

        self.Bind(wx.EVT_LEFT_DCLICK, self.removeItem)
        self.Bind(wx.EVT_LIST_BEGIN_DRAG, self.startDrag)
        self.Bind(wx.EVT_CONTEXT_MENU, self.spawnMenu)

        self.SetDropTarget(FittingViewDrop(self.handleListDrag))
        self.activeFitID = None
        self.FVsnapshot = None
        self.itemCount = 0

        self.hoveredRow = None
        self.hoveredColumn = None

        self.Bind(wx.EVT_KEY_UP, self.kbEvent)
        self.Bind(wx.EVT_LEFT_DOWN, self.click)
        self.Bind(wx.EVT_RIGHT_DOWN, self.click)
        self.Bind(wx.EVT_MIDDLE_DOWN, self.click)
        self.Bind(wx.EVT_SHOW, self.OnShow)
        self.Bind(wx.EVT_MOTION, self.OnMouseMove)
        self.Bind(wx.EVT_LEAVE_WINDOW, self.OnLeaveWindow)
        self.parent.Bind(EVT_NOTEBOOK_PAGE_CHANGED, self.pageChanged)
        pyfalog.debug("------------------ new fitting view -------------------")
        pyfalog.debug(self)

    def OnLeaveWindow(self, event):
        self.SetToolTip(None)
        self.hoveredRow = None
        self.hoveredColumn = None
        event.Skip()

    def OnMouseMove(self, event):
        row, _, col = self.HitTestSubItem(event.Position)
        if row != self.hoveredRow or col != self.hoveredColumn:
            if self.ToolTip is not None:
                self.SetToolTip(None)
            else:
                self.hoveredRow = row
                self.hoveredColumn = col
                if row != -1 and row not in self.blanks and col != -1 and col < len(self.DEFAULT_COLS):
                    mod = self.mods[self.GetItemData(row)]
                    tooltip = self.activeColumns[col].getToolTip(mod)
                    if tooltip is not None:
                        self.SetToolTip(tooltip)
                    else:
                        self.SetToolTip(None)
                else:
                    self.SetToolTip(None)
        event.Skip()

    def handleListDrag(self, x, y, data):
        """
        Handles dragging of items from various pyfa displays which support it

        data is list with two items:
            data[0] is hard-coded str of originating source
            data[1] is typeID or index of data we want to manipulate
        """

        if data[0] == "fitting":
            self.swapItems(x, y, int(data[1]))
        elif data[0] == "cargo":
            self.swapCargo(x, y, int(data[1]))
        elif data[0] == "market":
            self.addModule(x, y, int(data[1]))

    def handleDrag(self, type, fitID):
        # Those are drags coming from pyfa sources, NOT builtin wx drags
        if type == "fit":
            wx.PostEvent(self.mainFrame, FitSelected(fitID=fitID))

    def Destroy(self):
        pyfalog.debug("+++++ Destroy " + repr(self))
        d.Display.Destroy(self)

    def pageChanged(self, event):
        if self.parent.IsActive(self):
            fitID = self.getActiveFit()
            sFit = Fit.getInstance()
            sFit.switchFit(fitID)
            wx.PostEvent(self.mainFrame, GE.FitChanged(fitID=fitID))

        event.Skip()

    def getActiveFit(self):
        return self.activeFitID

    def startDrag(self, event):
        row = event.GetIndex()

        if row != -1 and row not in self.blanks and isinstance(self.mods[row], Module) and not self.mods[row].isEmpty:
            data = wx.TextDataObject()
            dataStr = "fitting:" + str(self.mods[row].modPosition)
            data.SetText(dataStr)

            dropSource = wx.DropSource(self)
            dropSource.SetData(data)
            DragDropHelper.data = dataStr
            dropSource.DoDragDrop()

    def getSelectedMods(self):
        sel = []
        row = self.GetFirstSelected()
        while row != -1:
            mod = self.mods[self.GetItemData(row)]
            if mod and not isinstance(mod, Rack):
                sel.append(mod)
            row = self.GetNextSelected(row)

        return sel

    def kbEvent(self, event):
        keycode = event.GetKeyCode()
        if keycode == wx.WXK_DELETE or keycode == wx.WXK_NUMPAD_DELETE:
            row = self.GetFirstSelected()
            modules = []

            while row != -1:
                if row not in self.blanks:
                    mod = self.mods[row]
                    if isinstance(mod, Module) and not mod.isEmpty:
                        modules.append(self.mods[row])
                self.Select(row, 0)
                row = self.GetNextSelected(row)
            self.removeModule(modules)

        event.Skip()

    def fitRemoved(self, event):
        """
        If fit is removed and active, the page is deleted.
        We also refresh the fit of the new current page in case
        delete fit caused change in stats (projected)
        todo: move this to the notebook, not the page. We don't want the page being responsible for deleting itself
        """
        pyfalog.debug("FittingView::fitRemoved")
        if not self:
            event.Skip()
            return
        if event.fitID == self.getActiveFit():
            pyfalog.debug("    Deleted fit is currently active")
            self.parent.DeletePage(self.parent.GetPageIndex(self))

            try:
                # Sometimes there is no active page after deletion, hence the try block
                sFit = Fit.getInstance()

                # stopgap for #1384
                fit = sFit.getFit(self.getActiveFit())
                if fit:
                    sFit.refreshFit(self.getActiveFit())
                    wx.PostEvent(self.mainFrame, GE.FitChanged(fitID=self.activeFitID))
            except RuntimeError:
                pyfalog.warning("Caught dead object")
                pass

        event.Skip()

    def fitRenamed(self, event):
        if not self:
            event.Skip()
            return
        fitID = event.fitID
        if fitID == self.getActiveFit():
            self.updateTab()

        event.Skip()

    def fitSelected(self, event):
        pyfalog.debug('====== Fit Selected: ' + repr(self) + str(bool(self)))

        if self.parent.IsActive(self):
            fitID = event.fitID
            startup = getattr(event, "startup", False)
            self.activeFitID = fitID
            sFit = Fit.getInstance()
            self.updateTab()
            if not startup or startup == 2:  # see OpenFitsThread in gui.mainFrame
                self.Show(fitID is not None)
                self.slotsChanged()
                sFit.switchFit(fitID)
                # @todo pheonix: had to disable this as it was causing a crash at the wxWidgets level. Dunno why, investigate
                wx.PostEvent(self.mainFrame, GE.FitChanged(fitID=fitID))

        event.Skip()

    def updateTab(self):
        sFit = Fit.getInstance()
        fit = sFit.getFit(self.getActiveFit(), basic=True)

        bitmap = BitmapLoader.getImage("race_%s_small" % fit.ship.item.race, "gui")
        text = "%s: %s" % (fit.ship.item.name, fit.name)

        pageIndex = self.parent.GetPageIndex(self)
        if pageIndex is not None:
            self.parent.SetPageTextIcon(pageIndex, text, bitmap)

    def appendItem(self, event):
        """
        Adds items that are double clicks from the market browser. We handle both modules and ammo
        """
        if not self:
            event.Skip()
            return
        if self.parent.IsActive(self):
            itemID = event.itemID
            fitID = self.activeFitID
            if fitID is not None:
                item = Market.getInstance().getItem(itemID, eager='group.category')
                if item is None or not (item.isModule or item.isSubsystem):
                    event.Skip()
                    return
                if item.isCharge:
                    # If we've selected ammo, then apply to the selected module(s)
                    modules = []
                    sel = self.GetFirstSelected()
                    while sel != -1 and sel not in self.blanks:
                        mod = self.mods[self.GetItemData(sel)]
                        if isinstance(mod, Module) and not mod.isEmpty:
                            modules.append(self.mods[self.GetItemData(sel)])
                        sel = self.GetNextSelected(sel)

                    if len(modules) > 0:
                        self.mainFrame.command.Submit(cmd.GuiChangeLocalModuleChargesCommand(fitID, modules, itemID))
                else:
                    self.mainFrame.command.Submit(cmd.GuiAddLocalModuleCommand(fitID, itemID))

        event.Skip()

    def removeItem(self, event):
        """Double Left Click - remove module"""
        if event.CmdDown():
            return
        row, _ = self.HitTest(event.Position)
        if row != -1 and row not in self.blanks and isinstance(self.mods[row], Module):
            col = self.getColumn(event.Position)
            if col != self.getColIndex(State):
                self.removeModule(self.mods[row])
            else:
                if "wxMSW" in wx.PlatformInfo:
                    self.click(event)

    def removeModule(self, modules):
        """Removes a list of modules from the fit"""
        if not isinstance(modules, list):
            modules = [modules]

        self.mainFrame.command.Submit(cmd.GuiRemoveLocalModuleCommand(self.activeFitID, modules))

    def addModule(self, x, y, itemID):
        """Add a module from the market browser (from dragging it)"""

        dstRow, _ = self.HitTest((x, y))
        if dstRow != -1 and dstRow not in self.blanks:
            fitID = self.mainFrame.getActiveFit()
            mod = self.mods[dstRow]
            if not isinstance(mod, Module):  # make sure we're not adding something to a T3D Mode
                return

            self.mainFrame.command.Submit(cmd.GuiAddLocalModuleCommand(fitID, itemID, self.mods[dstRow].modPosition))

    def swapCargo(self, x, y, cargoItemID):
        """Swap a module from cargo to fitting window"""
        mstate = wx.GetMouseState()

        dstRow, _ = self.HitTest((x, y))
        if dstRow != -1 and dstRow not in self.blanks:
            mod = self.mods[dstRow]

            if not isinstance(mod, Module):
                return

            self.mainFrame.command.Submit(cmd.GuiCargoToLocalModuleCommand(
                fitID=self.mainFrame.getActiveFit(),
                cargoItemID=cargoItemID,
                modPosition=mod.modPosition,
                copy=mstate.CmdDown()))

    def swapItems(self, x, y, srcIdx):
        """Swap two modules in fitting window"""
        mstate = wx.GetMouseState()
        sFit = Fit.getInstance()
        fit = sFit.getFit(self.activeFitID)

        dstRow, _ = self.HitTest((x, y))

        if dstRow != -1 and dstRow not in self.blanks:
            mod1 = fit.modules[srcIdx]
            mod2 = self.mods[dstRow]

            if not isinstance(mod2, Module):
                return

            # can't swap modules to different racks
            if mod1.slot != mod2.slot:
                return

            fitID = self.mainFrame.getActiveFit()
            if getattr(mod2, "modPosition") is not None:
                if mstate.CmdDown() and mod2.isEmpty:
                    self.mainFrame.command.Submit(cmd.GuiCloneLocalModuleCommand(
                        fitID=fitID, srcPosition=srcIdx, dstPosition=mod2.modPosition))
                elif not mstate.CmdDown():
                    self.mainFrame.command.Submit(cmd.GuiSwapLocalModulesCommand(
                        fitID=fitID, position1=srcIdx, position2=mod2.modPosition))
            else:
                pyfalog.error("Missing module position for: {0}", str(getattr(mod2, "ID", "Unknown")))

    def generateMods(self):
        """
        Generate module list.

        This also injects dummy modules to visually separate racks. These modules are only
        known to the display, and not the backend, so it's safe.
        """

        sFit = Fit.getInstance()
        fit = sFit.getFit(self.activeFitID)

        slotOrder = [
            FittingSlot.SUBSYSTEM,
            FittingSlot.HIGH,
            FittingSlot.MED,
            FittingSlot.LOW,
            FittingSlot.RIG,
            FittingSlot.SERVICE
        ]

        if fit is not None:
            self.mods = fit.modules[:]
            self.mods.sort(key=lambda _mod: (slotOrder.index(_mod.slot), _mod.position))

            # Blanks is a list of indexes that mark non-module positions (such
            # as Racks and tactical Modes. This allows us to skip over common
            # module operations such as swapping, removing, copying, etc. that
            # would otherwise cause complications
            self.blanks = []  # preliminary markers where blanks will be inserted

            if sFit.serviceFittingOptions["rackSlots"]:
                # flag to know when to add blanks, based on previous slot
                slotDivider = None if sFit.serviceFittingOptions["rackLabels"] else self.mods[0].slot

                # first loop finds where slot dividers must go before modifying self.mods
                for i, mod in enumerate(self.mods):
                    if mod.slot != slotDivider:
                        slotDivider = mod.slot
                        self.blanks.append((i, slotDivider))  # where and what

                # second loop modifies self.mods, rewrites self.blanks to represent actual index of blanks
                for i, (x, slot) in enumerate(self.blanks):
                    self.blanks[i] = x + i  # modify blanks with actual index
                    self.mods.insert(x + i, Rack.buildRack(slot, sum(m.slot == slot for m in self.mods)))

            if fit.mode:
                # Modes are special snowflakes and need a little manual loving
                # We basically append the Mode rack and Mode to the modules
                # while also marking the mode header position in the Blanks list
                if sFit.serviceFittingOptions["rackSlots"]:
                    self.blanks.append(len(self.mods))
                    self.mods.append(Rack.buildRack(FittingSlot.MODE, None))

                self.mods.append(fit.mode)
        else:
            self.mods = None

    def slotsChanged(self):
        self.generateMods()
        self.populate(self.mods)

    def fitChanged(self, event):
        # print('====== Fit Changed: {} {} activeFitID: {}, eventFitID: {}'.format(repr(self), str(bool(self)), self.activeFitID, event.fitID))
        if not self:
            event.Skip()
            return
        try:
            if self.activeFitID is not None and self.activeFitID == event.fitID:
                self.generateMods()
                if self.GetItemCount() != len(self.mods):
                    # This only happens when turning on/off slot divisions
                    self.populate(self.mods)
                self.refresh(self.mods)
                self.Refresh()

            self.Show(self.activeFitID is not None and self.activeFitID == event.fitID)
        except RuntimeError:
            pyfalog.error("Caught dead object")
        finally:
            event.Skip()

    def spawnMenu(self, event):
        if self.activeFitID is None or self.getColumn(self.ScreenToClient(event.Position)) == self.getColIndex(State):
            return

        sMkt = Market.getInstance()
        selection = []
        sel = self.GetFirstSelected()
        contexts = []

        while sel != -1 and sel not in self.blanks:
            mod = self.mods[self.GetItemData(sel)]

            # Test if this is a mode, which is a special snowflake of a Module
            if isinstance(mod, Mode):
                srcContext = "fittingMode"

                itemContext = "Tactical Mode"
                fullContext = (srcContext, itemContext)
                if srcContext not in tuple(fCtxt[0] for fCtxt in contexts):
                    contexts.append(fullContext)

                selection.append(mod)

            elif not mod.isEmpty:
                srcContext = "fittingModule"
                itemContext = sMkt.getCategoryByItem(mod.item).name
                fullContext = (srcContext, itemContext)
                if srcContext not in tuple(fCtxt[0] for fCtxt in contexts):
                    contexts.append(fullContext)

                if mod.charge is not None:
                    srcContext = "fittingCharge"
                    itemContext = sMkt.getCategoryByItem(mod.charge).name
                    fullContext = (srcContext, itemContext)
                    if srcContext not in tuple(fCtxt[0] for fCtxt in contexts):
                        contexts.append(fullContext)

                selection.append(mod)

            sel = self.GetNextSelected(sel)

        sFit = Fit.getInstance()
        fit = sFit.getFit(self.activeFitID)

        contexts.append(("fittingShip", "Ship" if not fit.isStructure else "Citadel"))

        menu = ContextMenu.getMenu(selection, *contexts)
        self.PopupMenu(menu)

    def click(self, event):
        """
        Handle click event on modules.

        This is only useful for the State column. If multiple items are selected,
        and we have clicked the State column, iterate through the selections and
        change State
        """

        row, _, col = self.HitTestSubItem(event.Position)

        # only do State column and ignore invalid rows
        if row != -1 and row not in self.blanks and col == self.getColIndex(State):
            sel = []
            curr = self.GetFirstSelected()

            while curr != -1 and row not in self.blanks:
                sel.append(curr)
                curr = self.GetNextSelected(curr)

            if row not in sel:
                mods = [self.mods[self.GetItemData(row)]]
            else:
                mods = self.getSelectedMods()

            fitID = self.mainFrame.getActiveFit()
            ctrl = event.cmdDown or event.middleIsDown
            click = "ctrl" if ctrl is True else "right" if event.GetButton() == 3 else "left"

            self.mainFrame.command.Submit(cmd.GuiChangeLocalModuleStatesCommand(
                fitID=fitID,
                mainPosition=self.mods[self.GetItemData(row)].modPosition,
                positions=[mod.modPosition for mod in mods],
                click=click))

            # update state tooltip
            tooltip = self.activeColumns[col].getToolTip(self.mods[self.GetItemData(row)])
            if tooltip:
                self.SetToolTip(tooltip)

        else:
            event.Skip()

    def slotColour(self, slot):
        return slotColourMap.get(slot) or self.GetBackgroundColour()

    def refresh(self, stuff):
        """
        Displays fitting

        Sends data to d.Display.refresh where the rows and columns are set up, then does a
        bit of post-processing (colors)
        """
        self.Freeze()
        d.Display.refresh(self, stuff)

        sFit = Fit.getInstance()
        fit = sFit.getFit(self.activeFitID)
        slotMap = {}

        # test for too many modules (happens with t3s / CCP change in slot layout)
        for slot in [e.value for e in FittingSlot]:
            slotMap[slot] = fit.getSlotsFree(slot) < 0

        for i, mod in enumerate(self.mods):
            self.SetItemBackgroundColour(i, self.GetBackgroundColour())

            #  only consider changing color if we're dealing with a Module
            if type(mod) is Module:
                hasRestrictionOverriden = getattr(mod, 'restrictionOverridden', None)
                # If module had broken fitting restrictions but now doesn't,
                # ensure it is now valid, and remove restrictionOverridden
                # variable. More in #1519
                if not fit.ignoreRestrictions and hasRestrictionOverriden:
                    clean = False
                    if mod.fits(fit, False):
                        if not mod.hardpoint:
                            clean = True
                        elif fit.getHardpointsFree(mod.hardpoint) >= 0:
                            clean = True
                    if clean:
                        del mod.restrictionOverridden
                        hasRestrictionOverriden = not hasRestrictionOverriden

                if slotMap[mod.slot] or hasRestrictionOverriden:  # Color too many modules as red
                    self.SetItemBackgroundColour(i, wx.Colour(204, 51, 51))
                elif sFit.serviceFittingOptions["colorFitBySlot"]:  # Color by slot it enabled
                    self.SetItemBackgroundColour(i, self.slotColour(mod.slot))

            # Set rack face to bold
            if isinstance(mod, Rack) and \
                    sFit.serviceFittingOptions["rackSlots"] and \
                    sFit.serviceFittingOptions["rackLabels"]:
                self.font.SetWeight(wx.FONTWEIGHT_BOLD)
                self.SetItemFont(i, self.font)
            else:
                self.font.SetWeight(wx.FONTWEIGHT_NORMAL)
                self.SetItemFont(i, self.font)

        self.Thaw()
        self.itemCount = self.GetItemCount()

        # if 'wxMac' in wx.PlatformInfo:
        #     try:
        #         self.MakeSnapshot()
        #     except Exception as e:
        #         pyfalog.critical("Failed to make snapshot")
        #         pyfalog.critical(e)

    def OnShow(self, event):
        if self and not self.IsShown():
            try:
                self.MakeSnapshot()
            except Exception as e:
                pyfalog.critical("Failed to make snapshot")
                pyfalog.critical(e)
        event.Skip()

    def Snapshot(self):
        return self.FVsnapshot

    # noinspection PyPropertyAccess
    def MakeSnapshot(self, maxColumns=1337):
        if self.FVsnapshot:
            self.FVsnapshot = None

        tbmp = wx.Bitmap(16, 16)
        tdc = wx.MemoryDC()
        tdc.SelectObject(tbmp)
        tdc.SetFont(self.font)

        columnsWidths = []
        for i in range(len(self.DEFAULT_COLS)):
            columnsWidths.append(0)

        sFit = Fit.getInstance()
        try:
            fit = sFit.getFit(self.activeFitID)
        except Exception as e:
            pyfalog.critical("Failed to get fit")
            pyfalog.critical(e)
            return

        if fit is None:
            return

        slotMap = {}

        for slot in [e.value for e in FittingSlot]:
            slotMap[slot] = fit.getSlotsFree(slot) < 0

        padding = 2
        isize = 16
        headerSize = max(isize, tdc.GetTextExtent("W")[0]) + padding * 2

        maxRowHeight = isize
        rows = 0
        for st in self.mods:
            for i, col in enumerate(self.activeColumns):
                if i > maxColumns:
                    break
                name = col.getText(st)

                if not isinstance(name, str):
                    name = ""

                nx, ny = tdc.GetTextExtent(name)
                imgId = col.getImageId(st)
                cw = 0
                if imgId != -1:
                    cw += isize + padding
                if name != "":
                    cw += nx + 4 * padding

                if imgId == -1 and name == "":
                    cw += isize + padding

                maxRowHeight = max(ny, maxRowHeight)
                columnsWidths[i] = max(columnsWidths[i], cw)

            rows += 1

        render = wx.RendererNative.Get()

        # Fix column widths (use biggest between header or items)

        for i, col in enumerate(self.activeColumns):
            if i > maxColumns:
                break

            name = col.columnText
            imgId = col.imageId

            if not isinstance(name, str):
                name = ""

            opts = wx.HeaderButtonParams()

            if name != "":
                opts.m_labelText = name

            if imgId != -1:
                opts.m_labelBitmap = wx.Bitmap(isize, isize)

            width = render.DrawHeaderButton(self, tdc, (0, 0, 16, 16), sortArrow=wx.HDR_SORT_ICON_NONE, params=opts)

            columnsWidths[i] = max(columnsWidths[i], width)

        tdc.SelectObject(wx.NullBitmap)

        maxWidth = padding * 2

        for i in range(len(self.DEFAULT_COLS)):
            if i > maxColumns:
                break
            maxWidth += columnsWidths[i]

        mdc = wx.MemoryDC()
        mbmp = wx.Bitmap(maxWidth, maxRowHeight * rows + padding * 4 + headerSize)

        mdc.SelectObject(mbmp)

        mdc.SetBackground(wx.Brush(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW)))
        mdc.Clear()

        mdc.SetFont(self.font)
        mdc.SetTextForeground(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT))

        cx = padding
        for i, col in enumerate(self.activeColumns):
            if i > maxColumns:
                break

            name = col.columnText
            imgId = col.imageId

            if not isinstance(name, str):
                name = ""

            opts = wx.HeaderButtonParams()
            opts.m_labelAlignment = wx.ALIGN_LEFT
            if name != "":
                opts.m_labelText = name

            if imgId != -1:
                bmp = col.bitmap
                opts.m_labelBitmap = bmp

            render.DrawHeaderButton(self, mdc, (cx, padding, columnsWidths[i], headerSize), wx.CONTROL_CURRENT, sortArrow=wx.HDR_SORT_ICON_NONE, params=opts)

            cx += columnsWidths[i]

        brush = wx.Brush(wx.Colour(224, 51, 51))
        pen = wx.Pen(wx.Colour(224, 51, 51))

        mdc.SetPen(pen)
        mdc.SetBrush(brush)

        cy = padding * 2 + headerSize
        for st in self.mods:
            cx = padding

            if slotMap[st.slot]:
                mdc.DrawRectangle(cx, cy, maxWidth - cx, maxRowHeight)

            for i, col in enumerate(self.activeColumns):
                if i > maxColumns:
                    break

                name = col.getText(st)
                if not isinstance(name, str):
                    name = ""

                imgId = col.getImageId(st)
                tcx = cx

                if imgId != -1:
                    self.imageList.Draw(imgId, mdc, cx, cy, wx.IMAGELIST_DRAW_TRANSPARENT, False)
                    tcx += isize + padding

                if name != "":
                    nx, ny = mdc.GetTextExtent(name)
                    rect = wx.Rect()
                    rect.top = cy
                    rect.left = cx + 2 * padding
                    rect.width = nx
                    rect.height = maxRowHeight + padding
                    mdc.DrawLabel(name, rect, wx.ALIGN_CENTER_VERTICAL)
                    tcx += nx + padding

                cx += columnsWidths[i]

            cy += maxRowHeight

        mdc.SelectObject(wx.NullBitmap)

        self.FVsnapshot = mbmp
