# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication

PLUGIN_DIR = os.path.dirname(__file__)


class SwcGridSlopePlugin(object):
    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.action = None
        self.provider = None

    def initProcessing(self):  # noqa: N802
        from .processing_provider import GridSlopeProvider
        self.provider = GridSlopeProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):  # noqa: N802
        self.initProcessing()
        icon = QIcon(os.path.join(PLUGIN_DIR, "icons", "icon.svg"))
        self.action = QAction(icon, "坵塊法坡度分析", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setToolTip("坵塊法坡度分析（水土保持技術規範第 25、26 條）")
        self.action.triggered.connect(self.toggle)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("坵塊法坡度分析", self.action)

    def toggle(self, checked):
        if self.dock is None:
            from .dock import SlopeDock
            self.dock = SlopeDock(self.iface, self.iface.mainWindow())
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
            self.dock.visibilityChanged.connect(self.action.setChecked)
        self.dock.setVisible(checked)

    def unload(self):
        if self.dock is not None:
            self.dock.unload()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.action is not None:
            self.iface.removePluginMenu("坵塊法坡度分析", self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
