# -*- coding: utf-8 -*-
"""地圖工具：繪製方格範圍矩形，以及平移 / 旋轉 / 改變尺寸"""

import math

from qgis.PyQt.QtCore import QObject, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor

from qgis.core import QgsFeature, QgsGeometry, QgsProject
from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker

from .compat import E, GEOM_LINE, GEOM_POLYGON
from .core import FRAME_FIELDS, Frame, new_memory_layer
from .styles import style_frame

FRAME_LAYER_NAME = "坵塊方格範圍（編輯中）"
HANDLE_PX = 9
ROT_HANDLE_PX = 35


class FrameController(QObject):
    """持有目前方格範圍（Frame）並同步至記憶體暫存圖層與預覽線"""

    changed = pyqtSignal()

    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas
        self.frame = None
        self.cell = 25.0
        self.snap = True
        self._layer_id = None
        self.preview = QgsRubberBand(canvas, GEOM_LINE)
        self.preview.setColor(QColor(0, 120, 255, 160))
        self.preview.setWidth(1)

    # -- layer --------------------------------------------------------------
    def layer(self):
        lyr = QgsProject.instance().mapLayer(self._layer_id) if self._layer_id else None
        return lyr

    def _ensure_layer(self):
        lyr = self.layer()
        if lyr is None:
            crs = self.canvas.mapSettings().destinationCrs()
            lyr = new_memory_layer("Polygon", crs, FRAME_LAYER_NAME, FRAME_FIELDS)
            style_frame(lyr, editing=True)
            QgsProject.instance().addMapLayer(lyr)
            self._layer_id = lyr.id()
        return lyr

    def set_frame(self, frame, snap=None, emit=True):
        if frame is None:
            return
        snap = self.snap if snap is None else snap
        if snap:
            frame.snap_to_cell(self.cell)
        self.frame = frame
        self._sync_layer()
        self._update_preview()
        if emit:
            self.changed.emit()

    def rows_cols(self):
        if self.frame is None:
            return 0, 0
        return (max(1, int(math.ceil(self.frame.h / self.cell - 1e-6))),
                max(1, int(math.ceil(self.frame.w / self.cell - 1e-6))))

    def _sync_layer(self):
        lyr = self._ensure_layer()
        f = self.frame
        rows, cols = self.rows_cols()
        prov = lyr.dataProvider()
        ids = [ft.id() for ft in lyr.getFeatures()]
        if ids:
            prov.deleteFeatures(ids)
        ft = QgsFeature(lyr.fields())
        ft.setGeometry(f.geometry())
        ft.setAttributes([f.cx, f.cy, f.w, f.h, f.angle, self.cell, rows, cols])
        prov.addFeatures([ft])
        lyr.updateExtents()
        lyr.triggerRepaint()

    def _update_preview(self):
        """預覽方格線"""
        self.preview.reset(GEOM_LINE)
        f = self.frame
        if f is None:
            return
        rows, cols = self.rows_cols()
        if rows * cols > 40000:
            return
        L = self.cell
        for c in range(cols + 1):
            x = -f.w / 2 + c * L
            self.preview.addGeometry(QgsGeometry.fromPolylineXY(
                [f.to_map(x, f.h / 2), f.to_map(x, f.h / 2 - rows * L)]), None)
        for r in range(rows + 1):
            y = f.h / 2 - r * L
            self.preview.addGeometry(QgsGeometry.fromPolylineXY(
                [f.to_map(-f.w / 2, y), f.to_map(-f.w / 2 + cols * L, y)]), None)

    def clear(self):
        self.preview.reset(GEOM_LINE)
        lyr = self.layer()
        if lyr is not None:
            QgsProject.instance().removeMapLayer(lyr.id())
        self._layer_id = None
        self.frame = None
        self.changed.emit()

    def hide_preview(self):
        self.preview.reset(GEOM_LINE)

    def remove_layer_only(self):
        lyr = self.layer()
        if lyr is not None:
            QgsProject.instance().removeMapLayer(lyr.id())
        self._layer_id = None


