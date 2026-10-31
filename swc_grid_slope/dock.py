# -*- coding: utf-8 -*-
"""主操作面板（Dock）"""

import csv
import math
import os

from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsFeedback,
    QgsGeometry,
    QgsMapLayerProxyModel,
    QgsProject,
)
from qgis.gui import QgsFieldComboBox, QgsMapLayerComboBox

from . import core, styles
from .compat import DIST_METERS
from .layout import ASPECT_LAYOUT_NAME, LAYOUT_NAME, build_aspect_layout, build_layout, export_layout
from .maptools import DrawFrameTool, EditFrameTool, FrameController
from .save import output_paths, save_project_to_folder

GROUP_NAME = "坵塊法坡度分析"
ASPECT_GROUP = "坡向圖"


def _filters(name):
    try:
        return getattr(Qgis.LayerFilter, name)
    except AttributeError:
        return getattr(QgsMapLayerProxyModel, name)


class SlopeDock(QDockWidget):

    def __init__(self, iface, parent=None):
        super().__init__("坵塊法坡度分析（水土保持技術規範）", parent)
        self.setObjectName("SwcGridSlopeDock")
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.ctrl = FrameController(self.canvas)
        self.draw_tool = DrawFrameTool(self.canvas, self.ctrl)
        self.edit_tool = EditFrameTool(self.canvas, self.ctrl)
        self.result = None
        self.result_ids = {}
        self._block = False
        self._build_ui()
        self.ctrl.changed.connect(self._frame_to_ui)
        self.ctrl.expanded.connect(self._frame_expanded)
        self.cb_plan.layerChanged.connect(self._plan_changed)
        self.canvas.mapToolSet.connect(self._tool_changed)
        self.ctrl.cell = self.cell_size()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        w = QWidget()
        v = QVBoxLayout(w)

        # 1. terrain ---------------------------------------------------------
        g1 = QGroupBox("1. 地形資料")
        f1 = QFormLayout(g1)
        self.rb_contour = QRadioButton("等高線")
        self.rb_dem = QRadioButton("DEM（自動產生等高線）")
        self.rb_contour.setChecked(True)
        bg = QButtonGroup(self)
        bg.addButton(self.rb_contour)
        bg.addButton(self.rb_dem)
        h = QHBoxLayout()
        h.addWidget(self.rb_contour)
        h.addWidget(self.rb_dem)
        f1.addRow(h)
        self.cb_contour = QgsMapLayerComboBox()
        self.cb_contour.setFilters(_filters("LineLayer"))
        self.cb_elev = QgsFieldComboBox()
        self.cb_elev.setAllowEmptyFieldName(True)
        self.cb_elev.setLayer(self.cb_contour.currentLayer())
        self.cb_contour.layerChanged.connect(self._contour_changed)
        f1.addRow("等高線圖層", self.cb_contour)
        f1.addRow("高程欄位", self.cb_elev)
        self.cb_dem = QgsMapLayerComboBox()
        self.cb_dem.setFilters(_filters("RasterLayer"))
        self.sp_band = QSpinBox()
        self.sp_band.setRange(1, 99)
        f1.addRow("DEM 圖層", self.cb_dem)
        f1.addRow("波段", self.sp_band)
        self.sp_dh = QDoubleSpinBox()
        self.sp_dh.setRange(0.01, 1000)
        self.sp_dh.setDecimals(2)
        self.sp_dh.setValue(1.0)
        self.sp_dh.setSuffix(" m")
        self.bt_detect = QPushButton("自動偵測")
        self.bt_detect.clicked.connect(self._detect_dh)
        h = QHBoxLayout()
        h.addWidget(self.sp_dh, 1)
        h.addWidget(self.bt_detect)
        f1.addRow("等高線間距 Δh", h)
        self.rb_contour.toggled.connect(self._terrain_mode)
        v.addWidget(g1)

        # 2. cell ------------------------------------------------------------
        g2 = QGroupBox("2. 坵塊設定")
        f2 = QFormLayout(g2)
        self.cb_cell = QComboBox()
        self.cb_cell.addItems(["10 公尺", "25 公尺"])
        self.cb_cell.setCurrentIndex(1)
        self.cb_cell.currentIndexChanged.connect(self._cell_changed)
        f2.addRow("方格坵塊邊長 L", self.cb_cell)
        self.cb_pi = QComboBox()
        self.cb_pi.addItems(["3.14（規範）", "π = 3.14159…"])
        f2.addRow("圓周率 π", self.cb_pi)
        self.cb_plan = QgsMapLayerComboBox()
        self.cb_plan.setFilters(_filters("PolygonLayer"))
        self.cb_plan.setAllowEmptyLayer(True)
        self.cb_plan.setLayer(None)
        f2.addRow("計畫範圍（選填）", self.cb_plan)
        v.addWidget(g2)

        # 3. frame -------------------------------------------------------------
        g3 = QGroupBox("3. 方格範圍")
        f3 = QFormLayout(g3)
        h = QHBoxLayout()
        self.bt_draw = QPushButton("繪製矩形")
        self.bt_draw.setCheckable(True)
        self.bt_edit = QPushButton("平移/旋轉/縮放")
        self.bt_edit.setCheckable(True)
        self.bt_fromplan = QPushButton("依計畫範圍")
        self.bt_fromplan.setToolTip("以計畫範圍之最小外接旋轉矩形建立方格範圍")
        self.bt_draw.clicked.connect(self._tool_draw)
        self.bt_edit.clicked.connect(self._tool_edit)
        self.bt_fromplan.clicked.connect(self._frame_from_plan)
        for b in (self.bt_draw, self.bt_edit, self.bt_fromplan):
            h.addWidget(b)
        f3.addRow(h)
        tip = QLabel("編輯：拖曳內部平移、拖曳角點/邊中點縮放、拖曳橘色圓點旋轉（Shift 5°）；方向鍵微移。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#555;font-size:11px")
        f3.addRow(tip)
        self.sp_cx, self.sp_cy = self._dspin(-1e9, 1e9, 3), self._dspin(-1e9, 1e9, 3)
        self.sp_w, self.sp_h = self._dspin(1, 1e6, 2), self._dspin(1, 1e6, 2)
        self.sp_ang = self._dspin(-180, 180, 2)
        self.sp_ang.setSuffix(" °")
        for s in (self.sp_cx, self.sp_cy, self.sp_w, self.sp_h, self.sp_ang):
            s.editingFinished.connect(self._ui_to_frame)
        f3.addRow("中心 X", self.sp_cx)
        f3.addRow("中心 Y", self.sp_cy)
        f3.addRow("寬 W (m)", self.sp_w)
        f3.addRow("高 H (m)", self.sp_h)
        f3.addRow("旋轉角（逆時針）", self.sp_ang)
        self.ck_snap = QCheckBox("寬高取坵塊邊長整數倍（自左上角起算）")
        self.ck_snap.setChecked(True)
        self.ck_snap.toggled.connect(self._snap_changed)
        f3.addRow(self.ck_snap)
        self.lb_grid = QLabel("尚未建立範圍")
        f3.addRow("方格數", self.lb_grid)
        v.addWidget(g3)

        # 4. compute -----------------------------------------------------------
        g4 = QGroupBox("4. 計算與展示")
        f4 = QFormLayout(g4)
        self.cb_style = QComboBox()
        self.cb_style.addItems(["文字式（分級 / N / S）", "圖式（大字級數 + 圈號交點數）"])
        f4.addRow("標註樣式", self.cb_style)
        self.ck_color = QCheckBox("依坡度分級著色")
        self.ck_color.setChecked(True)
        self.ck_points = QCheckBox("顯示交點")
        self.ck_points.setChecked(True)
        h = QHBoxLayout()
        for c in (self.ck_color, self.ck_points):
            h.addWidget(c)
        f4.addRow(h)
        self.bt_run = QPushButton("產出坵塊並計算坡度及坡向")
        self.bt_run.setStyleSheet("font-weight:bold;padding:6px")
        self.bt_run.clicked.connect(self.run)
        f4.addRow(self.bt_run)
        self.pb = QProgressBar()
        self.pb.setValue(0)
        f4.addRow(self.pb)
        self.tbl = QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(["級別", "坵塊數", "面積 (m²)", "比例 (%)"])
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.setMinimumHeight(200)
        f4.addRow(self.tbl)
        self.lb_summary = QLabel("")
        self.lb_summary.setWordWrap(True)
        f4.addRow(self.lb_summary)
        self.bt_csv = QPushButton("匯出統計 CSV")
        self.bt_csv.clicked.connect(self._export_csv)
        f4.addRow(self.bt_csv)
        v.addWidget(g4)

        # 5. layout ------------------------------------------------------------
        g5 = QGroupBox("5. 出圖版面")
        f5 = QFormLayout(g5)
        self.le_title = QLineEdit(LAYOUT_NAME)
        f5.addRow("坡度圖名", self.le_title)
        self.le_title2 = QLineEdit(ASPECT_LAYOUT_NAME)
        f5.addRow("坡向圖名", self.le_title2)
        self.le_sub = QLineEdit("")
        self.le_sub.setPlaceholderText("例：○○段○○地號 水土保持計畫")
        f5.addRow("副標", self.le_sub)
        self.cb_paper = QComboBox()
        self.cb_paper.addItems(["A3 橫式", "A4 橫式", "A1 橫式"])
        f5.addRow("紙張", self.cb_paper)
        self.ck_align = QCheckBox("地圖旋轉使方格正置")
        f5.addRow(self.ck_align)
        h = QHBoxLayout()
        self.bt_layout = QPushButton("建立版面（坡度圖＋坡向圖）")
        self.bt_layout.clicked.connect(self._make_layout)
        self.bt_export = QPushButton("匯出 PDF/PNG")
        self.bt_export.clicked.connect(self._export_layout)
        h.addWidget(self.bt_layout)
        h.addWidget(self.bt_export)
        f5.addRow(h)
        v.addWidget(g5)

        # 6. save ----------------------------------------------------------------
        g6 = QGroupBox("6. 儲存成果")
        f6 = QFormLayout(g6)
        self.le_folder = QLineEdit()
        self.le_folder.setPlaceholderText("選擇輸出資料夾")
        self.bt_folder = QPushButton("瀏覽…")
        self.bt_folder.clicked.connect(self._choose_folder)
        h = QHBoxLayout()
        h.addWidget(self.le_folder, 1)
        h.addWidget(self.bt_folder)
        f6.addRow("輸出資料夾", h)
        self.le_pname = QLineEdit("坵塊法坡度分析")
        f6.addRow("專案名稱", self.le_pname)
        self.bt_save = QPushButton("儲存專案（資料夾內：專案名稱.qgz + 專案名稱.gpkg）")
        self.bt_save.clicked.connect(self._save_project)
        f6.addRow(self.bt_save)
        v.addWidget(g6)
        v.addStretch(1)

        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(w)
        self.setWidget(sc)
        self._terrain_mode()

    def _dspin(self, lo, hi, dec):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(dec)
        s.setKeyboardTracking(False)
        return s

    # -------------------------------------------------------------- helpers
    def cell_size(self):
        return 10.0 if self.cb_cell.currentIndex() == 0 else 25.0

    def pi_value(self):
        return core.PI_314 if self.cb_pi.currentIndex() == 0 else math.pi

    def _msg(self, text, level=Qgis.MessageLevel.Info, duration=6):
        self.iface.messageBar().pushMessage("坵塊法坡度分析", text, level, duration)

    def _terrain_mode(self):
        c = self.rb_contour.isChecked()
        for wd in (self.cb_contour, self.cb_elev):
            wd.setEnabled(c)
        for wd in (self.cb_dem, self.sp_band):
            wd.setEnabled(not c)
        self.bt_detect.setEnabled(c)

    def _contour_changed(self, lyr):
        self.cb_elev.setLayer(lyr)
        if lyr is None:
            return
        for name in ("ELEV", "elev", "Elevation", "ELEVATION", "Z", "z", "HEIGHT", "height", "Contour", "CONTOUR", "高程"):
            if lyr.fields().indexOf(name) >= 0:
                self.cb_elev.setField(name)
                break

    def _detect_dh(self):
        lyr = self.cb_contour.currentLayer()
        fld = self.cb_elev.currentField()
        if lyr is None or not fld:
            self._msg("請先選擇等高線圖層與高程欄位", Qgis.MessageLevel.Warning)
            return
        dh = core.detect_interval(lyr, fld)
        if dh:
            self.sp_dh.setValue(dh)
            self._msg("偵測到等高線間距 Δh = {:g} m".format(dh), Qgis.MessageLevel.Success)
        else:
            self._msg("無法偵測間距，請手動輸入", Qgis.MessageLevel.Warning)

    def _cell_changed(self):
        self.ctrl.cell = self.cell_size()
        if self.ctrl.frame is not None:
            self.ctrl.set_frame(self.ctrl.frame.copy(), final=True)

    def _snap_changed(self, on):
        self.ctrl.snap = on
        if on and self.ctrl.frame is not None:
            self.ctrl.set_frame(self.ctrl.frame.copy(), final=True)

    def _check_crs(self):
        crs = self.canvas.mapSettings().destinationCrs()
        if crs.isGeographic() or crs.mapUnits() != DIST_METERS:
            QMessageBox.warning(self, "座標系統",
                                "目前專案座標系統（{}）不是以公尺為單位的投影座標。\n"
                                "請先將專案 CRS 設為 TWD97 / TM2（EPSG:3826 或 3825）等公尺制座標。".format(crs.authid()))
            return False
        return True

    # --------------------------------------------------------------- frame
    def _tool_draw(self):
        if not self._check_crs():
            self.bt_draw.setChecked(False)
            return
        self.bt_edit.setChecked(False)
        self.ctrl.cell = self.cell_size()
        self.canvas.setMapTool(self.draw_tool)

    def _tool_edit(self):
        if self.ctrl.frame is None:
            self._msg("請先繪製方格範圍", Qgis.MessageLevel.Warning)
            self.bt_edit.setChecked(False)
            return
        self.bt_draw.setChecked(False)
        if self.ctrl.layer() is None:  # re-show the editable frame after a run
            self.ctrl.set_frame(self.ctrl.frame.copy(), snap=False)
        self.canvas.setMapTool(self.edit_tool)
        self.canvas.setFocus()

    def _tool_changed(self, new, old=None):
        self.bt_draw.setChecked(new is self.draw_tool)
        self.bt_edit.setChecked(new is self.edit_tool)

    def _frame_from_plan(self):
        lyr = self.cb_plan.currentLayer()
        if lyr is None:
            self._msg("請先選擇計畫範圍圖層", Qgis.MessageLevel.Warning)
            return
        if not self._check_crs():
            return
        g = self._plan_geometry()
        if g is None or g.isEmpty():
            self._msg("計畫範圍沒有圖徵", Qgis.MessageLevel.Warning)
            return
        self.ctrl.cell = self.cell_size()
        f = core.Frame.from_geometry(g)
        if self.ctrl.snap:
            # expand to whole cells so that the plan area is fully covered
            L = self.ctrl.cell
            f.w = math.ceil(f.w / L - 1e-6) * L
            f.h = math.ceil(f.h / L - 1e-6) * L
        self.ctrl.set_frame(f, snap=False, final=True)
        self.canvas.setExtent(f.geometry().buffer(self.ctrl.cell, 1).boundingBox())
        self.canvas.refresh()

    def _plan_geometry(self):
        lyr = self.cb_plan.currentLayer()
        if lyr is None:
            return None
        crs = self.canvas.mapSettings().destinationCrs()
        geoms = [QgsGeometry(f.geometry()) for f in (lyr.getSelectedFeatures() if lyr.selectedFeatureCount() else lyr.getFeatures())]
        geoms = [g for g in geoms if not g.isNull()]
        if not geoms:
            return None
        g = QgsGeometry.unaryUnion(geoms)
        if lyr.crs() != crs:
            g.transform(QgsCoordinateTransform(lyr.crs(), crs, QgsProject.instance()))
        return g

    def _plan_changed(self, *args):
        self.ctrl.set_plan(self._plan_geometry())
        self._frame_to_ui()

    def _frame_expanded(self):
        self._msg("矩形方格之範圍須包含計畫範圍圖層，已自動擴充方格範圍。", Qgis.MessageLevel.Warning, 8)

    def _frame_to_ui(self):
        f = self.ctrl.frame
        self._block = True
        if f is None:
            self.lb_grid.setText("尚未建立範圍")
        else:
            self.sp_cx.setValue(f.cx)
            self.sp_cy.setValue(f.cy)
            self.sp_w.setValue(f.w)
            self.sp_h.setValue(f.h)
            self.sp_ang.setValue(f.angle)
            r, c = self.ctrl.rows_cols()
            if self.ctrl.plan_geom is not None:
                self.lb_grid.setText("{} 列 × {} 行；與計畫範圍交集 {} 格（L = {:g} m）".format(
                    r, c, self.ctrl.overlay_count, self.ctrl.cell))
            else:
                self.lb_grid.setText("{} 列 × {} 行 = {} 格（L = {:g} m；未指定計畫範圍）".format(
                    r, c, r * c, self.ctrl.cell))
        self._block = False

    def _ui_to_frame(self):
        if self._block or self.ctrl.frame is None:
            return
        f = core.Frame(self.sp_cx.value(), self.sp_cy.value(), self.sp_w.value(),
                       self.sp_h.value(), self.sp_ang.value())
        self.ctrl.set_frame(f, snap=self.ctrl.snap, final=True)

    # ------------------------------------------------------------------ run
    def run(self):
        if self.ctrl.frame is None:
            self._msg("請先建立方格範圍", Qgis.MessageLevel.Warning)
            return
        if not self._check_crs():
            return
        crs = self.canvas.mapSettings().destinationCrs()
        cell = self.cell_size()
        dh = self.sp_dh.value()
        # 再次確認範圍包含計畫範圍（未包含則自動擴充）
        self.ctrl.cell = cell
        self.ctrl.set_plan(self._plan_geometry())
        frame = self.ctrl.frame.copy()
        fb = QgsFeedback()
        fb.progressChanged.connect(lambda p: self.pb.setValue(int(p)))
        ext = frame.geometry().buffer(cell, 1).boundingBox()

        dem = None
        contour_layer = None
        generated = None
        try:
            if self.rb_contour.isChecked():
                contour_layer = self.cb_contour.currentLayer()
                if contour_layer is None:
                    self._msg("請選擇等高線圖層", Qgis.MessageLevel.Warning)
                    return
                elev = self.cb_elev.currentField() or None
                if not elev:
                    self._msg("等高線未指定高程欄位，無法判斷坡向（將標示為平坦）。", Qgis.MessageLevel.Warning, 8)
                cs = core.ContourSet.from_layer(contour_layer, crs, elev, ext, fb)
            else:
                dem = self.cb_dem.currentLayer()
                if dem is None:
                    self._msg("請選擇 DEM 圖層", Qgis.MessageLevel.Warning)
                    return
                self.pb.setValue(2)
                generated = core.contours_from_dem(dem, self.sp_band.value(), dh, crs, ext)
                cs = core.ContourSet.from_layer(generated, crs, "ELEV", ext, fb)
            if not cs.geoms:
                self._msg("方格範圍內沒有等高線", Qgis.MessageLevel.Warning)
                return
            plan = self._plan_geometry()
            res = core.run_analysis(frame, cell, dh, crs, cs, pi=self.pi_value(), plan_geom=plan,
                                    dem=dem, dem_band=self.sp_band.value(),
                                    compute_aspect=True, feedback=fb)
        except Exception as ex:  # noqa
            import traceback
            QMessageBox.critical(self, "計算失敗", "{}\n\n{}".format(ex, traceback.format_exc()))
            return

        self._add_results(res, generated)
        self.result = res
        ff = next(res.frame_layer.getFeatures())
        self.result_frame = core.Frame(*[ff[k] for k in ("cx", "cy", "w", "h", "angle")])
        self._fill_table(res)
        self.ctrl.hide_preview()
        self.ctrl.remove_layer_only()
        if self.canvas.mapTool() in (self.draw_tool, self.edit_tool):
            self.canvas.unsetMapTool(self.canvas.mapTool())
        self._msg("完成：坵塊 {} 格（與計畫範圍交集），交點 {} 個；已產出坡度與坡向圖層".format(
            res.summary["n_cells"], res.summary["n_points"]), Qgis.MessageLevel.Success)

    def _add_results(self, res, generated):
        prj = QgsProject.instance()
        for lid in list(self.result_ids.values()):
            if prj.mapLayer(lid) is not None:
                prj.removeMapLayer(lid)
        self.result_ids = {}
        root = prj.layerTreeRoot()
        grp = root.findGroup(GROUP_NAME)
        if grp is None:
            grp = root.insertGroup(0, GROUP_NAME)
        cell = res.summary["cell"]
        st = self.cb_style.currentIndex()
        styles.style_cells(res.cells, cell, st, self.ck_color.isChecked())
        styles.style_edges(res.edges, cell, st)
        styles.style_points(res.points)
        styles.style_frame(res.frame_layer)
        order = [("points", res.points), ("edges", res.edges), ("frame", res.frame_layer), ("cells", res.cells)]
        if generated is not None:
            styles.style_contours(generated)
            order.append(("contours", generated))
        for key, lyr in order:
            prj.addMapLayer(lyr, False)
            grp.addLayer(lyr)
            self.result_ids[key] = lyr.id()
        # 坡向圖（點圖層＋坵塊格線），預設關閉顯示以免與坡度標註重疊
        styles.style_aspect_points(res.aspect_points, cell)
        styles.style_aspect_cells(res.aspect_cells)
        sub = grp.findGroup(ASPECT_GROUP) or grp.insertGroup(0, ASPECT_GROUP)
        for key, lyr in (("aspect_points", res.aspect_points), ("aspect_cells", res.aspect_cells)):
            prj.addMapLayer(lyr, False)
            sub.addLayer(lyr)
            self.result_ids[key] = lyr.id()
        sub.setItemVisibilityChecked(False)
        node = root.findLayer(res.points.id())
        if node is not None:
            node.setItemVisibilityChecked(self.ck_points.isChecked())
        self.canvas.refresh()

    def _fill_table(self, res):
        self.tbl.setRowCount(0)
        for d in res.stats:
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)
            vals = ["{}（{}）".format(d["name"], d["range"]), str(d["count"]),
                    "{:,.2f}".format(d["area"]), "{:.2f}".format(d["pct"])]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setBackground(QColor(core.GRADE_COLORS[d["grade"]]).lighter(130))
                self.tbl.setItem(r, c, it)
        s = res.summary
        self.lb_summary.setText(
            "統計範圍：{}　總面積 {:,.2f} m²<br>面積加權平均坡度 {:.2f}%（{}）".format(
                "計畫範圍內" if s["use_plan"] else "方格範圍", s["total_area"], s["mean_slope"],
                core.GRADES[s["mean_grade"]][0]))

    def _export_csv(self):
        if self.result is None:
            self._msg("尚無計算結果", Qgis.MessageLevel.Warning)
            return
        path, _ = QFileDialog.getSaveFileName(self, "匯出統計", "", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["級別", "坡度範圍", "坵塊數", "面積(m2)", "比例(%)"])
            for d in self.result.stats:
                w.writerow([d["name"], d["range"], d["count"], round(d["area"], 2), round(d["pct"], 2)])
            w.writerow(["合計", "", sum(d["count"] for d in self.result.stats),
                        round(self.result.summary["total_area"], 2), 100])
            w.writerow([])
            w.writerow(["坡向", "方位角", "坵塊數", "面積(m2)", "比例(%)"])
            for d in self.result.aspect_stats:
                w.writerow([d["name"], d["range"], d["count"], round(d["area"], 2), round(d["pct"], 2)])
        self._msg("已匯出 {}".format(path), Qgis.MessageLevel.Success)

    # --------------------------------------------------------------- layout
    def _result_layers(self):
        prj = QgsProject.instance()
        d = {k: prj.mapLayer(v) for k, v in self.result_ids.items()}
        plan = self.cb_plan.currentLayer()
        if plan is not None:
            d["plan"] = plan
        if self.rb_contour.isChecked() and self.cb_contour.currentLayer() is not None:
            d["contours"] = self.cb_contour.currentLayer()
        return d

    def _make_layout(self):
        if self.result is None or not self.result_ids:
            self._msg("請先完成坡度計算", Qgis.MessageLevel.Warning)
            return None
        paper = {0: (420.0, 297.0), 1: (297.0, 210.0), 2: (841.0, 594.0)}[self.cb_paper.currentIndex()]
        prj = QgsProject.instance()
        rl = self._result_layers()
        lay = build_layout(prj, rl, self.result_frame,
                           self.result.stats, self.result.summary, title=self.le_title.text() or LAYOUT_NAME,
                           subtitle=self.le_sub.text(), align_to_grid=self.ck_align.isChecked(), paper=paper)
        lay2 = build_aspect_layout(prj, rl, self.result_frame, self.result.aspect_stats, self.result.summary,
                                   title=self.le_title2.text() or ASPECT_LAYOUT_NAME, subtitle=self.le_sub.text(),
                                   align_to_grid=self.ck_align.isChecked(), paper=paper)
        self.iface.openLayoutDesigner(lay)
        self.iface.openLayoutDesigner(lay2)
        return lay, lay2

    def _export_layout(self):
        mgr = QgsProject.instance().layoutManager()
        lay = mgr.layoutByName(self.le_title.text() or LAYOUT_NAME)
        lay2 = mgr.layoutByName(self.le_title2.text() or ASPECT_LAYOUT_NAME)
        if lay is None or lay2 is None:
            made = self._make_layout()
            if made is None:
                return
            lay, lay2 = made
        path, flt = QFileDialog.getSaveFileName(self, "匯出版面（坡度圖；坡向圖自動加上「_坡向圖」）", "",
                                                "PDF (*.pdf);;PNG (*.png)")
        if not path:
            return
        root, ext = os.path.splitext(path)
        if ext.lower() not in (".pdf", ".png"):
            ext = ".png" if "png" in (flt or "").lower() else ".pdf"
        p1, p2 = root + ext, root + "_坡向圖" + ext
        ok1 = export_layout(lay, p1)
        ok2 = export_layout(lay2, p2)
        if ok1 and ok2:
            self._msg("已匯出坡度圖 {} 及坡向圖 {}".format(p1, p2), Qgis.MessageLevel.Success, 10)
        else:
            self._msg("匯出失敗：{}".format("、".join(p for p, ok in ((p1, ok1), (p2, ok2)) if not ok)),
                      Qgis.MessageLevel.Critical)

    # ---------------------------------------------------------------- save
    def _choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "選擇輸出資料夾", self.le_folder.text())
        if d:
            self.le_folder.setText(d)

    def _save_project(self):
        folder = self.le_folder.text().strip()
        if not folder:
            self._choose_folder()
            folder = self.le_folder.text().strip()
            if not folder:
                return
        name = self.le_pname.text().strip() or "坵塊法坡度分析"
        qgz, gpkg = output_paths(folder, name)
        exist = [p for p in (qgz, gpkg) if os.path.exists(p)]
        if exist:
            ans = QMessageBox.question(self, "檔案已存在", "下列檔案已存在，是否覆蓋？\n\n" + "\n".join(exist))
            if ans != QMessageBox.StandardButton.Yes:
                return
        prj = QgsProject.instance()
        layers = [prj.mapLayer(i) for i in self.result_ids.values() if prj.mapLayer(i) is not None]
        for extra in (self.cb_plan.currentLayer(), self.cb_contour.currentLayer() if self.rb_contour.isChecked() else None,
                      self.cb_dem.currentLayer() if self.rb_dem.isChecked() else None):
            if extra is not None:
                layers.append(extra)
        # 其他記憶體暫存圖層不寫入就會遺失，一併保存
        for lyr in prj.mapLayers().values():
            if lyr.providerType() == "memory" and lyr not in layers and lyr is not self.ctrl.layer():
                layers.append(lyr)
        try:
            qgz, gpkg, msgs = save_project_to_folder(prj, folder, name, include_all=False,
                                                     layers=layers, feedback=lambda p: self.pb.setValue(p))
        except Exception as ex:  # noqa
            QMessageBox.critical(self, "儲存失敗", str(ex))
            return
        QMessageBox.information(self, "儲存完成", "資料夾：{}\n專案檔：{}\n資料庫：{}\n\n{}".format(
            folder, os.path.basename(qgz), os.path.basename(gpkg), "\n".join(msgs)))

    # ------------------------------------------------------------- cleanup
    def closeEvent(self, e):
        if self.canvas.mapTool() in (self.draw_tool, self.edit_tool):
            self.canvas.unsetMapTool(self.canvas.mapTool())
        self.ctrl.hide_preview()
        super().closeEvent(e)

    def unload(self):
        try:
            self.canvas.mapToolSet.disconnect(self._tool_changed)
        except Exception:
            pass
        if self.canvas.mapTool() in (self.draw_tool, self.edit_tool):
            self.canvas.unsetMapTool(self.canvas.mapTool())
        self.ctrl.hide_preview()
