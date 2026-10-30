# -*- coding: utf-8 -*-
"""Save robustness: Float64/Int32 DEM, multiband, rasters inside gpkg, fid clashes, DXF, CSV;
three consecutive saves into the same folder.  usage: python save_robustness_test.py <repo> <out>"""
import os, sys, shutil
os.environ["QT_QPA_PLATFORM"] = "offscreen"; sys.path.insert(0, sys.argv[1]); OUT = sys.argv[2]
shutil.rmtree(OUT, ignore_errors=True); os.makedirs(OUT)
from qgis.core import *
app = QgsApplication([], True); app.initQgis()
from osgeo import gdal, ogr, osr
import numpy as np
from swc_grid_slope import save
srs = osr.SpatialReference(); srs.ImportFromEPSG(3826)
prj = QgsProject.instance(); prj.setCrs(QgsCoordinateReferenceSystem("EPSG:3826"))
def ras(name, bands, dtype, ext="tif"):
    p = os.path.join(OUT, name + "." + ext)
    ds = gdal.GetDriverByName("GTiff").Create(p, 50, 50, bands, dtype)
    ds.SetGeoTransform([250000, 1, 0, 2700050, 0, -1]); ds.SetProjection(srs.ExportToWkt())
    for b in range(bands): ds.GetRasterBand(b + 1).WriteArray((np.random.rand(50, 50) * 200).astype("float64"))
    ds = None; return p
cases = {
 "dem_f32": (1, gdal.GDT_Float32), "dem_f64": (1, gdal.GDT_Float64), "dem_i32": (1, gdal.GDT_Int32),
 "ortho_rgb": (3, gdal.GDT_Byte), "ortho_rgbn5": (5, gdal.GDT_Byte), "ms_u16_4": (4, gdal.GDT_UInt16),
}
for n, (b, t) in cases.items():
    prj.addMapLayer(QgsRasterLayer(ras(n, b, t), n))
# vector: shapefile with string fid field
p = os.path.join(OUT, "parcel_fid.shp")
d = ogr.GetDriverByName("ESRI Shapefile").CreateDataSource(p); l = d.CreateLayer("parcel_fid", srs, ogr.wkbPolygon)
l.CreateField(ogr.FieldDefn("fid", ogr.OFTString)); 
for i in range(3):
    f = ogr.Feature(l.GetLayerDefn()); f.SetField("fid", "A"); f.SetGeometry(ogr.CreateGeometryFromWkt("POLYGON((0 0,1 0,1 1,0 0))")); l.CreateFeature(f)
d = None; prj.addMapLayer(QgsVectorLayer(p, "parcel_fid", "ogr"))
# shapefile with FID int duplicates
p = os.path.join(OUT, "dupfid.shp")
d = ogr.GetDriverByName("ESRI Shapefile").CreateDataSource(p); l = d.CreateLayer("dupfid", srs, ogr.wkbPoint)
l.CreateField(ogr.FieldDefn("FID", ogr.OFTInteger))
for i in range(3):
    f = ogr.Feature(l.GetLayerDefn()); f.SetField("FID", 1); f.SetGeometry(ogr.CreateGeometryFromWkt("POINT(1 1)")); l.CreateFeature(f)
d = None; prj.addMapLayer(QgsVectorLayer(p, "dupfid", "ogr"))
# geojson mixed geometry + case dup fields
p = os.path.join(OUT, "mixed.geojson")
open(p, "w").write('{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"Name":"a","name":"b"},"geometry":{"type":"LineString","coordinates":[[0,0],[1,1]]}},{"type":"Feature","properties":{"Name":"c","name":"d"},"geometry":{"type":"MultiLineString","coordinates":[[[0,0],[1,1]]]}}]}')
prj.addMapLayer(QgsVectorLayer(p, "mixed", "ogr"))
# DXF
p = os.path.join(OUT, "cad.dxf")
d = ogr.GetDriverByName("DXF").CreateDataSource(p); l = d.CreateLayer("entities", None, ogr.wkbUnknown)
for w in ("LINESTRING(0 0 120,1 1 120)", "POINT(2 2)", "POLYGON((0 0,1 0,1 1,0 0))"):
    f = ogr.Feature(l.GetLayerDefn()); f.SetGeometry(ogr.CreateGeometryFromWkt(w)); l.CreateFeature(f)
d = None
for gt in ("LineString", "Point", "Polygon"):
    prj.addMapLayer(QgsVectorLayer(p + "|layername=entities|geometrytype=" + gt, "cad_" + gt, "ogr"))
# csv no geometry (ogr)
p = os.path.join(OUT, "tab.csv"); open(p, "w").write("a,b\n1,2\n"); prj.addMapLayer(QgsVectorLayer(p, "tab", "ogr"))
# raster inside a gpkg
gp = os.path.join(OUT, "src.gpkg"); gdal.Translate(gp, os.path.join(OUT, "dem_f32.tif"), format="GPKG", creationOptions=["RASTER_TABLE=r1"])
gdal.Translate(gp, os.path.join(OUT, "ortho_rgb.tif"), format="GPKG", creationOptions=["RASTER_TABLE=r2", "APPEND_SUBDATASET=YES"])
prj.addMapLayer(QgsRasterLayer("GPKG:%s:r2" % gp, "gpkg_r2", "gdal"))
# layer named layer_styles
m = QgsVectorLayer("Point?crs=EPSG:3826&field=x:int", "layer_styles", "memory"); prj.addMapLayer(m)
print("layers:", len(prj.mapLayers()), [l.name() + ("" if l.isValid() else "(INVALID)") for l in prj.mapLayers().values()])
for rnd in (1, 2, 3):
    try:
        r = save.save_project_to_folder(prj, os.path.join(OUT, "out"), "A")
        print("ROUND", rnd, "OK ->", os.path.basename(r[1])); print("\n".join(r[2]))
    except Exception as ex:
        print("ROUND", rnd, "FAILED:", ex)
bad = [l.name() for l in prj.mapLayers().values() if not l.isValid()]
print("invalid layers:", bad)
prj.clear(); ok = prj.read(r[0]); print("reopen", ok, "invalid:", [l.name() for l in prj.mapLayers().values() if not l.isValid()])
print("files:", sorted(os.listdir(os.path.join(OUT, "out"))))
sys.stdout.flush(); os._exit(0)
