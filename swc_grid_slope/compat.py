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


def _enum(owner, scoped, *legacy):
    """Return owner.<scoped path> or first legacy attribute that exists."""
    obj = owner
    try:
        for part in scoped.split("."):
            obj = getattr(obj, part)
        return obj
    except AttributeError:
        pass
    for name in legacy:
        o = owner
        try:
            for part in name.split("."):
                o = getattr(o, part)
            return o
        except AttributeError:
            continue
    raise AttributeError(scoped)


# geometry types ----------------------------------------------------------
GEOM_POINT = _enum(Qgis, "GeometryType.Point") if hasattr(Qgis, "GeometryType") else QgsWkbTypes.PointGeometry
GEOM_LINE = _enum(Qgis, "GeometryType.Line") if hasattr(Qgis, "GeometryType") else QgsWkbTypes.LineGeometry
GEOM_POLYGON = _enum(Qgis, "GeometryType.Polygon") if hasattr(Qgis, "GeometryType") else QgsWkbTypes.PolygonGeometry


def geom_type(layer):
    return layer.geometryType()


# render units -------------------------------------------------------------
if hasattr(Qgis, "RenderUnit"):
    UNIT_MAP = Qgis.RenderUnit.MapUnits
    UNIT_MM = Qgis.RenderUnit.Millimeters
    UNIT_POINTS = Qgis.RenderUnit.Points
else:  # pragma: no cover
    UNIT_MAP = QgsUnitTypes.RenderMapUnits
    UNIT_MM = QgsUnitTypes.RenderMillimeters
    UNIT_POINTS = QgsUnitTypes.RenderPoints

# distance units
if hasattr(Qgis, "DistanceUnit"):
    DIST_METERS = Qgis.DistanceUnit.Meters
else:  # pragma: no cover
    DIST_METERS = QgsUnitTypes.DistanceMeters


def label_prop(name):
    """QgsPalLayerSettings data defined property key."""
    return _enum(QgsPalLayerSettings, "Property." + name, name)


def label_placement(name):
    if hasattr(Qgis, "LabelPlacement"):
        return getattr(Qgis.LabelPlacement, name)
    return getattr(QgsPalLayerSettings, name)  # pragma: no cover
