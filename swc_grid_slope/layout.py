# -*- coding: utf-8 -*-
"""出圖版面配置（A3 橫式）：坵塊坡度分析圖 + 圖例 + 指北針 + 比例尺 + 分級統計表 + 計算說明"""

import datetime
import os

from qgis.PyQt.QtGui import QFont

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsLayoutExporter,
    QgsLayoutFrame,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsLayoutItemTextTable,
    QgsLayoutMeasurement,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsLayoutTableColumn,
    QgsLegendStyle,
    QgsPrintLayout,
    QgsRectangle,
    QgsTextFormat,
)

from .compat import DIST_METERS, LAYOUT_MM, E
from .core import GRADES, PI_314
from .styles import _font_family

LAYOUT_NAME = "坵塊法坡度分析圖"
ASPECT_LAYOUT_NAME = "坵塊法坡向分析圖"

if hasattr(Qgis, "LegendComponent"):
    LEGEND_STYLES = (Qgis.LegendComponent.Title, Qgis.LegendComponent.Group,
                     Qgis.LegendComponent.Subgroup, Qgis.LegendComponent.SymbolLabel)
else:  # pragma: no cover
    LEGEND_STYLES = (QgsLegendStyle.Title, QgsLegendStyle.Group,
                     QgsLegendStyle.Subgroup, QgsLegendStyle.SymbolLabel)


def _mm(v):
    return QgsLayoutMeasurement(v, LAYOUT_MM)


def _label(layout, text, x, y, w, h, size=10, bold=False):
    lb = QgsLayoutItemLabel(layout)
    lb.setText(text)
    fmt = QgsTextFormat()
    f = QFont(_font_family())
    f.setBold(bold)
    fmt.setFont(f)
    fmt.setSize(size)
    try:
        lb.setTextFormat(fmt)
    except AttributeError:  # QGIS < 3.24
        f.setPointSizeF(size)
        lb.setFont(f)
    layout.addLayoutItem(lb)
    lb.attemptMove(QgsLayoutPoint(x, y, LAYOUT_MM))
    lb.attemptResize(QgsLayoutSize(w, h, LAYOUT_MM))
    return lb


def _box(layout, x, y, w, h):
    s = QgsLayoutItemShape(layout)
    s.setShapeType(E(QgsLayoutItemShape, "Shape.Rectangle", "Rectangle"))
    from qgis.core import QgsFillSymbol
    s.setSymbol(QgsFillSymbol.createSimple({"color": "0,0,0,0", "style": "no",
                                            "outline_color": "#000000", "outline_width": "0.35"}))
    layout.addLayoutItem(s)
    s.attemptMove(QgsLayoutPoint(x, y, LAYOUT_MM))
    s.attemptResize(QgsLayoutSize(w, h, LAYOUT_MM))
    return s


def _new_layout(project, title, paper):
    mgr = project.layoutManager()
    old = mgr.layoutByName(title)
    if old is not None:
        mgr.removeLayout(old)
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(title)
    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(paper[0], paper[1], LAYOUT_MM))
    mgr.addLayout(layout)
    return layout


def _set_fonts_legend(lg):
    try:
        for st in LEGEND_STYLES:
            ls = lg.style(st)
            tf = ls.textFormat()
            tf.setFont(QFont(_font_family()))
            tf.setSize(11 if st == LEGEND_STYLES[0] else 8)
            ls.setTextFormat(tf)
            lg.setStyle(st, ls)
    except AttributeError:  # QGIS < 3.30
        for st in LEGEND_STYLES:
            f = QFont(_font_family())
            f.setPointSizeF(11 if st == LEGEND_STYLES[0] else 8)
            lg.setStyleFont(st, f)