# ---------------------------------------------------------------------------
class DrawFrameTool(QgsMapTool):
    """按住左鍵拖曳繪製矩形；放開後建立範圍"""

    def __init__(self, canvas, controller):
        super().__init__(canvas)
        self.ctrl = controller
        self.start = None
        self.rb = QgsRubberBand(canvas, GEOM_POLYGON)
        self.rb.setColor(QColor(0, 120, 255, 60))
        self.rb.setStrokeColor(QColor(0, 120, 255))
        self.rb.setWidth(2)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def canvasPressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self.start = self.toMapCoordinates(e.pos())

    def canvasMoveEvent(self, e):
        if self.start is None:
            return
        p = self.toMapCoordinates(e.pos())
        self.rb.setToGeometry(QgsGeometry.fromRect(_rect(self.start, p)), None)

    def canvasReleaseEvent(self, e):
        if self.start is None or e.button() != Qt.MouseButton.LeftButton:
            return
        p = self.toMapCoordinates(e.pos())
        r = _rect(self.start, p)
        self.start = None
        self.rb.reset(GEOM_POLYGON)
        if r.width() <= 0 or r.height() <= 0:
            return
        c = r.center()
        self.ctrl.set_frame(Frame(c.x(), c.y(), r.width(), r.height(), 0.0))

    def deactivate(self):
        self.rb.reset(GEOM_POLYGON)
        super().deactivate()


def _rect(a, b):
    from qgis.core import QgsRectangle
    return QgsRectangle(a, b)


