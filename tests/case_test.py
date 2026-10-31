# -*- coding: utf-8 -*-
"""End-to-end test on a real project (CASE01).
usage: python case_test.py <repo> <case_dir> <out_dir>"""
import math
import os
import shutil
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, sys.argv[1])
CASE, OUT = sys.argv[2], sys.argv[3]
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
from unittest import mock

from qgis.core import *  # noqa
from qgis.gui import QgsMapCanvas, QgsMapMouseEvent
from qgis.PyQt.QtCore import QEvent, QPoint, QSize, Qt
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox

app = QgsApplication([], True)
app.initQgis()
import numpy as np
from osgeo import gdal

prj = QgsProject.instance()
assert prj.read(os.path.join(CASE, "基地地質圖.qgz"))
print("QGIS", Qgis.QGIS_VERSION, "| project CRS", prj.crs().authid())
canvas = QgsMapCanvas()
canvas.resize(QSize(1000, 800))
canvas.show()
if not prj.crs().isValid():   # project saved by a newer QGIS → CRS not readable
    prj.setCrs(QgsCoordinateReferenceSystem("EPSG:3826"))
canvas.setDestinationCrs(prj.crs())
plan = [l for l in prj.mapLayers().values() if l.name() == "基地範圍"][0]
dem = [l for l in prj.mapLayers().values() if l.name() == "DEM"][0]
pg = QgsGeometry.unaryUnion([f.geometry() for f in plan.getFeatures()])
canvas.setExtent(pg.boundingBox().buffered(120))
canvas.refresh()
cntr = QgsVectorLayer(os.path.join(CASE, "Cntr_1.shp"), "Cntr_1", "ogr")
prj.addMapLayer(cntr)

iface = mock.MagicMock()
iface.mapCanvas.return_value = canvas
from swc_grid_slope import core  # noqa
from swc_grid_slope.dock import SlopeDock  # noqa

d = SlopeDock(iface)
msgs = []
iface.messageBar.return_value.pushMessage.side_effect = lambda *a, **k: msgs.append(a[1])


def ev(t, x, y, mods=Qt.KeyboardModifier.NoModifier):
    b = Qt.MouseButton.LeftButton
    return QgsMapMouseEvent(canvas, t, QPoint(int(x), int(y)), b, b, mods)


def cc(x, y):
    p = d.draw_tool.toCanvasCoordinates(QgsPointXY(x, y))
    return p.x(), p.y()


# independent reference: DEM slope (%) and aspect (deg) via GDAL
ref, _keep = {}, []
for kind in ("slope", "aspect"):
    pth = os.path.join(OUT, kind + ".tif")
    opts = dict(slopeFormat="percent") if kind == "slope" else dict(zeroForFlat=False)
    gdal.DEMProcessing(pth, os.path.join(CASE, "DEM.tif"), kind, computeEdges=True, **opts)
    _rl = QgsRasterLayer(pth, kind)
    _keep.append(_rl)
    ref[kind] = _rl.dataProvider()


def ref_at(kind, x, y):
    v, ok = ref[kind].sample(QgsPointXY(x, y), 1)
    return v if ok and v == v and v > -9000 else None


def angdiff(a, b):
    return abs((a - b + 180) % 360 - 180)


