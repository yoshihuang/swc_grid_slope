# -*- coding: utf-8 -*-
"""
坵塊法坡度分析核心計算（不依賴任何 GUI 元件，可供 Processing 演算法共用）

依據：水土保持技術規範
  第 23 條  山坡地坡度係指一坵塊土地之平均傾斜比，坡度分級一級～七級。
  第 25 條  坵塊法：每 10 m 或 25 m 畫一方格坵塊，計算各邊與等高線交點數，
            註於方格邊上，四邊總和註於方格中央，
            S = (n·π·Δh / 8L) × 100 (%)，tanθ = S/100。
  第 26 條  坡向：每一方格坵塊內垂直於等高線向下坡之方向。
"""

import math
from collections import OrderedDict

from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsSpatialIndex,
    QgsVectorLayer,
    QgsWkbTypes,
)

from .compat import make_field

# --------------------------------------------------------------------------
# 坡度分級（第 23 條）
# --------------------------------------------------------------------------
GRADES = OrderedDict([
    (1, ("一級坡", "S ≦ 5%", 0.0, 5.0)),
    (2, ("二級坡", "5% < S ≦ 15%", 5.0, 15.0)),
    (3, ("三級坡", "15% < S ≦ 30%", 15.0, 30.0)),
    (4, ("四級坡", "30% < S ≦ 40%", 30.0, 40.0)),
    (5, ("五級坡", "40% < S ≦ 55%", 40.0, 55.0)),
    (6, ("六級坡", "55% < S ≦ 100%", 55.0, 100.0)),
    (7, ("七級坡", "S > 100%", 100.0, float("inf"))),
])

GRADE_COLORS = {
    1: "#1a9850", 2: "#91cf60", 3: "#d9ef8b", 4: "#fee08b",
    5: "#fc8d59", 6: "#d73027", 7: "#7f0000",
}

ASPECT_NAMES = ["北", "東北", "東", "東南", "南", "西南", "西", "西北"]

PI_314 = 3.14


def slope_percent(n, dh, cell, pi=PI_314):
    """S(%) = n·π·Δh / (8L) × 100"""
    if cell <= 0:
        return 0.0
    return n * pi * dh / (8.0 * cell) * 100.0


def slope_angle_deg(s_pct):
    """傾斜角 θ = tan⁻¹(S/100)，以度表示"""
    return math.degrees(math.atan(s_pct / 100.0))


def slope_grade(s_pct):
    """依第 23 條回傳 1~7 級"""
    eps = 1e-9
    for g, (_, _, lo, hi) in GRADES.items():
        if s_pct <= hi + eps and (g == 1 or s_pct > lo + eps):
            return g
    return 7


def grade_name(g):
    return GRADES[g][0]


