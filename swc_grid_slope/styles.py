# -*- coding: utf-8 -*-
"""圖層符號與標註：重現坵塊圖（方格邊交點數、中央總交點數、分級與坡度）"""

import functools
import os

from qgis.PyQt.QtCore import QSizeF
from qgis.PyQt.QtGui import QColor, QFont

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsProperty,
    QgsRendererCategory,
    QgsRuleBasedLabeling,
    QgsSingleSymbolRenderer,
    QgsTextBackgroundSettings,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
)

from .compat import E, UNIT_MAP, UNIT_MM, label_placement, label_prop, log_ignored
from .core import GRADE_COLORS, GRADES

FONT_FAMILY = "Noto Sans CJK TC"

CELL_TEXT_RATIO = 0.155  # 文字式標註字高 ≈ 0.155 L（最寬的「S=100.48%」仍可容納於方格內）

LABEL_STYLE_TEXT = 0      # 分級:3 / N=12 / S=18.84%
LABEL_STYLE_BIGGRADE = 1  # 大字級數 + 圈號交點數 + (S%)


@functools.lru_cache(maxsize=1)
def _font_family():
    from qgis.PyQt.QtGui import QFontDatabase
    try:
        fams = QFontDatabase.families()          # Qt6 (static)
    except TypeError:
        fams = QFontDatabase().families()        # Qt5
    except Exception:
        fams = []
    for cand in ("Noto Sans CJK TC", "Microsoft JhengHei", "微軟正黑體", "PingFang TC",
                 "Noto Sans TC", "Heiti TC", "Arial Unicode MS"):
        if cand in fams:
            return cand
    return QFont().defaultFamily()


def _fmt(size_mapunits, bold=False, buffer=True, family=None):
    fmt = QgsTextFormat()
    f = QFont(family or _font_family())
    f.setBold(bold)
    fmt.setFont(f)
    fmt.setSize(size_mapunits)
    fmt.setSizeUnit(UNIT_MAP)
    fmt.setColor(QColor("black"))
    if buffer:
        b = QgsTextBufferSettings()
        b.setEnabled(True)
        b.setSize(size_mapunits * 0.12)
        b.setSizeUnit(UNIT_MAP)
        b.setColor(QColor(255, 255, 255, 230))
        fmt.setBuffer(b)
    return fmt


def _allow_overlap(pal):
    try:
        from qgis.core import Qgis
        pal.placementSettings().setOverlapHandling(Qgis.LabelOverlapHandling.AllowOverlapAtNoCost)
        pal.placementSettings().setAllowDegradedPlacement(True)
    except Exception:  # QGIS < 3.26
        pal.displayAll = True
    try:
        pal.obstacleSettings().setIsObstacle(False)
    except Exception as ex:
        log_ignored("styles.py:80", ex)


def _point_pal(expression, lx, ly, fmt, xf='"cx"', yf='"cy"', rotf='"rot"', scale='"L"'):
    """標註放置於局部座標 (lx·L, ly·L)，並跟隨方格旋轉"""
    pal = QgsPalLayerSettings()
    pal.isExpression = True
    pal.fieldName = expression
    pal.setFormat(fmt)
    pal.placement = label_placement("OverPoint")
    _allow_overlap(pal)
    x = "{x} + ({lx})*{L}*cos(radians({r})) - ({ly})*{L}*sin(radians({r}))".format(x=xf, lx=lx, ly=ly, L=scale, r=rotf)
    y = "{y} + ({lx})*{L}*sin(radians({r})) + ({ly})*{L}*cos(radians({r}))".format(y=yf, lx=lx, ly=ly, L=scale, r=rotf)
    dd = pal.dataDefinedProperties()
    dd.setProperty(label_prop("PositionX"), QgsProperty.fromExpression(x))
    dd.setProperty(label_prop("PositionY"), QgsProperty.fromExpression(y))
    dd.setProperty(label_prop("Hali"), QgsProperty.fromValue("Center"))
    dd.setProperty(label_prop("Vali"), QgsProperty.fromValue("Half"))
    try:
        rot_key = label_prop("LabelRotation")
    except AttributeError:  # pragma: no cover
        rot_key = label_prop("Rotation")
    dd.setProperty(rot_key, QgsProperty.fromExpression("-{}".format(rotf)))
    pal.setDataDefinedProperties(dd)
    return pal


