# noinspection PyPackageRequirements
import wx

import gui.fitCommands as cmd
import gui.globalEvents as GE
import gui.mainFrame
from gui.contextMenu import ContextMenu
from service.settings import ContextMenuSettings


class FillWithModule(ContextMenu):
    def __init__(self):
        self.mainFrame = gui.mainFrame.MainFrame.getInstance()
        self.settings = ContextMenuSettings.getInstance()

    def display(self, srcContext, selection):
        if not self.settings.get('moduleFill'):
            return False
        return srcContext in ("fittingModule")

    def getText(self, itmContext, selection):
        return u"Fill With {0}".format(itmContext if itmContext is not None else "Module")

    def activate(self, fullContext, selection, i):

        srcContext = fullContext[0]
        fitID = self.mainFrame.getActiveFit()

        if srcContext == "fittingModule":
            self.mainFrame.command.Submit(cmd.GuiFillWithLocalModulesCommand(fitID, selection[0].itemID))
            return  # the command takes care of the PostEvent
        wx.PostEvent(self.mainFrame, GE.FitChanged(fitID=fitID))


FillWithModule.register()
