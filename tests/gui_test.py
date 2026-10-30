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
d.run()
assert d.result is not None, "run failed"
print("result", d.result.summary["rows"], d.result.summary["cols"], round(d.result.summary["mean_slope"],2))
print("table rows", d.tbl.rowCount(), d.lb_summary.text())
with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(os.path.join(OUT, "gui.csv"), "")):
    d._export_csv()
lay = d._make_layout(); assert lay is not None
with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(os.path.join(OUT, "gui_layout.pdf"), "")):
    d._export_layout()
assert os.path.exists(os.path.join(OUT, "gui_layout.pdf"))
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