def scenario(tag, mode, cell_idx, style):
    print("\n========== {} ==========".format(tag))
    t0 = time.time()
    if mode == "contour":
        d.rb_contour.setChecked(True)
        d.cb_contour.setLayer(cntr)
        d.cb_elev.setField("ELEV")
        d._detect_dh()
    else:
        d.rb_dem.setChecked(True)
        d.cb_dem.setLayer(dem)
        d.sp_dh.setValue(1.0)
    print("Δh =", d.sp_dh.value())
    d.cb_cell.setCurrentIndex(cell_idx)
    d.cb_style.setCurrentIndex(style)
    d.cb_plan.setLayer(plan)
    L = d.cell_size()

    # 1. draw a rectangle much smaller than the plan → must auto-expand
    n = len(msgs)
    c = pg.centroid().asPoint()
    d._tool_draw()
    x0, y0 = cc(c.x() - 20, c.y() + 20)
    x1, y1 = cc(c.x() + 20, c.y() - 20)
    d.draw_tool.canvasPressEvent(ev(QEvent.Type.MouseButtonPress, x0, y0))
    d.draw_tool.canvasMoveEvent(ev(QEvent.Type.MouseMove, x1, y1))
    d.draw_tool.canvasReleaseEvent(ev(QEvent.Type.MouseButtonRelease, x1, y1))
    exp_msgs = [m for m in msgs[n:] if "自動擴充" in m]
    assert exp_msgs and d.ctrl.frame.contains(pg), "draw → expand"
    print("1 draw small → expanded:", d.lb_grid.text())

    # 2. rotate 30° with the handle (shift-snapped), move, resize smaller → each re-expands
    t = d.edit_tool
    d._tool_edit()
    f = d.ctrl.frame
    hp, _ = t._handles()
    rp = t.toCanvasCoordinates(hp["rot"])
    cpt = t.toCanvasCoordinates(QgsPointXY(f.cx, f.cy))
    r = math.hypot(rp.x() - cpt.x(), rp.y() - cpt.y())
    tx, ty = cpt.x() + r * math.sin(math.radians(30)), cpt.y() - r * math.cos(math.radians(30))
    t.canvasPressEvent(ev(QEvent.Type.MouseButtonPress, rp.x(), rp.y()))
    assert t.handle == "rot"
    t.canvasMoveEvent(ev(QEvent.Type.MouseMove, tx, ty, Qt.KeyboardModifier.ShiftModifier))
    t.canvasReleaseEvent(ev(QEvent.Type.MouseButtonRelease, tx, ty))
    assert d.ctrl.frame.contains(pg)
    print("2 rotate → angle %.1f°, contains plan: %s | %s" % (d.ctrl.frame.angle, d.ctrl.frame.contains(pg), d.lb_grid.text()))
    n = len(msgs)
    cpt = t.toCanvasCoordinates(QgsPointXY(d.ctrl.frame.cx, d.ctrl.frame.cy))
    t.canvasPressEvent(ev(QEvent.Type.MouseButtonPress, cpt.x(), cpt.y()))
    t.canvasMoveEvent(ev(QEvent.Type.MouseMove, cpt.x() + 120, cpt.y() + 90))
    t.canvasReleaseEvent(ev(QEvent.Type.MouseButtonRelease, cpt.x() + 120, cpt.y() + 90))
    assert d.ctrl.frame.contains(pg) and any("自動擴充" in m for m in msgs[n:])
    print("3 move away → re-expanded:", d.lb_grid.text())
    hp, _ = t._handles()
    br = t.toCanvasCoordinates(hp["br"])
    cpt = t.toCanvasCoordinates(QgsPointXY(d.ctrl.frame.cx, d.ctrl.frame.cy))
    t.canvasPressEvent(ev(QEvent.Type.MouseButtonPress, br.x(), br.y()))
    assert t.handle == "br"
    t.canvasMoveEvent(ev(QEvent.Type.MouseMove, cpt.x(), cpt.y()))
    t.canvasReleaseEvent(ev(QEvent.Type.MouseButtonRelease, cpt.x(), cpt.y()))
    assert d.ctrl.frame.contains(pg)
    print("4 shrink → re-expanded:", d.lb_grid.text())
    ov = d.ctrl.overlay_count
    # a frame that misses part of the plan must not run
    good = d.ctrl.frame.copy()
    bad = good.copy()
    bad.w, bad.h = good.w * 0.5, good.h * 0.5
    d.ctrl.set_frame(bad, snap=False)
    prev = d.result
    with mock.patch.object(QMessageBox, "warning") as mw:
        d.run()
        assert mw.called and "未完全包含計畫範圍" in mw.call_args[0][2]
    assert d.result is prev
    print("5 frame missing part of plan → run blocked")
    d.ctrl.set_frame(good, snap=False)

    # 3. run
    t1 = time.time()
    d.run()
    res = d.result
    assert res is not None, "run failed"
    print("run %.1fs | cells %d (overlay preview %d), points %d" % (time.time() - t1, res.summary["n_cells"], ov, res.summary["n_points"]))
    assert res.summary["n_cells"] == ov
    tot = 0.0
    slope_err, asp_err, asp_n = [], [], 0
    for f in res.cells.getFeatures():
        g = f.geometry()
        assert g.intersection(pg).area() > 1e-6
        assert f["n"] <= f["n_top"] + f["n_right"] + f["n_bottom"] + f["n_left"]
        s = f["n"] * math.pi * 0 + f["n"] * 3.14 * f["dh"] / (8 * f["L"]) * 100
        assert abs(round(s, 2) - f["S_pct"]) < 0.011
        assert f["grade"] == core.slope_grade(f["S_pct"])
        tot += f["plan_area"]
        if f["plan_ratio"] > 0.99:
            rs = ref_at("slope", f["cx"], f["cy"])
            ra = ref_at("aspect", f["cx"], f["cy"])
            if rs is not None and rs > 15:
                slope_err.append(f["S_pct"] - rs)
            if ra is not None and f["aspect_deg"] is not None and rs and rs > 15:
                asp_n += 1
                asp_err.append(angdiff(f["aspect_deg"], ra))
    print("area check: cells-in-plan %.1f m² vs plan %.1f m²" % (tot, pg.area()))
    assert abs(tot - pg.area()) < 1.0
    for dd in res.stats:
        print("   %s %-14s %4d 格 %10.1f m² %6.2f%%" % (dd["name"], dd["range"], dd["count"], dd["area"], dd["pct"]))
    print("   mean slope %.2f%% (%s)" % (res.summary["mean_slope"], core.GRADES[res.summary["mean_grade"]][0]))
    print("   aspect:", ", ".join("%s %d" % (dd["name"], dd["count"]) for dd in res.aspect_stats if dd["count"]))
    if slope_err:
        e = np.array(slope_err)
        print("   vs DEM slope (interior cells): median diff %.1f%%, |diff| median %.1f%%" % (np.median(e), np.median(abs(e))))
    if asp_err:
        a = np.array(asp_err)
        print("   vs DEM aspect: median %.0f°, within 45°: %.0f%% of %d cells" % (np.median(a), 100 * np.mean(a <= 45), asp_n))
    nulls = [f["cell_no"] for f in res.aspect_points.getFeatures() if f["aspect_dir"] is None]
    print("   flat/undetermined aspect cells:", len(nulls))

    # 4. layouts + export
    lay = d._make_layout()
    assert lay and len(lay) == 2
    exdir = os.path.join(OUT, tag)
    os.makedirs(exdir)
    with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=exdir), \
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        d.cb_fmt.setCurrentIndex(0)
        d._export_layout()
        d.cb_fmt.setCurrentIndex(1)
        d._export_layout()
    for k in ("_坡度圖", "_坡向圖"):
        for e in (".pdf", ".png"):
            p = os.path.join(exdir, "基地地質圖" + k + e)
            assert os.path.exists(p) and os.path.getsize(p) > 10000, p
    print("exports OK:", sorted(os.listdir(exdir)))
    root = prj.layerTreeRoot()
    top = root.findGroup("坵塊法坡度坡向分析")
    assert top and top.findGroup("坡度圖") and top.findGroup("坡向圖")
    assert {n.name() for n in top.findGroup("坡度圖").findLayers()} == {"等高線交點", "坵塊邊界", "坵塊方格範圍", "坵塊坡度分析"}
    assert not root.findLayer(res.points.id()).itemVisibilityChecked()
    print("edge label size %.2f m (L=%g, layout scale 1:%.0f)" % (
        res.edges.labeling().settings().format().size(), L,
        prj.layoutManager().layoutByName("坵塊法坡度分析圖").itemById("main_map").scale()))
    print("scenario %.1fs" % (time.time() - t0))
    return res


