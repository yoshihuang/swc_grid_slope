# -*- coding: utf-8 -*-
"""Small helpers that keep the plugin working across QGIS 3.22 – 3.40+
(and that avoid enum names removed in newer releases)."""

from qgis.core import Qgis, QgsField, QgsWkbTypes, QgsUnitTypes, QgsPalLayerSettings

try:  # QGIS >= 3.38 prefers QMetaType
    from qgis.PyQt.QtCore import QMetaType
    _HAS_METATYPE = Qgis.QGIS_VERSION_INT >= 33800
except Exception:  # pragma: no cover
    _HAS_METATYPE = False
from qgis.PyQt.QtCore import QVariant


def make_field(name, kind, length=0, prec=0):
    """kind: 'int' | 'double' | 'string'"""
    if _HAS_METATYPE:
        t = {"int": QMetaType.Type.Int, "double": QMetaType.Type.Double,
             "string": QMetaType.Type.QString}[kind]
    else:
        t = {"int": QVariant.Int, "double": QVariant.Double,
             "string": QVariant.String}[kind]
    return QgsField(name, t, len=length, prec=prec)


_MISSING = object()


def _lookup(owner, dotted):
    """owner.<a>.<b>…；任何一段不存在時回傳 _MISSING（不使用例外）"""
    obj = owner
    for part in dotted.split("."):
        obj = getattr(obj, part, _MISSING)
        if obj is _MISSING:
            return _MISSING
    return obj


def _enum(owner, scoped, *legacy):
    """Return owner.<scoped path> or the first legacy attribute that exists."""
    for name in (scoped,) + tuple(legacy):
        obj = _lookup(owner, name)
        if obj is not _MISSING:
            return obj
    raise AttributeError(scoped)


# geometry types ----------------------------------------------------------
# QGIS >= 3.30 uses the Qgis.* scoped enums; older releases are looked up by
# name (string) so that no deprecated, unscoped enum appears in the source.
def _modern_or_legacy(modern_owner, modern, legacy_owner, legacy):
    obj = _lookup(modern_owner, modern)
    return obj if obj is not _MISSING else getattr(legacy_owner, legacy)


GEOM_POINT = _modern_or_legacy(Qgis, "GeometryType.Point", QgsWkbTypes, "PointGeometry")
GEOM_LINE = _modern_or_legacy(Qgis, "GeometryType.Line", QgsWkbTypes, "LineGeometry")
GEOM_POLYGON = _modern_or_legacy(Qgis, "GeometryType.Polygon", QgsWkbTypes, "PolygonGeometry")


def geom_type(layer):
    return layer.geometryType()


# render units -------------------------------------------------------------
UNIT_MAP = _modern_or_legacy(Qgis, "RenderUnit.MapUnits", QgsUnitTypes, "RenderMapUnits")
UNIT_MM = _modern_or_legacy(Qgis, "RenderUnit.Millimeters", QgsUnitTypes, "RenderMillimeters")
UNIT_POINTS = _modern_or_legacy(Qgis, "RenderUnit.Points", QgsUnitTypes, "RenderPoints")

# distance units
DIST_METERS = _modern_or_legacy(Qgis, "DistanceUnit.Meters", QgsUnitTypes, "DistanceMeters")


def label_prop(name):
    """QgsPalLayerSettings data defined property key."""
    return _enum(QgsPalLayerSettings, "Property." + name, name)


def label_placement(name):
    if hasattr(Qgis, "LabelPlacement"):
        return getattr(Qgis.LabelPlacement, name)
    return getattr(QgsPalLayerSettings, name)  # QGIS < 3.26


def E(owner, *paths):
    """Enum lookup that works with PyQt5 (unscoped) and PyQt6/QGIS 4 (scoped).
    E(QgsLayoutExporter, "ExportResult.Success", "Success")"""
    return _enum(owner, paths[0], *paths[1:])


# layout units
LAYOUT_MM = _modern_or_legacy(Qgis, "LayoutUnit.Millimeters", QgsUnitTypes, "LayoutMillimeters")


def wkb_geom_type(geom):
    """QgsGeometry → GEOM_POINT / GEOM_LINE / GEOM_POLYGON"""
    return geom.type()


def log_ignored(what, ex=None):
    """記錄可忽略的例外（例如舊版 QGIS 沒有某個選用 API），不中斷流程。
    取代 try/except/pass，讓問題仍可在「記錄訊息」面板查到。"""
    try:
        from qgis.core import QgsMessageLog
        QgsMessageLog.logMessage("{}{}".format(what, ": {}".format(ex) if ex is not None else ""),
                                 "坵塊法坡度分析", Qgis.MessageLevel.Info)
    except Exception as err:  # logging must never raise
        print("swc_grid_slope:", what, ex, err)
