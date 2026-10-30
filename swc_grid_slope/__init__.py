# -*- coding: utf-8 -*-
"""SWC Grid Slope – 坵塊法坡度分析（水土保持技術規範）"""


def classFactory(iface):  # noqa: N802
    from .plugin import SwcGridSlopePlugin
    return SwcGridSlopePlugin(iface)
