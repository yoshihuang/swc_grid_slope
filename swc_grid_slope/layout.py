# -*- coding: utf-8 -*-
"""出圖版面配置：坵塊坡度分析圖 + 圖例 + 指北針 + 比例尺 + 分級統計表 + 計算說明
（可指定出圖比例尺，並依比例尺自動選擇圖紙尺寸與縱向／橫向）"""

import datetime
import math
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

from .compat import DIST_METERS, LAYOUT_MM, E, _modern_or_legacy, log_ignored
from .core import GRADES, PI_314
from .styles import _font_family

LAYOUT_NAME = "坵塊法坡度分析圖"
ASPECT_LAYOUT_NAME = "坵塊法坡向分析圖"

# QGIS >= 3.32: Qgis.LegendComponent; older releases looked up by name
LEGEND_STYLES = tuple(_modern_or_legacy(Qgis, "LegendComponent." + n, QgsLegendStyle, n)
                      for n in ("Title", "Group", "Subgroup", "SymbolLabel"))
LEGEND_HIDDEN = _modern_or_legacy(Qgis, "LegendComponent.Hidden", QgsLegendStyle, "Hidden")


# 版面固定尺寸（mm）
MARGIN = 10.0
SIDE_W = 95.0
TITLE_H = 12.0
MIN_SCALE_DENOMINATOR = 1200      # 出圖比例尺不得小於 1/1200
DEFAULT_SCALE = 1000

# ISO A 系列（短邊, 長邊）mm，由小到大
PAPERS = (("A4", (210.0, 297.0)), ("A3", (297.0, 420.0)), ("A2", (420.0, 594.0)),
          ("A1", (594.0, 841.0)), ("A0", (841.0, 1189.0)))
LANDSCAPE, PORTRAIT = "橫向", "縱向"


def map_size(paper):
    """圖紙 (W, H) 上地圖框的可用尺寸 (w, h) mm"""
    return paper[0] - 2 * MARGIN - SIDE_W - 5.0, paper[1] - (MARGIN + TITLE_H) - MARGIN


