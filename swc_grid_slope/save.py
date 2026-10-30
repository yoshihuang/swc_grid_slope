# -*- coding: utf-8 -*-
"""將專案內所有本機資料（含記憶體暫存圖層、DEM）寫入單一 GeoPackage，
並把圖層重新指向 gpkg 後另存為新的 QGIS 專案檔 (.qgz)。"""

import os
import re

from .compat import E

from qgis.core import (
    QgsProject,
    QgsCoordinateTransformContext,
    QgsProviderRegistry,
    QgsRasterLayer,
    QgsVectorFileWriter,
    QgsVectorLayer,
)


def _safe(name, used):
    base = re.sub(r"[^\w一-鿿]+", "_", name, flags=re.UNICODE).strip("_") or "layer"
    base = base[:60]
    cand, i = base, 1
    while cand.lower() in used:
        i += 1
        cand = "{}_{}".format(base, i)
    used.add(cand.lower())
    return cand


def _is_local_vector(lyr):
    if lyr.providerType() == "memory":
        return True
    if lyr.providerType() != "ogr":
        return False
    path = QgsProviderRegistry.instance().decodeUri("ogr", lyr.source()).get("path", "")
    return bool(path) and os.path.exists(path)


def _is_local_raster(lyr):
    if lyr.providerType() != "gdal":
        return False
    path = QgsProviderRegistry.instance().decodeUri("gdal", lyr.source()).get("path", "")
    return bool(path) and os.path.exists(path)


def _source_path(lyr):
    try:
        return QgsProviderRegistry.instance().decodeUri(lyr.providerType(), lyr.source()).get("path", "") or ""
    except Exception:
        return ""


def output_paths(folder, name):
    """回傳 (qgz, gpkg)；檔名已存在且正被目前專案使用時自動加序號"""
    base = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "坵塊法坡度分析"
    in_use = {os.path.normcase(os.path.abspath(p)) for p in
              (_source_path(l) for l in QgsProject.instance().mapLayers().values()) if p}
    cand, i = base, 1
    while True:
        qgz = os.path.join(folder, cand + ".qgz")
        gpkg = os.path.join(folder, cand + ".gpkg")
        if os.path.normcase(os.path.abspath(gpkg)) not in in_use:
            return qgz, gpkg
        i += 1
        cand = "{}_{}".format(base, i)


def save_project_to_folder(project, folder, name, include_all=True, layers=None, feedback=None):
    """
    於 folder 內輸出 <name>.qgz 與 <name>.gpkg。
    步驟：(1) 先寫網格（建立 gpkg）→ (2) 寫向量（更新模式加入圖層）
         → (3) 全部成功後才把專案圖層改指向 gpkg → (4) 存專案檔。
    任何寫入失敗都不會改動目前專案。
    回傳 (qgz_path, gpkg_path, [訊息])
    """
    from osgeo import gdal

    if not os.path.isdir(folder):
        os.makedirs(folder)
    qgz_path, gpkg = output_paths(folder, name)
    # temporary file (must keep the .gpkg extension for the GPKG driver)
    tmp = os.path.join(folder, "~writing_" + os.path.basename(gpkg))
    for p in (tmp, gpkg):
        if os.path.exists(p):
            os.remove(p)

    msgs = []
    used = set()
    targets = list(project.mapLayers().values()) if include_all else list(layers or [])
    vectors = [l for l in targets if isinstance(l, QgsVectorLayer) and _is_local_vector(l)]
    rasters = [l for l in targets if isinstance(l, QgsRasterLayer) and _is_local_raster(l)]
    total = max(1, len(vectors) + len(rasters))
    done = 0
    plan = []  # (layer, new_source, provider, table, kind)

    # (1) rasters first: the first one creates the file, no "append" needed
    for lyr in rasters:
        tbl = _safe(lyr.name(), used)
        src = _source_path(lyr)
        co = ["RASTER_TABLE={}".format(tbl)]
        if os.path.exists(tmp):
            co.append("APPEND_SUBDATASET=YES")
        ds = gdal.Translate(tmp, src, format="GPKG", creationOptions=co)
        if ds is None:
            raise RuntimeError("網格「{}」寫入 gpkg 失敗：{}".format(lyr.name(), gdal.GetLastErrorMsg()))
        ds = None
        plan.append((lyr, "GPKG:{}:{}".format(gpkg, tbl), "gdal", tbl, "網格"))
        done += 1
        if feedback:
            feedback(int(80.0 * done / total))

    # (2) vectors
    ctx = project.transformContext() if project else QgsCoordinateTransformContext()
    for lyr in vectors:
        tbl = _safe(lyr.name(), used)
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.layerName = tbl
        opts.fileEncoding = "UTF-8"
        opts.actionOnExistingFile = (
            E(QgsVectorFileWriter, "ActionOnExistingFile.CreateOrOverwriteLayer", "CreateOrOverwriteLayer")
            if os.path.exists(tmp) else
            E(QgsVectorFileWriter, "ActionOnExistingFile.CreateOrOverwriteFile", "CreateOrOverwriteFile"))
        res = QgsVectorFileWriter.writeAsVectorFormatV3(lyr, tmp, ctx, opts)
        if res[0] != E(QgsVectorFileWriter, "WriterError.NoError", "NoError"):
            raise RuntimeError("圖層「{}」寫入 gpkg 失敗：{}".format(lyr.name(), res[1]))
        plan.append((lyr, "{}|layername={}".format(gpkg, tbl), "ogr", tbl, "向量"))
        done += 1
        if feedback:
            feedback(int(80.0 * done / total))

    if not plan:
        raise RuntimeError("專案中沒有可寫入的本機圖層。")

    # the file is complete – give it its final name
    os.replace(tmp, gpkg)

    # (3) repoint layers, keeping their style / labels
    from qgis.PyQt.QtXml import QDomDocument
    for lyr, source, provider, tbl, kind in plan:
        name = lyr.name()
        doc = QDomDocument()
        try:
            lyr.exportNamedStyle(doc)
        except Exception:
            doc = None
        lyr.setDataSource(source, name, provider)
        if doc is not None:
            lyr.importNamedStyle(doc)
        if provider == "ogr":
            try:  # also store the style inside the gpkg as default
                lyr.saveStyleToDatabase(tbl, "", True, "")
            except Exception:
                pass
        lyr.triggerRepaint()
        msgs.append("{}：{} → {}".format(kind, name, tbl))

    # (4) project file
    project.writeEntry("Paths", "/Absolute", False)
    project.setFileName(qgz_path)
    if not project.write():
        raise RuntimeError("專案檔寫入失敗：{}".format(project.error()))
    if feedback:
        feedback(100)
    return qgz_path, gpkg, msgs
