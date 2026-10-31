# -*- coding: utf-8 -*-
"""Processing 工具箱：坵塊法坡度分析（可批次 / 模型建構器使用）"""

import math
import os

from qgis.PyQt.QtGui import QIcon
from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsFeatureSink,
    QgsGeometry,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsProcessingProvider,
)

from . import core
from .compat import E

if hasattr(Qgis, "ProcessingSourceType"):
    _ST_TypeVectorPolygon = Qgis.ProcessingSourceType.VectorPolygon
    _ST_TypeVectorLine = Qgis.ProcessingSourceType.VectorLine
    _ST_TypeVectorPoint = Qgis.ProcessingSourceType.VectorPoint
else:  # QGIS < 3.36
    _ST_TypeVectorPolygon = QgsProcessing.TypeVectorPolygon
    _ST_TypeVectorLine = QgsProcessing.TypeVectorLine
    _ST_TypeVectorPoint = QgsProcessing.TypeVectorPoint
if hasattr(Qgis, "ProcessingNumberParameterType"):
    _NUM_INT = Qgis.ProcessingNumberParameterType.Integer
    _NUM_DBL = Qgis.ProcessingNumberParameterType.Double
else:  # QGIS < 3.36
    _NUM_INT = QgsProcessingParameterNumber.Integer
    _NUM_DBL = QgsProcessingParameterNumber.Double

ICON = os.path.join(os.path.dirname(__file__), "icons", "icon.svg")


