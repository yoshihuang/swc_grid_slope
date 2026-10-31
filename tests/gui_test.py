import os, sys, math
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, sys.argv[1]); OUT = sys.argv[2]
from unittest import mock
from qgis.core import *
from qgis.gui import QgsMapCanvas, QgsMapMouseEvent
from qgis.PyQt.QtCore import Qt, QPoint, QEvent, QSize
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox
app = QgsApplication([], True); app.initQgis()
canvas = QgsMapCanvas(); canvas.resize(QSize(800, 600)); canvas.show()
crs = QgsCoordinateReferenceSystem("EPSG:3826")
canvas.setDestinationCrs(crs); QgsProject.instance().setCrs(crs)
iface = mock.MagicMock(); iface.mapCanvas.return_value = canvas
dem = QgsRasterLayer(os.path.join(OUT, "terrain.tif"), "DEM"); assert dem.isValid()
QgsProject.instance().addMapLayer(dem)
from swc_grid_slope.dock import SlopeDock
from swc_grid_slope import plugin
d = SlopeDock(iface)
d.rb_dem.setChecked(True); d.cb_dem.setLayer(dem)
canvas.setExtent(QgsRectangle(250050, 2700050, 250350, 2700350)); canvas.refresh()
# ---- v1.2.1: plan layer is mandatory ----------------------------------------
with mock.patch.object(QMessageBox, "warning") as mw:
    d._tool_draw()
    assert mw.called and "計畫範圍" in mw.call_args[0][2] and canvas.mapTool() is not d.draw_tool
    mw.reset_mock(); d.run()
    assert mw.called and "計畫範圍" in mw.call_args[0][2] and d.result is None
    mw.reset_mock(); d._frame_from_plan(); assert mw.called
print("no plan → blocked with warning")
# a small plan in the middle of the rectangle drawn below (so no expansion there)
p0 = canvas.getCoordinateTransform().toMapCoordinates(380, 320)
p1 = canvas.getCoordinateTransform().toMapCoordinates(420, 280)
small = QgsVectorLayer("Polygon?crs=EPSG:3826", "小計畫範圍", "memory")
sf = QgsFeature(); sf.setGeometry(QgsGeometry.fromRect(QgsRectangle(p0, p1)))
small.dataProvider().addFeatures([sf]); QgsProject.instance().addMapLayer(small)
d.cb_plan.setLayer(small)
# draw a rectangle with the draw tool
d.bt_draw.setChecked(True); d._tool_draw()
assert canvas.mapTool() is d.draw_tool
def ev(t, x, y, btn=Qt.MouseButton.LeftButton, mods=Qt.KeyboardModifier.NoModifier):
    return QgsMapMouseEvent(canvas, t, QPoint(x, y), btn, btn, mods)