def _table(layout, x, y, w, h, columns, rows):
    table = QgsLayoutItemTextTable(layout)
    layout.addMultiFrame(table)
    cols = []
    for head, wmm in columns:
        c = QgsLayoutTableColumn(head)
        c.setWidth(wmm)
        cols.append(c)
    table.setColumns(cols)
    table.setContents(rows)
    try:
        hf = QgsTextFormat()
        hf.setFont(QFont(_font_family()))
        hf.setSize(7.5)
        table.setHeaderTextFormat(hf)
        cf = QgsTextFormat()
        cf.setFont(QFont(_font_family()))
        cf.setSize(7.5)
        table.setContentTextFormat(cf)
    except AttributeError:  # QGIS < 3.16
        pass
    table.setGridStrokeWidth(0.2)
    table.setCellMargin(1.0)
    fr = QgsLayoutFrame(layout, table)
    fr.attemptMove(QgsLayoutPoint(x, y, LAYOUT_MM))
    fr.attemptResize(QgsLayoutSize(w, h, LAYOUT_MM))
    table.addFrame(fr)
    table.recalculateFrameSizes()
    return table


def _build(project, title, subtitle, paper, frame, crs, map_layers, legend_layers,
           table_title, table_columns, table_rows, notes, align_to_grid, signature=False,
           compact_legend=None):
    """共用版面：左側地圖，右側指北針／比例尺／圖例／統計表／說明（／專業技師簽證）"""
    layout = _new_layout(project, title, paper)
    W, H = paper
    m = 10.0
    side_w = 95.0
    map_x, map_y = m, m + 12.0
    map_w, map_h = W - 2 * m - side_w - 5.0, H - map_y - m

    _label(layout, title, m, m - 2, map_w, 10, size=20, bold=True)
    if subtitle:
        _label(layout, subtitle, m + map_w - 120, m + 1, 120, 8, size=10)

    # map -------------------------------------------------------------------
    mp = QgsLayoutItemMap(layout)
    mp.setId("main_map")
    layout.addLayoutItem(mp)
    mp.attemptMove(QgsLayoutPoint(map_x, map_y, LAYOUT_MM))
    mp.attemptResize(QgsLayoutSize(map_w, map_h, LAYOUT_MM))
    mp.setFrameEnabled(True)
    mp.setFrameStrokeWidth(_mm(0.5))
    ratio = map_w / map_h
    if align_to_grid:
        gw, gh = frame.w * 1.12, frame.h * 1.12
        cx, cy = frame.cx, frame.cy
    else:
        bb = frame.geometry().boundingBox()
        gw, gh = bb.width() * 1.12, bb.height() * 1.12
        cx, cy = bb.center().x(), bb.center().y()
    if gw / gh > ratio:
        gh = gw / ratio
    else:
        gw = gh * ratio
    mp.setExtent(QgsRectangle(cx - gw / 2, cy - gh / 2, cx + gw / 2, cy + gh / 2))
    if align_to_grid:
        mp.setMapRotation(frame.angle)
    mp.setCrs(crs)
    mp.setLayers(map_layers)
    mp.setKeepLayerSet(True)

    # side panel ------------------------------------------------------------
    sx = W - m - side_w
    y = map_y
    na = QgsLayoutItemPicture(layout)
    svg = ""
    for p in QgsApplication.svgPaths():
        cand = os.path.join(p, "arrows", "NorthArrow_02.svg")
        if os.path.exists(cand):
            svg = cand
            break
    na.setPicturePath(svg)
    layout.addLayoutItem(na)
    na.attemptMove(QgsLayoutPoint(sx + side_w / 2 - 9, y, LAYOUT_MM))
    na.attemptResize(QgsLayoutSize(18, 22, LAYOUT_MM))
    na.setLinkedMap(mp)
    try:
        na.setNorthMode(E(QgsLayoutItemPicture, "NorthMode.GridNorth", "GridNorth"))
    except Exception:
        pass
    y += 25

    sb = QgsLayoutItemScaleBar(layout)
    sb.setStyle("Single Box")
    sb.setLinkedMap(mp)
    sb.setUnits(DIST_METERS)
    sb.setUnitLabel("m")
    sb.setNumberOfSegments(2)
    sb.setNumberOfSegmentsLeft(0)
    seg = 10.0 if frame.w < 300 else (25.0 if frame.w < 800 else 100.0)
    sb.setUnitsPerSegment(seg)
    try:
        tf = sb.textFormat()
        tf.setFont(QFont(_font_family()))
        tf.setSize(8)
        sb.setTextFormat(tf)
    except AttributeError:
        pass
    layout.addLayoutItem(sb)
    sb.attemptMove(QgsLayoutPoint(sx + 5, y, LAYOUT_MM))
    sb.update()
    y += 15

    lg = QgsLayoutItemLegend(layout)
    lg.setTitle("圖例")
    lg.setLinkedMap(mp)
    lg.setAutoUpdateModel(False)
    root = lg.model().rootGroup()
    root.removeAllChildren()
    for lyr in legend_layers:
        node = root.addLayer(lyr)
        if compact_legend is not None and lyr in compact_legend and node is not None:
            # 只列符號項目，不另列圖層名稱（如範例「坡向」）
            hidden = (Qgis.LegendComponent.Hidden if hasattr(Qgis, "LegendComponent")
                      else QgsLegendStyle.Hidden)
            try:
                from qgis.core import QgsLegendRenderer
                QgsLegendRenderer.setNodeLegendStyle(node, hidden)
            except Exception:
                pass
    _set_fonts_legend(lg)
    if compact_legend is not None:
        try:
            lg.setLegendFilterByMapEnabled(True)   # 只顯示圖上出現的項目
        except Exception:
            pass
        try:
            lg.setMaximumSymbolSize(7.0)           # 地圖單位箭頭在圖例中縮小
            lg.setMinimumSymbolSize(5.0)
        except Exception:
            pass
    lg.setSymbolHeight(4)
    lg.setSymbolWidth(7)
    layout.addLayoutItem(lg)
    lg.attemptMove(QgsLayoutPoint(sx, y, LAYOUT_MM))
    lg.setResizeToContents(True)
    lg.adjustBoxSize()
    try:
        lg_h = lg.sizeWithUnits().height()
    except Exception:
        lg_h = 0.0
    if lg_h <= 0:
        lg_h = 12.0 + 6.0 * (len(legend_layers) + 8)
    y += lg_h + 4

    _label(layout, table_title, sx, y, side_w, 6, size=10, bold=True)
    y += 7
    tbl_h = 7.0 + 6.0 * len(table_rows)
    _table(layout, sx, y, side_w, tbl_h, table_columns, table_rows)
    y += tbl_h + 3

    sig_h = 30.0 if signature else 0.0
    nh = max(25.0, H - m - y - sig_h)
    _label(layout, notes, sx, y, side_w, nh, size=8)
    if signature:
        sy = map_y + map_h - sig_h
        _box(layout, sx - 2, sy, side_w + 2, sig_h)
        _box(layout, sx - 2, sy, side_w + 2, 8)
        t = _label(layout, "專業技師簽證", sx, sy + 1.2, side_w, 6, size=10, bold=True)
        try:
            from qgis.PyQt.QtCore import Qt
            t.setHAlign(Qt.AlignmentFlag.AlignHCenter)
        except Exception:
            pass
    _box(layout, sx - 2, map_y, side_w + 2, map_h)
    return layout


