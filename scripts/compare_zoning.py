import json
import requests

h = {"User-Agent": "RowanGISChatbot/1.0"}
base = "https://gis.rowancountync.gov/arcgis/rest/services"

addr = requests.get(
    f"{base}/Public/search/MapServer/0/query",
    params={
        "where": "Address = '550 MT HALL RD'",
        "outFields": "Address",
        "returnGeometry": "true",
        "f": "json",
        "outSR": 4326,
    },
    headers=h,
    timeout=30,
).json()
x, y = addr["features"][0]["geometry"]["x"], addr["features"][0]["geometry"]["y"]
print("point", x, y)

par = requests.get(
    f"{base}/Public/RowanTaxParcels/MapServer/0/query",
    params={
        "geometry": f"{x},{y}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "distance": 75,
        "units": "esriSRUnit_Foot",
        "outFields": "PIN,OWNNAME,PARCEL_ID",
        "returnGeometry": "true",
        "f": "geojson",
        "outSR": 4326,
    },
    headers=h,
    timeout=30,
).json()
for feature in par.get("features", []):
    props = feature["properties"]
    print("parcel", props.get("PIN"), props.get("PARCEL_ID"), props.get("OWNNAME"))

r = requests.get(
    f"{base}/Public/ParcelReport/MapServer/identify",
    params={
        "geometry": json.dumps({"x": x, "y": y, "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPoint",
        "sr": 4326,
        "layers": "1,10",
        "tolerance": 5,
        "mapExtent": "-81,35,-80,36",
        "imageDisplay": "800,600,96",
        "f": "json",
    },
    headers=h,
    timeout=30,
).json()
print("\nidentify at POINT:")
for item in r.get("results", []):
    attrs = item.get("attributes", {})
    print(
        item.get("layerName"),
        "ZONING=",
        attrs.get("ZONING"),
        "area=",
        attrs.get("Shape.STArea()") or attrs.get("SHAPE.STArea()"),
    )

poly = par["features"][0]["geometry"]
ring = poly["coordinates"][0]
z = requests.get(
    f"{base}/Public/ParcelReport/MapServer/1/query",
    params={
        "geometry": json.dumps({"rings": [ring], "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPolygon",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "ZONING,OBJECTID",
        "returnGeometry": "false",
        "f": "json",
        "outSR": 4326,
    },
    headers=h,
    timeout=30,
).json()
print("\ncounty zoning intersect PARCEL polygon:")
for feature in z.get("features", []):
    print(feature["attributes"])
