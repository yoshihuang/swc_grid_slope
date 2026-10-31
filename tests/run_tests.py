# -*- coding: utf-8 -*-
"""Headless tests: python3.12 run_tests.py <plugin_parent_dir> <out_dir>"""
import math
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, sys.argv[1])
OUT = sys.argv[2]
os.makedirs(OUT, exist_ok=True)

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsFeature, QgsGeometry,
                       QgsPointXY, QgsProject, QgsRasterLayer, QgsVectorLayer)

app = QgsApplication([], True)
app.initQgis()

from osgeo import gdal, osr
import numpy as np

from swc_grid_slope import core, styles, layout as lay, save

crs = QgsCoordinateReferenceSystem("EPSG:3826")


def make_dem(path, fn, x0=250000, y0=2700000, n=400, res=1.0):
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(path, n, n, 1, gdal.GDT_Float32)
    ds.SetGeoTransform([x0, res, 0, y0 + n * res, 0, -res])
    s = osr.SpatialReference(); s.ImportFromEPSG(3826)
    ds.SetProjection(s.ExportToWkt())
    xs = x0 + (np.arange(n) + 0.5) * res
    ys = y0 + n * res - (np.arange(n) + 0.5) * res
    X, Y = np.meshgrid(xs, ys)
    ds.GetRasterBand(1).WriteArray(fn(X - x0, Y - y0).astype("float32"))
    ds = None
    return QgsRasterLayer(path, "DEM")


# ---- 1. pure formula checks against the user's figure ------------------------
assert abs(core.slope_percent(12, 1, 25) - 18.84) < 1e-9
assert abs(core.slope_percent(6, 1, 25) - 9.42) < 1e-9
assert abs(core.slope_percent(46, 1, 25) - 72.22) < 1e-9
for s, g in ((18.84, 3), (34.54, 4), (40.82, 5), (56.52, 6), (12.56, 2), (9.42, 2), (5, 1), (5.01, 2),
             (100, 6), (100.1, 7), (30, 3), (30.0001, 4)):
    assert core.slope_grade(s) == g, (s, core.slope_grade(s), g)
print("formula & grades OK")

# ---- 2. plane DEM, gradient 0.2 along +y (north) → aligned grid --------------
dem = make_dem(os.path.join(OUT, "plane.tif"), lambda X, Y: 100 + 0.2 * Y)
frame = core.Frame(250200, 2700200, 200, 150, 0)
ext = frame.geometry().buffer(25, 1).boundingBox()
gen = core.contours_from_dem(dem, 1, 1.0, crs, ext)
cs = core.ContourSet.from_layer(gen, crs, "ELEV", ext)
res = core.run_analysis(frame, 25, 1.0, crs, cs, dem=dem)
ns = sorted(set(f["n"] for f in res.cells.getFeatures()))
print("plane aligned: n values", ns, "S", sorted(set(f["S_pct"] for f in res.cells.getFeatures())))
assert ns == [10], ns   # 5 contours on each vertical edge ×2
asp = set(f["aspect"] for f in res.cells.getFeatures())
print("aspect", asp)
assert asp == {"南"}, asp  # z rises to the north → faces south

# contour-based aspect
res_c = core.run_analysis(frame, 25, 1.0, crs, cs)
asp = set(f["aspect"] for f in res_c.cells.getFeatures())
assert asp == {"南"}, asp

# ---- 3. rotated frame 30° on plane: n = 2·0.2·25·(|cos|+|sin|) ------------------
fr = core.Frame(250200, 2700200, 200, 150, 30).snap_to_cell(25)
res_r = core.run_analysis(fr, 25, 1.0, crs, cs)
mean_n = np.mean([f["n"] for f in res_r.cells.getFeatures()])
exp_n = 2 * 0.2 * 25 * (abs(math.cos(math.radians(30))) + abs(math.sin(math.radians(30))))
print("rotated: mean n %.2f expected %.2f" % (mean_n, exp_n))
assert abs(mean_n - exp_n) < 1.0

# ---- 4. realistic terrain + plan polygon + layout + save ---------------------
def terrain(X, Y):
    return (120 + 0.35 * Y + 12 * np.sin(X / 45.0) * np.cos(Y / 60.0)
            - 18 * np.exp(-((Y - 200 - 0.25 * (X - 200)) ** 2) / 900.0))

dem2 = make_dem(os.path.join(OUT, "terrain.tif"), terrain)
QgsProject.instance().setCrs(crs)
gen2 = core.contours_from_dem(dem2, 1, 1.0, crs, dem2.extent())
gen2.setName("1m等高線")
plan = QgsVectorLayer("Polygon?crs=EPSG:3826", "計畫範圍", "memory")
pf = QgsFeature()
pf.setGeometry(QgsGeometry.fromPolygonXY([[QgsPointXY(250080, 2700300), QgsPointXY(250200, 2700330),
                                          QgsPointXY(250300, 2700270), QgsPointXY(250290, 2700120),
                                          QgsPointXY(250170, 2700080), QgsPointXY(250070, 2700150),
                                          QgsPointXY(250080, 2700300)]]))
plan.dataProvider().addFeatures([pf])
plan.updateExtents()
pg = QgsGeometry(pf.geometry())
f4 = core.Frame.from_geometry(pg)
print("frame from plan: w %.1f h %.1f angle %.2f" % (f4.w, f4.h, f4.angle))
f4.w = math.ceil(f4.w / 25) * 25
f4.h = math.ceil(f4.h / 25) * 25
cs2 = core.ContourSet.from_layer(gen2, crs, "ELEV", f4.geometry().buffer(25, 1).boundingBox())
res4 = core.run_analysis(f4, 25, 1.0, crs, cs2, plan_geom=pg)
print("rows x cols", res4.summary["rows"], res4.summary["cols"], "points", res4.summary["n_points"])
for d in res4.stats:
    print("  ", d["name"], d["count"], round(d["area"], 1), round(d["pct"], 2))