def aspect_name(azimuth):
    if azimuth is None:
        return "平坦"
    idx = int(((azimuth % 360.0) + 22.5) // 45.0) % 8
    return ASPECT_NAMES[idx]


# --------------------------------------------------------------------------
# 方格範圍（可旋轉之矩形）
# --------------------------------------------------------------------------
class Frame(object):
    """以中心點 (cx, cy)、寬 w、高 h 與旋轉角 angle（度，逆時針，0 = 正東）
    描述的矩形。局部座標 x 軸沿「寬」，y 軸沿「高」；左上角為 (-w/2, +h/2)。"""

    def __init__(self, cx=0.0, cy=0.0, w=100.0, h=100.0, angle=0.0):
        self.cx, self.cy, self.w, self.h, self.angle = float(cx), float(cy), float(w), float(h), float(angle)

    def copy(self):
        return Frame(self.cx, self.cy, self.w, self.h, self.angle)

    # -- transforms -------------------------------------------------------
    def _cs(self):
        a = math.radians(self.angle)
        return math.cos(a), math.sin(a)

    def to_map(self, lx, ly):
        c, s = self._cs()
        return QgsPointXY(self.cx + lx * c - ly * s, self.cy + lx * s + ly * c)

    def to_local(self, x, y):
        c, s = self._cs()
        dx, dy = x - self.cx, y - self.cy
        return dx * c + dy * s, -dx * s + dy * c

    def corners(self):
        """左上、右上、右下、左下（地圖座標）"""
        hw, hh = self.w / 2.0, self.h / 2.0
        return [self.to_map(-hw, hh), self.to_map(hw, hh),
                self.to_map(hw, -hh), self.to_map(-hw, -hh)]

    def geometry(self):
        pts = self.corners()
        return QgsGeometry.fromPolygonXY([pts + [pts[0]]])

    def snap_to_cell(self, cell, keep="topleft"):
        """將寬高調整為坵塊邊長整數倍，維持左上角不動"""
        tl = self.to_map(-self.w / 2.0, self.h / 2.0)
        nw = max(1, int(round(self.w / cell))) * cell
        nh = max(1, int(round(self.h / cell))) * cell
        self.w, self.h = float(nw), float(nh)
        if keep == "topleft":
            # recompute center so that the top-left corner stays put
            c, s = self._cs()
            lx, ly = nw / 2.0, -nh / 2.0
            self.cx = tl.x() + lx * c - ly * s
            self.cy = tl.y() + lx * s + ly * c
        return self

    @staticmethod
    def from_geometry(geom):
        """以多邊形之最小外接旋轉矩形建立範圍（取最接近正北向上的方向）"""
        bbox_geom = geom.orientedMinimumBoundingBox()[0]
        pts = bbox_geom.asPolygon()[0]
        c = bbox_geom.centroid().asPoint()
        l1 = math.hypot(pts[1].x() - pts[0].x(), pts[1].y() - pts[0].y())
        l2 = math.hypot(pts[2].x() - pts[1].x(), pts[2].y() - pts[1].y())
        a1 = math.degrees(math.atan2(pts[1].y() - pts[0].y(), pts[1].x() - pts[0].x()))
        a1 = ((a1 + 90.0) % 180.0) - 90.0          # (-90, 90]
        if abs(a1) <= 45.0:
            return Frame(c.x(), c.y(), l1, l2, a1)
        a2 = a1 - 90.0 if a1 > 0 else a1 + 90.0
        return Frame(c.x(), c.y(), l2, l1, a2)


# --------------------------------------------------------------------------
# 方格坵塊
# --------------------------------------------------------------------------
class Grid(object):
    """自範圍左上角開始，每 L 公尺切一方格坵塊直到填滿範圍"""

    def __init__(self, frame, cell):
        self.frame = frame
        self.cell = float(cell)
        self.cols = max(1, int(math.ceil(frame.w / self.cell - 1e-6)))
        self.rows = max(1, int(math.ceil(frame.h / self.cell - 1e-6)))

    def node(self, r, c):
        f = self.frame
        return f.to_map(-f.w / 2.0 + c * self.cell, f.h / 2.0 - r * self.cell)

    # edge keys: ('H', r, c) top edge of row r between columns c..c+1
    #            ('V', r, c) left edge of column c between rows r..r+1
    def edge_points(self, key):
        k, r, c = key
        if k == "H":
            return self.node(r, c), self.node(r, c + 1)
        return self.node(r, c), self.node(r + 1, c)

    def edges(self):
        for r in range(self.rows + 1):
            for c in range(self.cols):
                yield ("H", r, c)
        for r in range(self.rows):
            for c in range(self.cols + 1):
                yield ("V", r, c)

    @staticmethod
    def cell_edges(r, c):
        """上、右、下、左"""
        return [("H", r, c), ("V", r, c + 1), ("H", r + 1, c), ("V", r, c)]

    def cell_polygon(self, r, c):
        p = [self.node(r, c), self.node(r, c + 1), self.node(r + 1, c + 1), self.node(r + 1, c)]
        return QgsGeometry.fromPolygonXY([p + [p[0]]])

    def cell_center(self, r, c):
        f = self.frame
        return f.to_map(-f.w / 2.0 + (c + 0.5) * self.cell, f.h / 2.0 - (r + 0.5) * self.cell)


# --------------------------------------------------------------------------
# 交點計算
# --------------------------------------------------------------------------
def _extract_points(geom):
    out = []
    if geom is None or geom.isEmpty():
        return out
    gt = QgsWkbTypes.geometryType(geom.wkbType())
    if gt == QgsWkbTypes.geometryType(QgsWkbTypes.Point):
        if geom.isMultipart():
            out.extend(geom.asMultiPoint())
        else:
            out.append(geom.asPoint())
    else:
        # geometry collection or collinear overlap – keep only point parts
        for part in geom.asGeometryCollection():
            if QgsWkbTypes.geometryType(part.wkbType()) == QgsWkbTypes.geometryType(QgsWkbTypes.Point):
                out.extend(_extract_points(part))
    return out


def _key(pt, tol):
    return (round(pt.x() / tol), round(pt.y() / tol))


class ContourSet(object):
    """等高線集合（已轉換至分析座標系）"""

    def __init__(self, geoms, elevations=None):
        self.geoms = geoms
        self.elev = elevations or [None] * len(geoms)
        self.index = QgsSpatialIndex()
        for i, g in enumerate(geoms):
            f = QgsFeature(i)
            f.setGeometry(g)
            self.index.addFeature(f)

    @staticmethod
    def from_layer(layer, dest_crs, elev_field=None, extent=None, feedback=None):
        tr = None
        if layer.crs() != dest_crs:
            tr = QgsCoordinateTransform(layer.crs(), dest_crs, QgsProject.instance())
        req = None
        from qgis.core import QgsFeatureRequest
        req = QgsFeatureRequest()
        if extent is not None:
            ext = extent
            if tr is not None:
                ext = QgsCoordinateTransform(dest_crs, layer.crs(), QgsProject.instance()).transformBoundingBox(extent)
            req.setFilterRect(ext)
        geoms, elev = [], []
        idx = layer.fields().indexOf(elev_field) if elev_field else -1
        for feat in layer.getFeatures(req):
            g = QgsGeometry(feat.geometry())
            if g.isNull() or g.isEmpty():
                continue
            if tr is not None:
                g.transform(tr)
            # drop Z/M, keep 2D lines
            g = QgsGeometry(g.constGet().clone())
            try:
                g.get().dropZValue()
                g.get().dropMValue()
            except Exception:
                pass
            if g.type() == QgsWkbTypes.geometryType(QgsWkbTypes.Polygon):
                g = QgsGeometry(g.constGet().boundary())
            geoms.append(g)
            z = None
            if idx >= 0:
                try:
                    z = float(feat[idx])
                except (TypeError, ValueError):
                    z = None
            elev.append(z)
            if feedback is not None and feedback.isCanceled():
                break
        return ContourSet(geoms, elev)


def detect_interval(layer, elev_field):
    """由等高線高程欄位自動推估等高線間距 Δh"""
    idx = layer.fields().indexOf(elev_field)
    if idx < 0:
        return None
    vals = set()
    for i, v in enumerate(layer.uniqueValues(idx)):
        try:
            vals.add(round(float(v), 4))
        except (TypeError, ValueError):
            pass
    vals = sorted(vals)
    if len(vals) < 2:
        return None
    diffs = [round(b - a, 4) for a, b in zip(vals, vals[1:]) if b - a > 1e-6]
    if not diffs:
        return None
    # the most common difference
    best = max(set(diffs), key=diffs.count)
    return best


def count_intersections(grid, contours, feedback=None):
    """回傳 (edge_counts{key:n}, edge_points{key:[QgsPointXY]})"""
    tol = 1e-4
    counts, pts = {}, {}
    edges = list(grid.edges())
    total = len(edges)
    for i, key in enumerate(edges):
        a, b = grid.edge_points(key)
        eg = QgsGeometry.fromPolylineXY([a, b])
        eng = QgsGeometry.createGeometryEngine(eg.constGet())
        eng.prepareGeometry()
        seen = OrderedDict()
        for cid in contours.index.intersects(eg.boundingBox()):
            cg = contours.geoms[cid]
            if not eng.intersects(cg.constGet()):
                continue
            inter = eg.intersection(cg)
            for p in _extract_points(inter):
                seen.setdefault(_key(p, tol), (p, contours.elev[cid]))
        counts[key] = len(seen)
        pts[key] = list(seen.values())
        if feedback is not None and i % 200 == 0:
            feedback.setProgress(10 + 50.0 * i / max(1, total))
            if feedback.isCanceled():
                break
    return counts, pts


# --------------------------------------------------------------------------
# 坡向（平面擬合）
# --------------------------------------------------------------------------
def _plane_aspect(samples):
    """samples: [(x,y,z)] → (azimuth°, gradient) ; azimuth = 下坡方向，北=0 順時針"""
    if len(samples) < 3:
        return None, 0.0
    try:
        import numpy as np
    except ImportError:  # pragma: no cover
        return None, 0.0
    arr = np.array(samples, dtype=float)
    z = arr[:, 2]
    if np.ptp(z) < 1e-9:
        return None, 0.0
    x0, y0 = arr[:, 0].mean(), arr[:, 1].mean()
    A = np.c_[arr[:, 0] - x0, arr[:, 1] - y0, np.ones(len(arr))]
    try:
        (a, b, _c), *_ = np.linalg.lstsq(A, z, rcond=None)
    except Exception:
        return None, 0.0
    grad = math.hypot(a, b)
    if grad < 1e-6:
        return None, 0.0
    az = math.degrees(math.atan2(-a, -b)) % 360.0
    return az, grad


def aspect_from_contours(grid, r, c, contours, densify):
    """以坵塊（外擴半格）範圍內等高線節點擬合平面求坡向"""
    poly = grid.cell_polygon(r, c).buffer(grid.cell * 0.5, 1)
    samples = []
    for cid in contours.index.intersects(poly.boundingBox()):
        z = contours.elev[cid]
        if z is None:
            continue
        part = contours.geoms[cid].intersection(poly)
        if part.isEmpty():
            continue
        part = part.densifyByDistance(densify)
        for v in part.vertices():
            samples.append((v.x(), v.y(), z))
    return _plane_aspect(samples)


def aspect_from_dem(grid, r, c, provider, band, tr=None, n=5):
    samples = []
    f = grid.frame
    for i in range(n):
        for j in range(n):
            lx = -f.w / 2.0 + (c + (j + 0.5) / n) * grid.cell
            ly = f.h / 2.0 - (r + (i + 0.5) / n) * grid.cell
            p = f.to_map(lx, ly)
            q = tr.transform(p) if tr is not None else p
            val, ok = provider.sample(q, band)
            if ok and val == val:
                samples.append((p.x(), p.y(), val))
    return _plane_aspect(samples)


# --------------------------------------------------------------------------
# 主流程：產生圖層
# --------------------------------------------------------------------------
CELL_FIELDS = [
    ("cell_no", "int"), ("row", "int"), ("col", "int"),
    ("n_top", "int"), ("n_right", "int"), ("n_bottom", "int"), ("n_left", "int"),
    ("n", "int"), ("L", "double"), ("dh", "double"),
    ("S_pct", "double"), ("theta_deg", "double"), ("grade", "int"), ("grade_name", "string"),
    ("aspect_deg", "double"), ("aspect", "string"),
    ("cx", "double"), ("cy", "double"), ("rot", "double"),
    ("area_m2", "double"), ("plan_ratio", "double"), ("plan_area", "double"),
]

EDGE_FIELDS = [("edge_id", "string"), ("dir", "string"), ("n", "int"),
               ("mx", "double"), ("my", "double"), ("rot", "double")]

POINT_FIELDS = [("edge_id", "string"), ("elev", "double")]

FRAME_FIELDS = [("cx", "double"), ("cy", "double"), ("w", "double"),
                ("h", "double"), ("angle", "double"), ("cell", "double"),
                ("rows", "int"), ("cols", "int")]


def new_memory_layer(geom, crs, name, fields):
    uri = "{}?crs={}".format(geom, crs.authid() if crs.authid() else "")
    lyr = QgsVectorLayer(uri, name, "memory")
    if not crs.authid():
        lyr.setCrs(crs)
    lyr.dataProvider().addAttributes([make_field(n, t, 254 if t == "string" else 0) for n, t in fields])
    lyr.updateFields()
    return lyr


class AnalysisResult(object):
    def __init__(self):
        self.cells = None
        self.edges = None
        self.points = None
        self.frame_layer = None
        self.stats = []        # list of dicts per grade
        self.summary = {}


def run_analysis(frame, cell, dh, crs, contours, pi=PI_314, plan_geom=None,
                 dem=None, dem_band=1, compute_aspect=True, feedback=None,
                 names=None):
    """
    frame     : Frame
    cell      : 坵塊邊長 L (m)
    dh        : 等高線間距 Δh (m)
    crs       : 分析座標系（公尺）
    contours  : ContourSet（已在 crs 中）
    plan_geom : 計畫範圍 QgsGeometry（crs 中，可為 None）
    dem       : QgsRasterLayer（坡向用，可為 None）
    """
    names = names or {}
    grid = Grid(frame, cell)
    res = AnalysisResult()

    if feedback is not None:
        feedback.setProgress(5)
    counts, epts = count_intersections(grid, contours, feedback)

    # ---- edges & points ---------------------------------------------------
    edges = new_memory_layer("LineString", crs, names.get("edges", "坵塊邊交點數"), EDGE_FIELDS)
    pts = new_memory_layer("Point", crs, names.get("points", "等高線交點"), POINT_FIELDS)
    efeats, pfeats = [], []
    for key in grid.edges():
        a, b = grid.edge_points(key)
        eid = "{}{}_{}".format(*key)
        f = QgsFeature(edges.fields())
        f.setGeometry(QgsGeometry.fromPolylineXY([a, b]))
        f.setAttributes([eid, key[0], counts.get(key, 0), (a.x() + b.x()) / 2.0,
                         (a.y() + b.y()) / 2.0, frame.angle])
        efeats.append(f)
        for p, z in epts.get(key, []):
            pf = QgsFeature(pts.fields())
            pf.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(p)))
            pf.setAttributes([eid, z])
            pfeats.append(pf)
    edges.dataProvider().addFeatures(efeats)
    pts.dataProvider().addFeatures(pfeats)
    edges.updateExtents()
    pts.updateExtents()

    # ---- cells --------------------------------------------------------------
    cells = new_memory_layer("Polygon", crs, names.get("cells", "坵塊坡度分析"), CELL_FIELDS)
    plan_eng = None
    if plan_geom is not None and not plan_geom.isEmpty():
        plan_eng = QgsGeometry.createGeometryEngine(plan_geom.constGet())
        plan_eng.prepareGeometry()

    dem_tr, dem_prov = None, None
    if dem is not None and compute_aspect:
        dem_prov = dem.dataProvider()
        if dem.crs() != crs:
            dem_tr = QgsCoordinateTransform(crs, dem.crs(), QgsProject.instance())

    cfeats = []
    no = 0
    total = grid.rows * grid.cols
    for r in range(grid.rows):
        for c in range(grid.cols):
            no += 1
            ek = Grid.cell_edges(r, c)
            side = [counts.get(k, 0) for k in ek]
            # distinct points on the cell boundary (a contour crossing exactly
            # through a grid node must be counted only once)
            uniq = set()
            for k in ek:
                for p, _z in epts.get(k, []):
                    uniq.add(_key(p, 1e-4))
            n = len(uniq)
            s = slope_percent(n, dh, cell, pi)
            th = slope_angle_deg(s)
            g = slope_grade(s)
            poly = grid.cell_polygon(r, c)
            center = grid.cell_center(r, c)
            area = poly.area()
            ratio, parea = 1.0, area
            if plan_eng is not None:
                if plan_eng.contains(poly.constGet()):
                    ratio, parea = 1.0, area
                elif not plan_eng.intersects(poly.constGet()):
                    ratio, parea = 0.0, 0.0
                else:
                    parea = poly.intersection(plan_geom).area()
                    ratio = parea / area if area > 0 else 0.0
            az = None
            if compute_aspect:
                if dem_prov is not None:
                    az, _ = aspect_from_dem(grid, r, c, dem_prov, dem_band, dem_tr)
                else:
                    az, _ = aspect_from_contours(grid, r, c, contours, cell / 5.0)
            f = QgsFeature(cells.fields())
            f.setGeometry(poly)
            f.setAttributes([
                no, r + 1, c + 1, side[0], side[1], side[2], side[3], n,
                float(cell), float(dh), round(s, 2), round(th, 2), g, grade_name(g),
                round(az, 1) if az is not None else None,
                aspect_name(az) if compute_aspect else None,
                center.x(), center.y(), frame.angle,
                round(area, 2), round(ratio, 4), round(parea, 2),
            ])
            cfeats.append(f)
            if feedback is not None and no % 50 == 0:
                feedback.setProgress(60 + 35.0 * no / max(1, total))
                if feedback.isCanceled():
                    break
    cells.dataProvider().addFeatures(cfeats)
    cells.updateExtents()

    # ---- frame --------------------------------------------------------------
    fl = new_memory_layer("Polygon", crs, names.get("frame", "坵塊方格範圍"), FRAME_FIELDS)
    gf = QgsFeature(fl.fields())
    gframe = Frame(frame.cx, frame.cy, grid.cols * cell, grid.rows * cell, frame.angle)
    # the grid starts from the top-left corner – realign the frame outline
    tl = frame.to_map(-frame.w / 2.0, frame.h / 2.0)
    ca, sa = math.cos(math.radians(frame.angle)), math.sin(math.radians(frame.angle))
    gframe.cx = tl.x() + (gframe.w / 2.0) * ca + (gframe.h / 2.0) * sa
    gframe.cy = tl.y() + (gframe.w / 2.0) * sa - (gframe.h / 2.0) * ca
    gf.setGeometry(gframe.geometry())
    gf.setAttributes([gframe.cx, gframe.cy, gframe.w, gframe.h, gframe.angle, float(cell), grid.rows, grid.cols])
    fl.dataProvider().addFeatures([gf])
    fl.updateExtents()

    res.cells, res.edges, res.points, res.frame_layer = cells, edges, pts, fl
    res.stats, res.summary = statistics(cfeats, plan_geom is not None)
    res.summary.update({"rows": grid.rows, "cols": grid.cols, "cell": cell, "dh": dh,
                        "pi": pi, "n_points": len(pfeats)})
    if feedback is not None:
        feedback.setProgress(100)
    return res