d.draw_tool.canvasPressEvent(ev(QEvent.Type.MouseButtonPress, 200, 150))
d.draw_tool.canvasMoveEvent(ev(QEvent.Type.MouseMove, 500, 400))
d.draw_tool.canvasReleaseEvent(ev(QEvent.Type.MouseButtonRelease, 600, 450))
f = d.ctrl.frame; print("drawn", round(f.w,2), round(f.h,2), d.lb_grid.text())
assert f.w % 25 == 0 and f.h % 25 == 0
# edit: rotate via the handle and move inside
d.bt_edit.setChecked(True); d._tool_edit(); t = d.edit_tool
pts, _ = t._handles()
rp = t.toCanvasCoordinates(pts["rot"]); cp = t.toCanvasCoordinates(QgsPointXY(f.cx, f.cy))
t.canvasPressEvent(ev(QEvent.Type.MouseButtonPress, int(rp.x()), int(rp.y())))
assert t.handle == "rot", t.handle
t.canvasMoveEvent(ev(QEvent.Type.MouseMove, int(rp.x()) + 120, int(rp.y()) + 20, mods=Qt.KeyboardModifier.ShiftModifier))
t.canvasReleaseEvent(ev(QEvent.Type.MouseButtonRelease, int(rp.x()) + 120, int(rp.y()) + 20))
print("angle after rotate", d.ctrl.frame.angle); assert d.ctrl.frame.angle % 5 == 0 and d.ctrl.frame.angle != 0
pts, _ = t._handles(); br = t.toCanvasCoordinates(pts["br"])
t.canvasPressEvent(ev(QEvent.Type.MouseButtonPress, int(br.x()), int(br.y())))
assert t.handle == "br", t.handle
t.canvasMoveEvent(ev(QEvent.Type.MouseMove, int(br.x()) + 60, int(br.y()) + 60))
t.canvasReleaseEvent(ev(QEvent.Type.MouseButtonRelease, int(br.x()) + 60, int(br.y()) + 60))
print("after resize", d.ctrl.frame.w, d.ctrl.frame.h, d.lb_grid.text())
cp = t.toCanvasCoordinates(QgsPointXY(d.ctrl.frame.cx, d.ctrl.frame.cy))
t.canvasPressEvent(ev(QEvent.Type.MouseButtonPress, int(cp.x()), int(cp.y()))); assert t.handle == "move"
t.canvasMoveEvent(ev(QEvent.Type.MouseMove, int(cp.x()) - 30, int(cp.y())))
t.canvasReleaseEvent(ev(QEvent.Type.MouseButtonRelease, int(cp.x()) - 30, int(cp.y())))
# numeric edit
d.sp_ang.setValue(15); d._ui_to_frame(); assert abs(d.ctrl.frame.angle - 15) < 1e-9
d.cb_cell.setCurrentIndex(0); assert d.ctrl.cell == 10
d.cb_style.setCurrentIndex(1)
# ---- plan polygon: frame must contain it, only overlay cells are produced
plan = QgsVectorLayer("Polygon?crs=EPSG:3826", "計畫範圍", "memory")
pf = QgsFeature(); pf.setGeometry(QgsGeometry.fromWkt(
    "POLYGON((250120 2700150,250260 2700180,250280 2700290,250150 2700300,250120 2700150))"))
plan.dataProvider().addFeatures([pf]); QgsProject.instance().addMapLayer(plan)
pg = QgsGeometry(pf.geometry())
warned = []
d.ctrl.expanded.connect(lambda: warned.append(1))
d.cb_plan.setLayer(plan)
print("after plan selected:", d.lb_grid.text(), "warned", len(warned))
assert d.ctrl.frame.contains(pg)
# draw a tiny rectangle inside the plan → must auto-expand
n0 = len(warned)
d.bt_draw.setChecked(True); d._tool_draw()
c = d.draw_tool.toCanvasCoordinates(QgsPointXY(250200, 2700230))
d.draw_tool.canvasPressEvent(ev(QEvent.Type.MouseButtonPress, int(c.x()), int(c.y())))
d.draw_tool.canvasReleaseEvent(ev(QEvent.Type.MouseButtonRelease, int(c.x()) + 20, int(c.y()) + 20))
assert len(warned) > n0 and d.ctrl.frame.contains(pg), "draw must expand"
print("draw expanded:", d.lb_grid.text())
# move the frame away with the edit tool → re-expanded on release
d._tool_edit(); t = d.edit_tool
cp = t.toCanvasCoordinates(QgsPointXY(d.ctrl.frame.cx, d.ctrl.frame.cy))
n1 = len(warned)
t.canvasPressEvent(ev(QEvent.Type.MouseButtonPress, int(cp.x()), int(cp.y()))); assert t.handle == "move"
t.canvasMoveEvent(ev(QEvent.Type.MouseMove, int(cp.x()) + 150, int(cp.y()) - 120))
t.canvasReleaseEvent(ev(QEvent.Type.MouseButtonRelease, int(cp.x()) + 150, int(cp.y()) - 120))
assert len(warned) > n1 and d.ctrl.frame.contains(pg), "move must re-expand"
# numeric rotation → still contains
d.sp_ang.setValue(25); d._ui_to_frame(); assert d.ctrl.frame.contains(pg)
ov = d.ctrl.overlay_count
r_, c_ = d.ctrl.rows_cols(); print("overlay", ov, "of", r_ * c_); assert ov < r_ * c_
# frame that does not fully contain the plan (e.g. plan edited afterwards) → run blocked
keep_frame = d.ctrl.frame.copy()
from swc_grid_slope import core as _core
d.ctrl.set_frame(_core.Frame(250200, 2700230, 60, 60, 0), snap=False)   # not a user edit: no expansion
assert not d.ctrl.frame.contains(pg)
with mock.patch.object(QMessageBox, "warning") as mw:
    d.run()
    assert mw.called and "未完全包含計畫範圍" in mw.call_args[0][2], mw.call_args