def _ground_extent(frame, extent_layer, align_to_grid):
    """出圖範圍（實際產出之坵塊）：回傳 (cx, cy, 寬, 高)，地圖單位；
    align_to_grid 時寬高沿方格方向量測。"""
    if align_to_grid:
        xs, ys = [], []
        if extent_layer is not None:
            for f in extent_layer.getFeatures():
                for v in f.geometry().vertices():
                    lx, ly = frame.to_local(v.x(), v.y())
                    xs.append(lx)
                    ys.append(ly)
        if not xs:
            xs, ys = [-frame.w / 2, frame.w / 2], [-frame.h / 2, frame.h / 2]
        c0 = frame.to_map((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2)
        return c0.x(), c0.y(), max(xs) - min(xs), max(ys) - min(ys)
    bb = extent_layer.extent() if extent_layer is not None and extent_layer.featureCount() else \
        frame.geometry().boundingBox()
    return bb.center().x(), bb.center().y(), bb.width(), bb.height()


def required_map_size(frame, extent_layer, align_to_grid, scale, margin=10.0):
    """以比例尺 1/scale 出圖時，地圖框至少需要的 (寬, 高) mm（四周各留 margin/2）"""
    _cx, _cy, gw, gh = _ground_extent(frame, extent_layer, align_to_grid)
    return gw * 1000.0 / scale + margin, gh * 1000.0 / scale + margin


def pick_paper(need_w, need_h, size=None, orient=None):
    """依地圖框所需尺寸選擇圖紙。
    size   : None = 自動，或 "A4"…"A0"
    orient : None = 自動，或 LANDSCAPE / PORTRAIT
    回傳 (名稱, 方向, (W, H), 是否容納得下)。自動時取容納得下的最小圖紙（同尺寸優先橫向）；
    都容納不下時取最接近者。"""
    cands = []
    for name, (short, long_) in PAPERS:
        if size is not None and name != size:
            continue
        for ori, wh in ((LANDSCAPE, (long_, short)), (PORTRAIT, (short, long_))):
            if orient is None or orient == ori:
                cands.append((name, ori, wh))
    best_fit, best_any, best_cov = None, None, -1.0
    for name, ori, wh in cands:
        mw, mh = map_size(wh)
        cov = min(mw / need_w, mh / need_h)
        if cov >= 1.0 and best_fit is None:
            best_fit = (name, ori, wh, True)
        if cov > best_cov + 1e-9:
            best_cov, best_any = cov, (name, ori, wh, False)
    return best_fit or best_any


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
    except AttributeError as ex:
        log_ignored("layout.py:129", ex)
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
           compact_legend=None, extent_layer=None, legend_node_order=None, scale=None):
    """共用版面：左側地圖，右側指北針／比例尺／圖例／統計表／說明（／專業技師簽證）
    scale 不為 None 時地圖固定為 1/scale；否則縮放至容納出圖範圍。"""
    layout = _new_layout(project, title, paper)
    W, H = paper
    m = MARGIN
    side_w = SIDE_W
    map_x, map_y = m, m + TITLE_H
    map_w, map_h = map_size(paper)
    sx = W - m - side_w

    _label(layout, title, m, m - 2, max(map_w, 70.0), 10, size=20, bold=True)
    if subtitle:
        if map_w >= 200.0:
            _label(layout, subtitle, m + map_w - 120, m + 1, 120, 8, size=10)
        else:   # 窄版（縱向小圖紙）：副標放在右側欄上方
            _label(layout, subtitle, sx, m + 1, side_w, 8, size=10)

    # map -------------------------------------------------------------------
    mp = QgsLayoutItemMap(layout)
    mp.setId("main_map")
    layout.addLayoutItem(mp)
    mp.attemptMove(QgsLayoutPoint(map_x, map_y, LAYOUT_MM))
    mp.attemptResize(QgsLayoutSize(map_w, map_h, LAYOUT_MM))
    mp.setFrameEnabled(True)
    mp.setFrameStrokeWidth(_mm(0.5))
    ratio = map_w / map_h
    # 圖面範圍：框住實際產出的坵塊（與計畫範圍交集者），而非整個方格範圍
    cx, cy, gw, gh = _ground_extent(frame, extent_layer, align_to_grid)
    if scale:
        gw, gh = map_w * scale / 1000.0, map_h * scale / 1000.0
    else:
        gw, gh = gw * 1.12, gh * 1.12
        if gw / gh > ratio:
            gh = gw / ratio
        else:
            gw = gh * ratio
    mp.setCrs(crs)
    mp.setExtent(QgsRectangle(cx - gw / 2, cy - gh / 2, cx + gw / 2, cy + gh / 2))
    if align_to_grid:
        mp.setMapRotation(frame.angle)
    if scale:
        mp.setScale(float(scale))
    mp.setLayers(map_layers)
    mp.setKeepLayerSet(True)

    # side panel ------------------------------------------------------------
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
    except Exception as ex:
        log_ignored("layout.py:212", ex)
    y += 25

    sb = QgsLayoutItemScaleBar(layout)
    sb.setStyle("Single Box")
    sb.setLinkedMap(mp)
    sb.setUnits(DIST_METERS)
    sb.setUnitLabel("m")
    sb.setNumberOfSegments(2)
    sb.setNumberOfSegmentsLeft(0)
    # 比例尺刻度：每段圖上約 35 mm（兩段須容納於右側欄），取 1-2-5 整數
    raw = 35.0 * gw / map_w
    mag = 10 ** math.floor(math.log10(max(raw, 1e-6)))
    seg = min((v * mag for v in (1, 2, 2.5, 5, 10)), key=lambda v: abs(v - raw))
    sb.setUnitsPerSegment(seg)
    try:
        tf = sb.textFormat()
        tf.setFont(QFont(_font_family()))
        tf.setSize(8)
        sb.setTextFormat(tf)
    except AttributeError as ex:
        log_ignored("layout.py:233", ex)
    layout.addLayoutItem(sb)
    sb.attemptMove(QgsLayoutPoint(sx + 5, y, LAYOUT_MM))
    sb.update()
    if scale:
        _label(layout, "比例尺 1 / {:,}".format(int(round(scale))), sx + 5, y + 10.5, side_w - 5, 5, size=9)
        y += 5
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
            hidden = LEGEND_HIDDEN
            try:
                from qgis.core import QgsLegendRenderer
                QgsLegendRenderer.setNodeLegendStyle(node, hidden)
            except Exception as ex:
                log_ignored("layout.py:254", ex)
            if legend_node_order is not None:
                # 只列出圖上實際出現的項目（不使用「依地圖篩選圖例」，
                # 該功能在 QGIS 3.x 匯出時會等待背景繪圖而卡住）
                try:
                    from qgis.core import QgsMapLayerLegendUtils
                    QgsMapLayerLegendUtils.setLegendNodeOrder(node, legend_node_order)
                    lg.model().refreshLayerLegend(node)
                except Exception as ex:
                    log_ignored("layout.py:263", ex)
    _set_fonts_legend(lg)
    if compact_legend is not None:
        try:
            lg.setMaximumSymbolSize(7.0)           # 地圖單位箭頭在圖例中縮小
            lg.setMinimumSymbolSize(5.0)
        except Exception as ex:
            log_ignored("layout.py:270", ex)
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
        except Exception as ex:
            log_ignored("layout.py:303", ex)
    _box(layout, sx - 2, map_y, side_w + 2, map_h)
    return layout


