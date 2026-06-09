import json
import urllib.parse
import urllib.request


def q(where):
    url = (
        "https://gis.rowancountync.gov/arcgis/rest/services/Public/RowanTaxParcels/MapServer/0/query?"
        + urllib.parse.urlencode(
            {
                "where": where,
                "outFields": "PIN,OWNNAME,PROP_ADDRESS",
                "returnGeometry": "false",
                "f": "json",
                "resultRecordCount": 3,
            }
        )
    )
    req = urllib.request.Request(url, headers={"User-Agent": "test"})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return [(f["attributes"].get("OWNNAME"), f["attributes"].get("PROP_ADDRESS")) for f in data.get("features", [])]


print("550 hall", q("UPPER(PROP_ADDRESS) LIKE '%550%' AND UPPER(PROP_ADDRESS) LIKE '%HALL%'"))
print("hawks", q("UPPER(OWNNAME) LIKE '%HAWKS%'"))
print("earl hawks", q("UPPER(OWNNAME) LIKE '%EARL%' AND UPPER(OWNNAME) LIKE '%HAWKS%'"))