# ---------------------------------------------------------------------------
class EditFrameTool(QgsMapTool):
    """
    拖曳內部 → 平移；拖曳角點/邊中點 → 改變尺寸（對側固定）；
    拖曳上方圓形把手 → 旋轉（按住 Shift 以 5° 為單位）。
    方向鍵可微調平移（Shift = 坵塊邊長）。
    """

    def __init__(self, canvas, controller):
        super().__init__(canvas)
        self.ctrl = controller
        self.mode = None
        self.handle = None
        self.start_pt = None
        self.start_frame = None
        self.markers = []
        self.rot_line = QgsRubberBand(canvas, GEOM_LINE)
        self.rot_line.setColor(QColor(255, 120, 0))
        self.rot_line.setWidth(1)
        self.ctrl.changed.connect(self._refresh_handles)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    # -- handles ------------------------------------------------------------
    def _handles(self):
        """{name: (local_x, local_y)} 以及旋轉把手（地圖座標）"""
        f = self.ctrl.frame
        hw, hh = f.w / 2.0, f.h / 2.0
        loc = {
            "tl": (-hw, hh), "t": (0, hh), "tr": (hw, hh), "r": (hw, 0),
            "br": (hw, -hh), "b": (0, -hh), "bl": (-hw, -hh), "l": (-hw, 0),
        }
        out = {k: f.to_map(*v) for k, v in loc.items()}
        mupp = self.canvas().mapUnitsPerPixel()
        out["rot"] = f.to_map(0, hh + ROT_HANDLE_PX * mupp)
        return out, loc

    def _refresh_handles(self):
        for m in self.markers:
            self.canvas().scene().removeItem(m)
        self.markers = []
        self.rot_line.reset(GEOM_LINE)
        if self.ctrl.frame is None or not self.isActive():
            return
        pts, _ = self._handles()
        for k, p in pts.items():
            m = QgsVertexMarker(self.canvas())
            m.setCenter(p)
            if k == "rot":
                m.setIconType(E(QgsVertexMarker, "IconType.ICON_CIRCLE", "ICON_CIRCLE"))
                m.setColor(QColor(255, 120, 0))
            else:
                m.setIconType(E(QgsVertexMarker, "IconType.ICON_BOX", "ICON_BOX"))
                m.setColor(QColor(0, 120, 255))
            m.setIconSize(HANDLE_PX)
            m.setPenWidth(2)
            self.markers.append(m)
        self.rot_line.addPoint(pts["t"])
        self.rot_line.addPoint(pts["rot"])

    def activate(self):
        super().activate()
        self._refresh_handles()

    def deactivate(self):
        for m in self.markers:
            self.canvas().scene().removeItem(m)
        self.markers = []
        self.rot_line.reset(GEOM_LINE)
        super().deactivate()

    def _hit(self, pos):
        if self.ctrl.frame is None:
            return None
        pts, _ = self._handles()
        best, bd = None, HANDLE_PX + 3
        for k, p in pts.items():
            sp = self.toCanvasCoordinates(p)
            d = math.hypot(sp.x() - pos.x(), sp.y() - pos.y())
            if d < bd:
                best, bd = k, d
        if best:
            return best
        mp = self.toMapCoordinates(pos)
        if self.ctrl.frame.geometry().contains(QgsGeometry.fromPointXY(mp)):
            return "move"
        return None

    # -- mouse --------------------------------------------------------------
    def canvasPressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self.ctrl.frame is None:
            return
        h = self._hit(e.pos())
        if h is None:
            return
        self.handle = h
        self.start_pt = self.toMapCoordinates(e.pos())
        self.start_frame = self.ctrl.frame.copy()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def canvasMoveEvent(self, e):
        if self.handle is None:
            h = self._hit(e.pos())
            cur = {None: Qt.CursorShape.ArrowCursor, "move": Qt.CursorShape.SizeAllCursor,
                   "rot": Qt.CursorShape.PointingHandCursor}.get(h, Qt.CursorShape.CrossCursor)
            self.canvas().setCursor(cur)
            return
        p = self.toMapCoordinates(e.pos())
        f0 = self.start_frame
        f = f0.copy()
        if self.handle == "move":
            f.cx = f0.cx + (p.x() - self.start_pt.x())
            f.cy = f0.cy + (p.y() - self.start_pt.y())
            self.ctrl.set_frame(f, snap=False)
        elif self.handle == "rot":
            ang = math.degrees(math.atan2(p.y() - f0.cy, p.x() - f0.cx)) - 90.0
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                ang = round(ang / 5.0) * 5.0
            f.angle = ((ang + 180.0) % 360.0) - 180.0
            self.ctrl.set_frame(f, snap=False)
        else:
            self._resize(f0, f, p)
            self.ctrl.set_frame(f, snap=False)

    def _resize(self, f0, f, p):
        """以對側（角點或邊）為固定點調整尺寸"""
        h = self.handle
        hw, hh = f0.w / 2.0, f0.h / 2.0
        lx, ly = f0.to_local(p.x(), p.y())
        # fixed side in local coords
        left, right, top, bot = -hw, hw, hh, -hh
        if "l" in h:
            left = min(lx, right - 1e-3)
        if "r" in h:
            right = max(lx, left + 1e-3)
        if "t" in h:
            top = max(ly, bot + 1e-3)
        if h in ("b", "bl", "br"):
            bot = min(ly, top - 1e-3)
        w, hgt = right - left, top - bot
        if self.ctrl.snap:
            L = self.ctrl.cell
            nw = max(1, int(round(w / L))) * L
            nh = max(1, int(round(hgt / L))) * L
            if "l" in h:
                left = right - nw
            else:
                right = left + nw
            if "t" in h:
                top = bot + nh
            else:
                bot = top - nh
            w, hgt = nw, nh
        c = f0.to_map((left + right) / 2.0, (top + bot) / 2.0)
        f.cx, f.cy, f.w, f.h = c.x(), c.y(), w, hgt

    def canvasReleaseEvent(self, e):
        if self.handle is not None:
            self.handle = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.ctrl.set_frame(self.ctrl.frame, snap=self.ctrl.snap)

    def keyPressEvent(self, e):
        f = self.ctrl.frame
        if f is None:
            return
        step = self.ctrl.cell if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        d = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
             Qt.Key.Key_Up: (0, step), Qt.Key.Key_Down: (0, -step)}.get(e.key())
        if d is None:
            return
        g = f.copy()
        g.cx += d[0]
        g.cy += d[1]
        self.ctrl.set_frame(g, snap=False)
        e.accept()