def statistics(cell_features, use_plan):
    """依坡度分級統計面積；有計畫範圍時以範圍內面積計算"""
    acc = OrderedDict((g, {"grade": g, "name": GRADES[g][0], "range": GRADES[g][1],
                           "count": 0, "area": 0.0}) for g in GRADES)
    tot_area, wsum = 0.0, 0.0
    for f in cell_features:
        g = f["grade"]
        a = f["plan_area"] if use_plan else f["area_m2"]
        if a is None or a <= 0:
            continue
        acc[g]["count"] += 1
        acc[g]["area"] += a
        tot_area += a
        wsum += a * f["S_pct"]
    rows = []
    for g, d in acc.items():
        d["pct"] = (d["area"] / tot_area * 100.0) if tot_area > 0 else 0.0
        rows.append(d)
    summary = {"total_area": tot_area,
               "mean_slope": (wsum / tot_area) if tot_area > 0 else 0.0,
               "use_plan": use_plan}
    summary["mean_grade"] = slope_grade(summary["mean_slope"])
    return rows, summary


# --------------------------------------------------------------------------
# DEM → 等高線
# --------------------------------------------------------------------------
def contours_from_dem(dem_layer, band, interval, dest_crs, extent=None, feedback=None):
    """以 GDAL 由 DEM 產生等高線（記憶體圖層，欄位 ELEV），回傳 QgsVectorLayer"""
    from osgeo import gdal, ogr, osr
    import os
    import tempfile
    import uuid

    src = dem_layer.source()
    ds = gdal.Open(src)
    if ds is None:
        raise RuntimeError("無法以 GDAL 開啟 DEM：{}".format(src))
    tmp_tif = None
    if extent is not None:
        ext = extent
        if dem_layer.crs() != dest_crs:
            ext = QgsCoordinateTransform(dest_crs, dem_layer.crs(), QgsProject.instance()).transformBoundingBox(extent)
        gt = ds.GetGeoTransform()
        pad = abs(gt[1]) * 3
        tmp_tif = "/vsimem/swc_clip_{}.tif".format(uuid.uuid4().hex)
        ds = gdal.Translate(tmp_tif, ds, projWin=[ext.xMinimum() - pad, ext.yMaximum() + pad,
                                                  ext.xMaximum() + pad, ext.yMinimum() - pad])
        if ds is None:
            raise RuntimeError("DEM 裁切失敗，請確認範圍與 DEM 重疊。")
    b = ds.GetRasterBand(band)
    nodata = b.GetNoDataValue()

    out_path = os.path.join(tempfile.gettempdir(), "swc_contours_{}.gpkg".format(uuid.uuid4().hex[:8]))
    drv = ogr.GetDriverByName("GPKG")
    ods = drv.CreateDataSource(out_path)
    srs = osr.SpatialReference()
    wkt = ds.GetProjection()
    if wkt:
        srs.ImportFromWkt(wkt)
    olyr = ods.CreateLayer("contours", srs, ogr.wkbLineString)
    olyr.CreateField(ogr.FieldDefn("ID", ogr.OFTInteger))
    olyr.CreateField(ogr.FieldDefn("ELEV", ogr.OFTReal))
    opts = ["LEVEL_INTERVAL={}".format(interval), "ID_FIELD=0", "ELEV_FIELD=1"]
    if nodata is not None:
        opts.append("NODATA={}".format(nodata))
    gdal.ContourGenerateEx(b, olyr, options=opts)
    ods = None
    ds = None
    if tmp_tif:
        gdal.Unlink(tmp_tif)

    lyr = QgsVectorLayer(out_path + "|layername=contours", "DEM 等高線 (Δh={}m)".format(interval), "ogr")
    if not lyr.isValid():
        raise RuntimeError("等高線產生失敗")
    if not lyr.crs().isValid():
        lyr.setCrs(dem_layer.crs())
    # copy into memory so the temp file can be dropped later
    mem = new_memory_layer("LineString", lyr.crs(), lyr.name(), [("ELEV", "double")])
    feats = []
    for f in lyr.getFeatures():
        nf = QgsFeature(mem.fields())
        nf.setGeometry(f.geometry())
        nf.setAttributes([f["ELEV"]])
        feats.append(nf)
    mem.dataProvider().addFeatures(feats)
    mem.updateExtents()
    del lyr
    try:
        os.remove(out_path)
    except OSError:
        pass
    return mem
