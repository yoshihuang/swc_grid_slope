# -*- coding: utf-8 -*-
"""Plugin load/unload + Processing algorithm.  usage: python plugin_load_test.py <repo> <out_dir_with_terrain.tif>"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, sys.argv[1])
OUT = sys.argv[2]
from unittest import mock

from qgis.core import *  # noqa

a = QgsApplication([], True)
a.initQgis()
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtWidgets import QMainWindow

iface = mock.MagicMock()
w = QMainWindow()
iface.mainWindow.return_value = w
iface.mapCanvas.return_value = QgsMapCanvas()
import swc_grid_slope as p  # noqa

pl = p.classFactory(iface)
pl.initGui()
pl.toggle(True)
assert pl.dock is not None
from processing.core.Processing import Processing  # noqa

Processing.initialize()
import processing  # noqa


def poly(name, wkt):
    lyr = QgsVectorLayer("Polygon?crs=EPSG:3826", name, "memory")
    f = QgsFeature()
    f.setGeometry(QgsGeometry.fromWkt(wkt))
    lyr.dataProvider().addFeatures([f])
    return lyr


fr = poly("f", "POLYGON((250100 2700100,250300 2700150,250270 2700300,250080 2700250,250100 2700100))")
plan = poly("plan", "POLYGON((250150 2700160,250240 2700180,250230 2700250,250140 2700230,250150 2700160))")
params = {"FRAME": fr, "PLAN": plan, "DEM": os.path.join(OUT, "terrain.tif"), "DH": 1, "CELL": 0,
          "OUT_CELLS": "memory:", "OUT_EDGES": "memory:", "OUT_POINTS": "memory:"}
r = processing.run("swcgridslope:gridslope", params)
print("processing cells", r["OUT_CELLS"].featureCount(), "pts", r["OUT_POINTS"].featureCount())
assert r["OUT_CELLS"].featureCount() > 0
# plan sticking out of the frame → error
params["PLAN"] = poly("plan2", "POLYGON((250150 2700160,250500 2700180,250230 2700250,250150 2700160))")
try:
    processing.run("swcgridslope:gridslope", params)
    raise AssertionError("should have failed")
except QgsProcessingException as ex:
    print("plan outside frame → error:", str(ex).splitlines()[0])
# plan missing → error (required parameter)
params.pop("PLAN")
try:
    processing.run("swcgridslope:gridslope", params)
    raise AssertionError("should have failed")
except QgsProcessingException as ex:
    print("no plan → error:", str(ex).splitlines()[0])
pl.unload()
print("LOAD/UNLOAD OK", Qgis.QGIS_VERSION)
sys.stdout.flush()
os._exit(0)
