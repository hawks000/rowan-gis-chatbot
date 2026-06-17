import json
import requests

headers = {"User-Agent": "RowanGISChatbot/1.0"}
base = "https://gis.rowancountync.gov/arcgis/rest/services"

g = requests.get(
    f"{base}/Public/search/MapServer/0/query",
    params={
        "where": "Address LIKE '%550%MT%HALL%'",
        "outFields": "*",
        "returnGeometry": "true",
        "f": "json",
        "outSR": 4326,
        "resultRecordCount": 1,
    },
    headers=headers,
    timeout=30,
).json()
feat = g["features"][0]
x, y = feat["geometry"]["x"], feat["geometry"]["y"]
print("Address:", feat["attributes"].get("Address"))

r = requests.get(
    f"{base}/Public/ParcelReport/MapServer/identify",
    params={
        "geometry": json.dumps({"x": x, "y": y, "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPoint",
        "sr": 4326,
        "layers": "all",
        "tolerance": 5,
        "mapExtent": "-81,35,-80,36",
        "imageDisplay": "800,600,96",
        "f": "json",
    },
    headers=headers,
    timeout=30,
).json()

for item in r.get("results", []):
    print("\n---", item.get("layerName"), "layerId", item.get("layerId"))
    attrs = item.get("attributes") or {}
    for key, val in attrs.items():
        if val not in (None, "", " ", 0):
            print(f"  {key}: {val}")