# --------------------------------------------------------------------------
def style_cells(layer, cell, label_style=LABEL_STYLE_TEXT, colorize=True, show_no=True):
    # renderer ---------------------------------------------------------------
    if colorize:
        cats = []
        for g, (name, rng, _lo, _hi) in GRADES.items():
            sym = QgsFillSymbol.createSimple({
                "color": QColor(GRADE_COLORS[g]).name(), "outline_color": "#000000",
                "outline_width": "0.35", "outline_width_unit": "MM", "style": "solid"})
            sym.setOpacity(0.55)
            cats.append(QgsRendererCategory(g, sym, "{}（{}）".format(name, rng)))
        layer.setRenderer(QgsCategorizedSymbolRenderer("grade", cats))
    else:
        sym = QgsFillSymbol.createSimple({"color": "0,0,0,0", "style": "no",
                                          "outline_color": "#000000", "outline_width": "0.35"})
        layer.setRenderer(QgsSingleSymbolRenderer(sym))

    # labels -----------------------------------------------------------------
    root = QgsRuleBasedLabeling.Rule(None)
    if label_style == LABEL_STYLE_TEXT:
        fmt = _fmt(cell * CELL_TEXT_RATIO)
        expr = "'分級:' || \"grade\" || '\\n' || 'N=' || \"n\" || '\\n' || 'S=' || to_string(round(\"S_pct\", 2)) || '%'"
        r = QgsRuleBasedLabeling.Rule(_point_pal(expr, 0, 0, fmt))
        r.setDescription("分級/N/S")
        root.appendChild(r)
        if show_no:
            r2 = QgsRuleBasedLabeling.Rule(_point_pal("'NO.' || \"cell_no\"", -0.33, 0.41, _fmt(cell * 0.07)))
            r2.setDescription("編號")
            root.appendChild(r2)
    else:
        big = _fmt(cell * 0.50, bold=False)
        r = QgsRuleBasedLabeling.Rule(_point_pal("\"grade\"", 0.02, 0.04, big))
        r.setDescription("坡度級數")
        root.appendChild(r)
        small = _fmt(cell * 0.10)
        rs = QgsRuleBasedLabeling.Rule(_point_pal("'(' || to_string(round(\"S_pct\", 2)) || '%)'", 0.21, -0.32, small))
        rs.setDescription("平均坡度")
        root.appendChild(rs)
        circ = _fmt(cell * 0.10, buffer=False)
        bg = QgsTextBackgroundSettings()
        bg.setEnabled(True)
        bg.setType(E(QgsTextBackgroundSettings, "ShapeType.ShapeCircle", "ShapeCircle"))
        bg.setSizeType(E(QgsTextBackgroundSettings, "SizeType.SizeBuffer", "SizeBuffer"))
        bg.setSize(QSizeF(cell * 0.03, cell * 0.03))
        bg.setSizeUnit(UNIT_MAP)
        bg.setFillColor(QColor("white"))
        bg.setStrokeColor(QColor("black"))
        bg.setStrokeWidth(0.25)
        bg.setStrokeWidthUnit(UNIT_MM)
        circ.setBackground(bg)
        rn = QgsRuleBasedLabeling.Rule(_point_pal("\"n\"", -0.31, -0.33, circ))
        rn.setDescription("交點總數")
        root.appendChild(rn)
        if show_no:
            r2 = QgsRuleBasedLabeling.Rule(_point_pal("'NO.' || \"cell_no\"", -0.30, 0.39, _fmt(cell * 0.08)))
            r2.setDescription("編號")
            root.appendChild(r2)
    layer.setLabeling(QgsRuleBasedLabeling(root))
    layer.setLabelsEnabled(True)
    layer.triggerRepaint()


EDGE_LABEL_RATIO = 0.16   # 坵塊邊界交點數字高 ≈ 0.16 L（地圖單位）


def style_edges(layer, cell, label_style=LABEL_STYLE_TEXT, size=None):
    """坵塊邊界：細線＋各邊交點數（置於邊中點，隨方格旋轉）"""
    sym = QgsLineSymbol.createSimple({"line_color": "#000000", "line_width": "0.25", "line_width_unit": "MM"})
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    size = size or cell * EDGE_LABEL_RATIO
    pal = _point_pal("\"n\"", 0, 0, _fmt(size, bold=True), xf='"mx"', yf='"my"', scale="1")
    layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    layer.setLabelsEnabled(True)
    layer.triggerRepaint()