def build_layout(project, result_layers, frame, stats, summary, title=LAYOUT_NAME,
                 subtitle="", align_to_grid=False, paper=(420.0, 297.0), extra_layers=None):
    """坡度分析版面。result_layers : dict(cells=, edges=, points=, frame=, plan=, contours=)"""
    lyrs = [result_layers[k] for k in ("points", "edges", "cells", "frame", "plan", "contours")
            if result_layers.get(k) is not None]
    lyrs += list(extra_layers or [])
    rows = [[d["name"], d["range"], str(d["count"]), "{:,.2f}".format(d["area"]), "{:.2f}".format(d["pct"])]
            for d in stats]
    rows.append(["合計", "", str(sum(d["count"] for d in stats)), "{:,.2f}".format(summary.get("total_area", 0)), "100.00"])
    pi_txt = "3.14" if abs(summary.get("pi", PI_314) - PI_314) < 1e-9 else "π"
    notes = (
        "計算說明（水土保持技術規範第 23、25 條）\n"
        "S(%) = n·π·Δh / (8L) × 100，θ = tan⁻¹(S/100)\n"
        "n：方格四邊與等高線交點數總和；π = {pi}\n"
        "Δh：等高線間距 = {dh:g} m；L：坵塊邊長 = {L:g} m\n"
        "坵塊數：{nc} 格（與計畫範圍交集者）\n"
        "面積加權平均坡度：{ms:.2f}%（{mg}）\n"
        "製圖日期：{d}"
    ).format(pi=pi_txt, dh=summary.get("dh", 0), L=summary.get("cell", 0),
             nc=summary.get("n_cells", 0), ms=summary.get("mean_slope", 0.0),
             mg=GRADES[summary.get("mean_grade", 1)][0], d=datetime.date.today().isoformat())
    return _build(project, title, subtitle, paper, frame, result_layers["cells"].crs(), lyrs, lyrs,
                  "坡度分級面積統計" + ("（計畫範圍內）" if summary.get("use_plan") else "（方格範圍）"),
                  (("級別", 11), ("坡度範圍", 25), ("坵塊數", 11), ("面積(m²)", 19), ("比例(%)", 13)),
                  rows, notes, align_to_grid)