print("  mean slope", round(res4.summary["mean_slope"], 2))
assert abs(sum(d["area"] for d in res4.stats) - pg.area()) < 1.0

# only cells overlapping the plan are produced
eng = QgsGeometry.createGeometryEngine(pg.constGet()); eng.prepareGeometry()
cnt = 0
for f in res4.cells.getFeatures():
    cnt += 1
    assert f.geometry().intersection(pg).area() > 1e-6, f["cell_no"]
full = res4.summary["rows"] * res4.summary["cols"]
print("overlay cells", cnt, "of", full)
assert cnt < full and cnt == res4.summary["n_cells"]
assert res4.aspect_points.featureCount() == cnt and res4.aspect_cells.featureCount() == cnt
dirs = sorted(set(f["aspect_dir"] for f in res4.aspect_points.getFeatures()), key=lambda v: (v is None, v))
print("aspect dirs", dirs)
assert all(v is None or v % 45 == 0 for v in dirs)
print("aspect stats", [(d["name"], d["count"]) for d in res4.aspect_stats])
assert sum(d["count"] for d in res4.aspect_stats) == cnt

# frame auto-expansion: a small rotated frame inside the plan grows to contain it
small = core.Frame(250180, 2700200, 50, 50, 12)
assert not small.contains(pg)
big = small.expanded_to_contain(pg, 25)
assert big.contains(pg), "expanded frame must contain plan"
assert abs(big.angle - 12) < 1e-9 and abs(big.w % 25) < 1e-6 and abs(big.h % 25) < 1e-6
# grid lines stay on the original lattice: original top-left is a lattice node of the new frame
tl0 = small.to_local(*[small.to_map(-25, 25).x(), small.to_map(-25, 25).y()])
lx, ly = big.to_local(small.to_map(-25, 25).x(), small.to_map(-25, 25).y())
assert abs(((lx + big.w / 2) / 25) - round((lx + big.w / 2) / 25)) < 1e-6
assert abs(((big.h / 2 - ly) / 25) - round((big.h / 2 - ly) / 25)) < 1e-6
print("expanded frame", round(big.w), round(big.h))

# consistency: cell n = sum of side counts (no node hits expected)
for f in res4.cells.getFeatures():
    assert f["n"] <= f["n_top"] + f["n_right"] + f["n_bottom"] + f["n_left"]

from swc_grid_slope.styles import style_plan, style_contours
prj = QgsProject.instance()
style_contours(gen2); style_plan(plan)
for st, tag in ((styles.LABEL_STYLE_TEXT, "A"), (styles.LABEL_STYLE_BIGGRADE, "B")):
    styles.style_cells(res4.cells, 25, st, colorize=(tag == "A"))
    styles.style_edges(res4.edges, 25, st)
    styles.style_points(res4.points)
    styles.style_frame(res4.frame_layer)
    for l in (gen2, plan, res4.cells, res4.edges, res4.points, res4.frame_layer):
        if prj.mapLayer(l.id()) is None:
            prj.addMapLayer(l)
    ff = next(res4.frame_layer.getFeatures())
    rf = core.Frame(*[ff[k] for k in ("cx", "cy", "w", "h", "angle")])
    L = lay.build_layout(prj, dict(cells=res4.cells, edges=res4.edges, points=res4.points,
                                   frame=res4.frame_layer, plan=plan, contours=gen2),
                         rf, res4.stats, res4.summary, subtitle="測試案例", align_to_grid=(tag == "B"),
                         title="坵塊法坡度分析圖" + tag)
    ok = lay.export_layout(L, os.path.join(OUT, "layout_%s.png" % tag))
    print("layout", tag, ok)
    assert ok
    styles.style_aspect_points(res4.aspect_points, 25)
    styles.style_aspect_cells(res4.aspect_cells)
    for l in (res4.aspect_points, res4.aspect_cells):
        if prj.mapLayer(l.id()) is None:
            prj.addMapLayer(l)
    L2 = lay.build_aspect_layout(prj, dict(aspect_points=res4.aspect_points, aspect_cells=res4.aspect_cells,
                                           plan=plan, contours=gen2), rf, res4.aspect_stats, res4.summary,
                                 subtitle="測試案例", align_to_grid=(tag == "B"), title="坵塊法坡向分析圖" + tag)
    ok = lay.export_layout(L2, os.path.join(OUT, "aspect_%s.png" % tag))
    print("aspect layout", tag, ok)
    assert ok

# save
prj.addMapLayer(dem2)
folder = os.path.join(OUT, "proj")
qgz, gpkg, msgs = save.save_project_to_folder(prj, folder, "result")
print("\n".join(msgs))
assert os.path.exists(gpkg) and os.path.exists(qgz) and os.path.basename(qgz) == "result.qgz"
assert not [f for f in os.listdir(folder) if f.startswith("~writing_")]
# saving again into the same folder: the gpkg is now in use → auto suffix
qgz2, gpkg2, _ = save.save_project_to_folder(prj, folder, "result")
print("second save:", os.path.basename(qgz2), os.path.basename(gpkg2))
assert os.path.basename(gpkg2) == "result_2.gpkg"
# reopen
prj.clear()
assert prj.read(qgz2)
bad = [l.name() for l in prj.mapLayers().values() if not l.isValid()]
print("reopened layers:", [l.name() for l in prj.mapLayers().values()], "invalid:", bad)
assert not bad
assert prj.layoutManager().layoutByName("坵塊法坡度分析圖A") is not None
print("ALL TESTS PASSED")
app.exitQgis()