def fit_edge_labels_to_scale(layer, cell, scale, target_mm=2.6):
    """依版面地圖比例尺調整邊界交點數字大小：出圖時字高約 target_mm，
    但限制在 0.12 L～0.24 L 之間，避免過小或超出方格。"""
    want = target_mm / 1000.0 * scale
    size = min(max(want, cell * 0.12), cell * 0.24)
    lab = layer.labeling()
    if lab is None:
        return size
    pal = lab.settings()
    fmt = pal.format()
    fmt.setSize(size)
    buf = fmt.buffer()
    buf.setSize(size * 0.12)
    fmt.setBuffer(buf)
    pal.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    layer.triggerRepaint()
    return size


def style_points(layer, cell=None):
    """交點小圓圈；大小隨坵塊邊長（地圖單位），避免密集時遮蓋圖面"""
    size, unit = ("1.2", "MM") if not cell else (str(cell * 0.06), "MapUnit")
    sym = QgsMarkerSymbol.createSimple({"name": "circle", "color": "255,255,255,0",
                                        "outline_color": "#333333", "outline_width": "0.15",
                                        "size": size, "size_unit": unit})
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    layer.triggerRepaint()


def style_frame(layer, editing=False):
    if editing:
        sym = QgsFillSymbol.createSimple({"color": "0,120,255,30", "outline_color": "#0078ff",
                                          "outline_width": "0.6", "outline_style": "dash"})
    else:
        sym = QgsFillSymbol.createSimple({"color": "0,0,0,0", "style": "no", "outline_color": "#000000",
                                          "outline_width": "0.6"})
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    layer.triggerRepaint()


def style_plan(layer):
    sym = QgsFillSymbol.createSimple({"color": "0,0,0,0", "style": "no", "outline_color": "#d62728",
                                      "outline_width": "0.7", "outline_style": "dash"})
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    layer.triggerRepaint()


def style_contours(layer):
    sym = QgsLineSymbol.createSimple({"line_color": "#7f7f7f", "line_width": "0.18"})
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    layer.triggerRepaint()


# --------------------------------------------------------------------------
# 坡向圖（第 26 條）：空心方塊箭頭指向下坡，八方位
# --------------------------------------------------------------------------
ARROW_SVG = os.path.join(os.path.dirname(__file__), "icons", "aspect_arrow.svg")


def aspect_arrow_symbol(size_map_units=None):
    from qgis.core import QgsSvgMarkerSymbolLayer
    sl = QgsSvgMarkerSymbolLayer(ARROW_SVG)
    sl.setFillColor(QColor("white"))
    sl.setStrokeColor(QColor("black"))
    sl.setStrokeWidth(0.3)
    sl.setStrokeWidthUnit(UNIT_MM)
    if size_map_units:
        sl.setSize(size_map_units)
        sl.setSizeUnit(UNIT_MAP)
    else:
        sl.setSize(5)
    sym = QgsMarkerSymbol()
    sym.changeSymbolLayer(0, sl)
    return sym


def style_aspect_points(layer, cell):
    from qgis.core import QgsRuleBasedRenderer
    root = QgsRuleBasedRenderer.Rule(None)
    arrow = aspect_arrow_symbol(cell * 0.72)
    arrow.setDataDefinedAngle(QgsProperty.fromField("aspect_dir"))
    r1 = QgsRuleBasedRenderer.Rule(arrow, 0, 0, '"aspect_dir" IS NOT NULL', "坡向")
    root.appendChild(r1)
    flat = QgsMarkerSymbol.createSimple({"name": "circle", "color": "255,255,255,255",
                                         "outline_color": "#000000", "outline_width": "0.3",
                                         "size": str(cell * 0.18), "size_unit": "MapUnit"})
    r2 = QgsRuleBasedRenderer.Rule(flat, 0, 0, '"aspect_dir" IS NULL', "平坦（無坡向）")
    root.appendChild(r2)
    layer.setRenderer(QgsRuleBasedRenderer(root))
    layer.setLabelsEnabled(False)
    layer.triggerRepaint()


def style_aspect_cells(layer):
    sym = QgsFillSymbol.createSimple({"color": "0,0,0,0", "style": "no", "outline_color": "#808080",
                                      "outline_width": "0.2", "outline_width_unit": "MM"})
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    layer.setLabelsEnabled(False)
    layer.triggerRepaint()