def build_layout(project, result_layers, frame, stats, summary, title=LAYOUT_NAME,
                 subtitle="", align_to_grid=False, paper=(420.0, 297.0), extra_layers=None,
                 scale=None, bld_summary=None, bld_stats=None):
    """坡度分析版面。result_layers : dict(cells=, edges=, points=, frame=, plan=, contours=, building=)"""
    lyrs = [result_layers[k] for k in ("building", "points", "edges", "cells", "plan", "contours")
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
    ).format(pi=pi_txt, dh=summary.get("dh", 0), L=summary.get("cell", 0),
             nc=summary.get("n_cells", 0), ms=summary.get("mean_slope", 0.0),
             mg=GRADES[summary.get("mean_grade", 1)][0])
    if bld_summary and bld_summary.get("n_cells"):
        notes += "建築範圍內坵塊：{} 格（{}）\n建築面積加權平均坡度：{:.2f}%（{}）\n".format(
            bld_summary["n_cells"],
            "、".join("{} {} 格".format(d["name"], d["count"]) for d in (bld_stats or []) if d["count"]),
            bld_summary.get("mean_slope", 0.0), GRADES[bld_summary.get("mean_grade", 1)][0])
    notes += "製圖日期：{}".format(datetime.date.today().isoformat())
    layout = _build(project, title, subtitle, paper, frame, result_layers["cells"].crs(), lyrs, lyrs,
                  "坡度分級面積統計" + ("（計畫範圍內）" if summary.get("use_plan") else "（方格範圍）"),
                  (("級別", 11), ("坡度範圍", 25), ("坵塊數", 11), ("面積(m²)", 19), ("比例(%)", 13)),
                  rows, notes, align_to_grid, extent_layer=result_layers["cells"], scale=scale)
    # 坵塊邊界交點數字依版面比例尺放大，使出圖可清楚判讀
    mp = layout.itemById("main_map")
    if mp is not None and result_layers.get("edges") is not None:
        from .styles import fit_edge_labels_to_scale
        fit_edge_labels_to_scale(result_layers["edges"], summary.get("cell", 25.0), mp.scale())
    return layout


def build_aspect_layout(project, result_layers, frame, aspect_stats, summary, title=ASPECT_LAYOUT_NAME,
                        subtitle="", align_to_grid=False, paper=(420.0, 297.0), scale=None):
    """坡向分析版面（第 26 條）。result_layers : dict(aspect_points=, aspect_cells=, plan=, contours=, building=)"""
    map_layers = [result_layers[k] for k in ("aspect_points", "building", "aspect_cells", "plan", "contours")
                  if result_layers.get(k) is not None]
    legend_layers = [result_layers[k] for k in ("plan", "building", "aspect_points")
                     if result_layers.get(k) is not None]
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
                  compact_legend=[result_layers.get("aspect_points")],
                  legend_node_order=None if any(d["name"] == "平坦" and d["count"] > 0 for d in aspect_stats) else [0],
                  extent_layer=result_layers.get("aspect_cells"), scale=scale)


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
    except AttributeError as ex:
        log_ignored("layout.py:381", ex)
    return exp.exportToImage(path, st) == E(QgsLayoutExporter, "ExportResult.Success", "Success")