class GridSlopeAlgorithm(QgsProcessingAlgorithm):
    CONTOURS, ELEV, DEM, BAND, DH, CELL, PI, FRAME, PLAN, ASPECT = (
        "CONTOURS", "ELEV_FIELD", "DEM", "BAND", "DH", "CELL", "PI", "FRAME", "PLAN", "ASPECT")
    OUT_CELLS, OUT_EDGES, OUT_POINTS = "OUT_CELLS", "OUT_EDGES", "OUT_POINTS"

    def name(self):
        return "gridslope"

    def displayName(self):
        return "坵塊法坡度分析（Grid method slope analysis）"

    def group(self):
        return ""

    def groupId(self):
        return ""

    def icon(self):
        return QIcon(ICON)

    def shortHelpString(self):
        return ("依水土保持技術規範第 25 條坵塊法計算方格平均坡度：S = nπΔh/(8L)×100。\n"
                "方格範圍取「範圍圖層」第一個圖徵之最小外接旋轉矩形，自左上角起每 L 公尺切一格。\n"
                "地形可輸入等高線或 DEM（DEM 會先以 Δh 產生等高線）。\n"
                "坡度分級依第 23 條（一～七級坡）；坡向依第 26 條。")

    def createInstance(self):
        return GridSlopeAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.FRAME, "方格範圍（多邊形，取最小外接旋轉矩形）", [_ST_TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.CONTOURS, "等高線（與 DEM 擇一）", [_ST_TypeVectorLine], optional=True))
        self.addParameter(QgsProcessingParameterField(
            self.ELEV, "高程欄位（坡向用，選填）", parentLayerParameterName=self.CONTOURS, optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(self.DEM, "DEM（與等高線擇一）", optional=True))
        self.addParameter(QgsProcessingParameterNumber(self.BAND, "DEM 波段", _NUM_INT, 1, minValue=1))
        self.addParameter(QgsProcessingParameterNumber(self.DH, "等高線間距 Δh (m)", _NUM_DBL, 1.0, minValue=0.01))
        self.addParameter(QgsProcessingParameterEnum(self.CELL, "坵塊邊長 L", ["10 公尺", "25 公尺"], defaultValue=1))
        self.addParameter(QgsProcessingParameterEnum(self.PI, "圓周率", ["3.14（規範）", "math.pi"], defaultValue=0))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.PLAN, "計畫範圍（選填，用於面積統計）", [_ST_TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterBoolean(self.ASPECT, "計算坡向", True))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUT_CELLS, "坵塊坡度分析", _ST_TypeVectorPolygon))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUT_EDGES, "坵塊邊界", _ST_TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUT_POINTS, "等高線交點", _ST_TypeVectorPoint))

    def processAlgorithm(self, parameters, context, feedback):
        frame_src = self.parameterAsSource(parameters, self.FRAME, context)
        crs = frame_src.sourceCrs()
        if crs.isGeographic():
            raise QgsProcessingException("範圍圖層須為公尺制投影座標（如 EPSG:3826）")
        geoms = [f.geometry() for f in frame_src.getFeatures()]
        if not geoms:
            raise QgsProcessingException("範圍圖層沒有圖徵")
        frame = core.Frame.from_geometry(QgsGeometry.unaryUnion(geoms))
        cell = 10.0 if self.parameterAsEnum(parameters, self.CELL, context) == 0 else 25.0
        frame.w = math.ceil(frame.w / cell - 1e-6) * cell
        frame.h = math.ceil(frame.h / cell - 1e-6) * cell
        dh = self.parameterAsDouble(parameters, self.DH, context)
        pi = core.PI_314 if self.parameterAsEnum(parameters, self.PI, context) == 0 else math.pi
        ext = frame.geometry().buffer(cell, 1).boundingBox()

        contour_src = self.parameterAsVectorLayer(parameters, self.CONTOURS, context)
        dem = self.parameterAsRasterLayer(parameters, self.DEM, context)
        band = self.parameterAsInt(parameters, self.BAND, context)
        elev = self.parameterAsString(parameters, self.ELEV, context) or None
        if contour_src is not None:
            cs = core.ContourSet.from_layer(contour_src, crs, elev, ext, feedback)
            dem = None
        elif dem is not None:
            feedback.pushInfo("由 DEM 產生等高線 Δh = {:g} m".format(dh))
            gen = core.contours_from_dem(dem, band, dh, crs, ext)
            cs = core.ContourSet.from_layer(gen, crs, "ELEV", ext, feedback)
        else:
            raise QgsProcessingException("請輸入等高線或 DEM")
        if not cs.geoms:
            raise QgsProcessingException("方格範圍內沒有等高線")

        plan_geom = None
        plan_src = self.parameterAsSource(parameters, self.PLAN, context)
        if plan_src is not None:
            pg = [f.geometry() for f in plan_src.getFeatures()]
            if pg:
                plan_geom = QgsGeometry.unaryUnion(pg)
                if plan_src.sourceCrs() != crs:
                    plan_geom.transform(QgsCoordinateTransform(plan_src.sourceCrs(), crs, context.transformContext()))

        res = core.run_analysis(frame, cell, dh, crs, cs, pi=pi, plan_geom=plan_geom, dem=dem, dem_band=band,
                                compute_aspect=self.parameterAsBoolean(parameters, self.ASPECT, context),
                                feedback=feedback)
        out = {}
        for key, lyr in ((self.OUT_CELLS, res.cells), (self.OUT_EDGES, res.edges), (self.OUT_POINTS, res.points)):
            sink, dest = self.parameterAsSink(parameters, key, context, lyr.fields(), lyr.wkbType(), crs)
            for f in lyr.getFeatures():
                sink.addFeature(f, E(QgsFeatureSink, "Flag.FastInsert", "FastInsert"))
            out[key] = dest
        for d in res.stats:
            feedback.pushInfo("{}（{}）：{} 格，{:.2f} m²，{:.2f}%".format(d["name"], d["range"], d["count"], d["area"], d["pct"]))
        feedback.pushInfo("面積加權平均坡度 {:.2f}%".format(res.summary["mean_slope"]))
        return out


class GridSlopeProvider(QgsProcessingProvider):
    def id(self):
        return "swcgridslope"

    def name(self):
        return "坵塊法坡度分析"

    def icon(self):
        return QIcon(ICON)

    def loadAlgorithms(self):
        self.addAlgorithm(GridSlopeAlgorithm())