# mandatory plan: nothing selected → draw / run blocked with a warning
d.cb_plan.setLayer(None)
with mock.patch.object(QMessageBox, "warning") as mw:
    d._tool_draw()
    assert mw.called and "計畫範圍" in mw.call_args[0][2]
    mw.reset_mock()
    d.run()
    assert mw.called and d.result is None
print("no plan selected → blocked:", mw.call_args[0][2].splitlines()[0])

scenario("C10_contour_L10", "contour", 0, 0)
scenario("C25_contour_L25_big", "contour", 1, 1)
scenario("D25_dem_L25", "dem", 1, 0)

# 5. save twice into the same folder, then reopen
folder = os.path.join(OUT, "save")
d.le_pname.setText("CASE01_坡度分析")
for rnd in (1, 2):
    with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=folder), \
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
            mock.patch.object(QMessageBox, "information") as mi, mock.patch.object(QMessageBox, "critical") as mc:
        d.le_folder.setText(folder)
        d._save_project()
        assert not mc.called, mc.call_args
        print("\nsave round", rnd, "\n" + mi.call_args[0][2].split("\n\n")[0])
    # layers must stay usable in the running session right after saving
    for l in prj.mapLayers().values():
        if l.providerType() in ("ogr", "gdal"):
            assert l.isValid(), ("invalid after save", l.name())
            if isinstance(l, QgsVectorLayer):
                n1 = l.featureCount()
                n2 = sum(1 for _ in l.getFeatures())
                assert n1 == n2 and n2 > 0, (l.name(), n1, n2)
    print("   in-session layers valid & readable after save:", rnd)
print("folder:", sorted(os.listdir(folder)))
# re-saving must not accumulate extra fid columns
from osgeo import ogr
for gp in sorted(f for f in os.listdir(folder) if f.endswith(".gpkg")):
    ds = ogr.Open(os.path.join(folder, gp))
    for i in range(ds.GetLayerCount()):
        lyr = ds.GetLayer(i)
        names = [lyr.GetLayerDefn().GetFieldDefn(j).GetName().lower() for j in range(lyr.GetLayerDefn().GetFieldCount())]
        assert not any(n.startswith("gpkg_fid") for n in names), (gp, lyr.GetName(), names)
        assert lyr.GetFIDColumn() not in ("gpkg_fid2",), (gp, lyr.GetName())
    print("   %s: FID column of 基地範圍 = %s" % (gp, ds.GetLayerByName("基地範圍").GetFIDColumn()))
    ds = None
qgz = prj.fileName()
prj.clear()
assert prj.read(qgz)
bad = [l.name() for l in prj.mapLayers().values() if not l.isValid() and l.providerType() != "wms"]
print("reopen", os.path.basename(qgz), "layers", len(prj.mapLayers()), "invalid (non-web):", bad,
      "layouts", [x.name() for x in prj.layoutManager().layouts()])
assert not bad
print("\nCASE01 TEST PASSED")
sys.stdout.flush()
os._exit(0)
