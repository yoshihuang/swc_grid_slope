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
    """本機網格：以完整來源字串（如 GPKG:檔案:表格）可被 GDAL 開啟且有波段"""
    if lyr.providerType() != "gdal":
        return False
    src = lyr.source()
    low = src.lower()
    if low.startswith(("http:", "https:", "/vsicurl", "wms:", "wcs:")):
        return False
    from osgeo import gdal
    try:
        ds = gdal.Open(src)
    except Exception:
        return False
    return ds is not None and ds.RasterCount > 0


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


def _cleanup(gpkg):
    for p in (gpkg, gpkg + "-wal", gpkg + "-shm"):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def save_project_to_folder(project, folder, name, include_all=True, layers=None, feedback=None):
    """
    於 folder 內輸出 <name>.qgz 與 <name>.gpkg。
    步驟：(1) 先寫網格（建立 gpkg）→ (2) 寫向量（更新模式加入圖層）
         → (3) 全部成功後才把專案圖層改指向 gpkg → (4) 存專案檔。
    失敗時刪除未完成的 gpkg。
    任何寫入失敗都不會改動目前專案。
    回傳 (qgz_path, gpkg_path, [訊息])
    """
    from osgeo import gdal

    if not os.path.isdir(folder):
        os.makedirs(folder)
    qgz_path, gpkg = output_paths(folder, name)
    # write straight into the final file (no temp + rename: on Windows QGIS/GDAL
    # may still hold the file open right after writing, which makes rename fail).
    # The project is only re-pointed after every write succeeded.
    tmp = gpkg
    for p in (gpkg, gpkg + "-wal", gpkg + "-shm"):
        if os.path.exists(p):
            os.remove(p)

    msgs = []
    used = {"layer_styles", "gpkg_contents", "gpkg_spatial_ref_sys", "gpkg_geometry_columns",
            "gpkg_extensions", "gpkg_metadata", "gpkg_tile_matrix", "gpkg_tile_matrix_set", "sqlite_sequence"}
    targets = list(project.mapLayers().values()) if include_all else list(layers or [])
    vectors = [l for l in targets if isinstance(l, QgsVectorLayer) and _is_local_vector(l)]
    rasters = [l for l in targets if isinstance(l, QgsRasterLayer) and _is_local_raster(l)]
    total = max(1, len(vectors) + len(rasters))
    done = 0
    plan = []  # (layer, new_source, provider, table, kind)
    skipped = []

    # (1) rasters first: the first one creates the file
    gpkg_types = (gdal.GDT_Byte, gdal.GDT_Int16, gdal.GDT_UInt16, gdal.GDT_Float32)
    for lyr in rasters:
        name = lyr.name()
        try:
            src = gdal.Open(lyr.source())        # keep "GPKG:file:table" etc.
            nb = src.RasterCount
            dtype = src.GetRasterBand(1).DataType
            kw = {}
            if nb == 1 and dtype not in gpkg_types:
                kw["outputType"] = gdal.GDT_Float32
                msgs.append("提示：網格「{}」資料型態 {} 不被 GeoPackage 支援，已轉為 Float32".format(
                    name, gdal.GetDataTypeName(dtype)))
            elif nb > 1 and (nb > 4 or any(src.GetRasterBand(i + 1).DataType != gdal.GDT_Byte for i in range(nb))):
                skipped.append("網格「{}」：{} 波段 {} 影像無法存入 GeoPackage（僅支援單波段或 1–4 波段 Byte），保留原檔連結".format(
                    name, nb, gdal.GetDataTypeName(dtype)))
                continue
            tbl = _safe(name, used)
            co = ["RASTER_TABLE={}".format(tbl)]
            if os.path.exists(tmp):
                co.append("APPEND_SUBDATASET=YES")
            out = gdal.Translate(tmp, src, format="GPKG", creationOptions=co, **kw)
            if out is None:
                raise RuntimeError(gdal.GetLastErrorMsg())
            out = None
            src = None
        except Exception as ex:  # noqa
            skipped.append("網格「{}」寫入失敗，保留原檔連結：{}".format(name, ex))
            continue
        plan.append((lyr, "GPKG:{}:{}".format(gpkg, tbl), "gdal", tbl, "網格"))
        done += 1
        if feedback:
            feedback(int(80.0 * done / total))

    # (2) vectors
    ctx = project.transformContext() if project else QgsCoordinateTransformContext()
    for lyr in vectors:
        name = lyr.name()
        tbl = _safe(name, used)
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.layerName = tbl
        opts.fileEncoding = "UTF-8"
        # a source field called "fid" would clash with the GeoPackage primary key
        names_lower = [f.name().lower() for f in lyr.fields()]
        if "fid" in names_lower:
            pk, i = "gpkg_fid", 1
            while pk in names_lower:
                i += 1
                pk = "gpkg_fid{}".format(i)
            opts.layerOptions = ["FID={}".format(pk)]
        # GeoPackage field names are case-insensitive: drop later duplicates
        seen, keep = set(), []
        for idx, f in enumerate(lyr.fields()):
            k = f.name().lower()
            if k in seen:
                msgs.append("提示：圖層「{}」欄位「{}」與其他欄位僅大小寫不同，未寫入".format(name, f.name()))
                continue
            seen.add(k)
            keep.append(idx)
        if len(keep) != lyr.fields().count():
            opts.attributes = keep
        opts.actionOnExistingFile = (
            E(QgsVectorFileWriter, "ActionOnExistingFile.CreateOrOverwriteLayer", "CreateOrOverwriteLayer")
            if os.path.exists(tmp) else
            E(QgsVectorFileWriter, "ActionOnExistingFile.CreateOrOverwriteFile", "CreateOrOverwriteFile"))
        try:
            res = QgsVectorFileWriter.writeAsVectorFormatV3(lyr, tmp, ctx, opts)
            ok = res[0] == E(QgsVectorFileWriter, "WriterError.NoError", "NoError")
            err = res[1]
        except Exception as ex:  # noqa
            ok, err = False, str(ex)
        if not ok:
            used.discard(tbl.lower())
            skipped.append("圖層「{}」寫入失敗，保留原檔連結：{}".format(name, err))
            continue
        plan.append((lyr, "{}|layername={}".format(gpkg, tbl), "ogr", tbl, "向量"))
        done += 1
        if feedback:
            feedback(int(80.0 * done / total))

    # layers that must not be lost (memory layers) or that were explicitly requested
    must = set(id(l) for l in (layers or []))
    lost = [l for l in vectors + rasters if l not in [p[0] for p in plan]
            and (l.providerType() == "memory" or id(l) in must)]
    if lost:
        _cleanup(gpkg)
        raise RuntimeError("下列圖層無法寫入 gpkg，未儲存任何檔案：\n" + "\n".join(skipped))

    if not plan:
        _cleanup(gpkg)
        raise RuntimeError("專案中沒有可寫入的本機圖層。\n" + "\n".join(skipped))


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
    if skipped:
        msgs = ["⚠ 下列圖層未寫入 gpkg（專案中仍連結原始檔案）："] + skipped + [""] + msgs
    return qgz_path, gpkg, msgs