assert d.result is None
print("frame not containing plan → blocked with warning")
d.ctrl.set_frame(keep_frame, snap=False)
d.run()
assert d.result is not None, "run failed"
assert d.result.summary["n_cells"] == ov, (d.result.summary["n_cells"], ov)
assert d.result.aspect_points.featureCount() == ov
assert d.bt_run.text() == "產出坵塊並計算坡度及坡向"
assert not hasattr(d, "ck_all") and not hasattr(d, "ck_aspect")
print("result", d.result.summary["rows"], d.result.summary["cols"], round(d.result.summary["mean_slope"],2))
print("table rows", d.tbl.rowCount(), d.lb_summary.text())
with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(os.path.join(OUT, "gui.csv"), "")):
    d._export_csv()
lay = d._make_layout(); assert lay is not None and len(lay) == 2
assert QgsProject.instance().layoutManager().layoutByName("坵塊法坡向分析圖") is not None
lay = d._make_layout()   # rebuilding overwrites, never duplicates
assert len(QgsProject.instance().layoutManager().layouts()) == 2
# groups / visibility / names (v1.2.0)
root = QgsProject.instance().layerTreeRoot()
top = root.findGroup("坵塊法坡度坡向分析"); assert top is not None
sg, ag = top.findGroup("坡度圖"), top.findGroup("坡向圖"); assert sg is not None and ag is not None
names = [n.name() for n in sg.findLayers()]
print("坡度圖 group:", names, "| 坡向圖 group:", [n.name() for n in ag.findLayers()])
assert set(names) == {"等高線交點", "坵塊邊界", "坵塊方格範圍", "坵塊坡度分析"}
assert not root.findLayer(d.result.points.id()).itemVisibilityChecked(), "intersection points hidden by default"
exdir = os.path.join(OUT, "export"); os.makedirs(exdir, exist_ok=True)
with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=exdir), \
     mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
    d.cb_fmt.setCurrentIndex(0); d._export_layout()
    d.cb_fmt.setCurrentIndex(1); d._export_layout()
print("exports:", sorted(os.listdir(exdir)))
for e in (".pdf", ".png"):
    for k in ("_坡度圖", "_坡向圖"):
        assert os.path.exists(os.path.join(exdir, d._project_name() + k + e))
import shutil; shutil.rmtree(os.path.join(OUT, "guiproj"), ignore_errors=True)
d.le_pname.setText("A")
for rnd in range(2):  # second round: files exist → overwrite prompt; gpkg in use → A_2
    with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=os.path.join(OUT, "guiproj")), \
         mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as mq, \
         mock.patch.object(QMessageBox, "information") as mi, mock.patch.object(QMessageBox, "critical") as mc:
        d.le_folder.setText("") if rnd == 0 else None
        d._save_project()
        assert not mc.called, mc.call_args
        print(mi.call_args[0][2].split("\n\n")[0])
print(sorted(os.listdir(os.path.join(OUT, "guiproj"))))
# run again (replace results), then rerun edit
d._tool_edit(); d.run(); d.unload()
print("GUI TEST PASSED"); sys.stdout.flush(); os._exit(0)