def build_aspect_layout(project, result_layers, frame, aspect_stats, summary, title=ASPECT_LAYOUT_NAME,
                        subtitle="", align_to_grid=False, paper=(420.0, 297.0)):
    """坡向分析版面（第 26 條）。result_layers : dict(aspect_points=, aspect_cells=, plan=, contours=)"""
    map_layers = [result_layers[k] for k in ("aspect_points", "aspect_cells", "plan", "contours")
                  if result_layers.get(k) is not None]
    legend_layers = [result_layers[k] for k in ("plan", "aspect_points") if result_layers.get(k) is not None]
    rows = [[d["name"], d["range"], str(d["count"]), "{:,.2f}".format(d["area"]), "{:.2f}".format(d["pct"])]
            for d in aspect_stats if d["count"] > 0 or d["name"] != "平坦"]
    tot = sum(d["area"] for d in aspect_stats)
    rows.append(["合計", "", str(sum(d["count"] for d in aspect_stats)), "{:,.2f}".format(tot), "100.00"])
    notes = (
        "說明（水土保持技術規範第 26 條）\n"
        "坡向係於地形圖上每 {L:g} 公尺畫一方格坵塊，取該\n"
        "方格坵塊內垂直於等高線向下坡之方向。\n"
        "箭頭指向下坡方向，以八方位表示（北 = 0°，順時針）。\n"
        "坵塊數：{nc} 格（與計畫範圍交集者）\n"
        "製圖日期：{d}"
    ).format(L=summary.get("cell", 0), nc=summary.get("n_cells", 0), d=datetime.date.today().isoformat())
    return _build(project, title, subtitle, paper, frame, result_layers["aspect_points"].crs(),
                  map_layers, legend_layers,
                  "坡向面積統計" + ("（計畫範圍內）" if summary.get("use_plan") else "（方格範圍）"),
                  (("坡向", 12), ("方位角", 27), ("坵塊數", 11), ("面積(m²)", 18), ("比例(%)", 13)),
                  rows, notes, align_to_grid, signature=True,
                  compact_legend=[result_layers.get("aspect_points")])


def export_layout(layout, path):
    exp = QgsLayoutExporter(layout)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        st = QgsLayoutExporter.PdfExportSettings()
        return exp.exportToPdf(path, st) == E(QgsLayoutExporter, "ExportResult.Success", "Success")
    st = QgsLayoutExporter.ImageExportSettings()
    st.dpi = 200
    st.generateWorldFile = False
    try:
        st.exportMetadata = False
    except AttributeError:
        pass
    return exp.exportToImage(path, st) == E(QgsLayoutExporter, "ExportResult.Success", "Success")
