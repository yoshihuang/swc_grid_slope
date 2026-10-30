# -*- coding: utf-8 -*-
"""將專案內所有本機資料（含記憶體暫存圖層、DEM）寫入單一 GeoPackage，
並把圖層重新指向 gpkg 後另存為新的 QGIS 專案檔 (.qgz)。"""

import os
import re

from qgis.core import (
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


def save_project_to_gpkg(project, qgz_path, include_all=True, layers=None, feedback=None):
    """
    include_all : True → 專案內所有本機向量/網格圖層皆寫入 gpkg；
                  False → 只寫入 layers 指定者
    回傳 (gpkg_path, [訊息])
    """
    from osgeo import gdal

    if not qgz_path.lower().endswith((".qgz", ".qgs")):
        qgz_path += ".qgz"
    gpkg = os.path.splitext(qgz_path)[0] + ".gpkg"
    if os.path.exists(gpkg):
        for l in project.mapLayers().values():
            p = QgsProviderRegistry.instance().decodeUri(l.providerType(), l.source()).get("path", "")
            if p and os.path.abspath(p) == os.path.abspath(gpkg):
                raise RuntimeError("目前專案仍在使用 {}，請另取新檔名。".format(gpkg))
        os.remove(gpkg)
    msgs = []
    used = set()
    targets = list(project.mapLayers().values()) if include_all else list(layers or [])
    vectors = [l for l in targets if isinstance(l, QgsVectorLayer) and _is_local_vector(l)]
    rasters = [l for l in targets if isinstance(l, QgsRasterLayer) and _is_local_raster(l)]

    ctx = project.transformContext() if project else QgsCoordinateTransformContext()
    first = True
    for i, lyr in enumerate(vectors):
        tbl = _safe(lyr.name(), used)
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.layerName = tbl
        opts.fileEncoding = "UTF-8"
        opts.actionOnExistingFile = (QgsVectorFileWriter.CreateOrOverwriteFile if first
                                     else QgsVectorFileWriter.CreateOrOverwriteLayer)
        res = QgsVectorFileWriter.writeAsVectorFormatV3(lyr, gpkg, ctx, opts)
        err = res[0]
        if err != QgsVectorFileWriter.NoError:
            msgs.append("圖層「{}」寫入失敗：{}".format(lyr.name(), res[1]))
            continue
        first = False
        # keep style, labels, renderer – just swap the data source
        name = lyr.name()
        style_doc = None
        try:
            from qgis.PyQt.QtXml import QDomDocument
            style_doc = QDomDocument()
            lyr.exportNamedStyle(style_doc)
        except Exception:
            style_doc = None
        lyr.setDataSource("{}|layername={}".format(gpkg, tbl), name, "ogr")
        if style_doc is not None:
            lyr.importNamedStyle(style_doc)
        try:  # also store the style inside the gpkg as default
            lyr.saveStyleToDatabase(tbl, "", True, "")
        except Exception:
            pass
        lyr.triggerRepaint()
        msgs.append("向量：{} → {}".format(name, tbl))
        if feedback:
            feedback(int(70.0 * (i + 1) / max(1, len(vectors))))

    for lyr in rasters:
        tbl = _safe(lyr.name(), used)
        src = QgsProviderRegistry.instance().decodeUri("gdal", lyr.source()).get("path")
        co = ["RASTER_TABLE={}".format(tbl)]
        if os.path.exists(gpkg):
            co.append("APPEND_SUBDATASET=YES")
        ds = gdal.Translate(gpkg, src, format="GPKG", creationOptions=co)
        if ds is None:
            msgs.append("網格「{}」寫入失敗".format(lyr.name()))
            continue
        ds = None
        name = lyr.name()
        lyr.setDataSource("GPKG:{}:{}".format(gpkg, tbl), name, "gdal")
        msgs.append("網格：{} → {}".format(name, tbl))

    project.writeEntry("Paths", "/Absolute", False)
    project.setFileName(qgz_path)
    ok = project.write()
    if not ok:
        msgs.append("專案寫入失敗：{}".format(project.error()))
    if feedback:
        feedback(100)
    return gpkg, msgs
