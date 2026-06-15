/* global require, window */

let mapView = null;
let resultsLayer = null;
let geocodeLayer = null;
let mapReadyPromise = null;
let activeZoomId = 0;
let lastDrawFeatures = [];
let lastGeocode = null;
let lastMapTarget = null;
let mapClickHandler = null;

const DEFAULT_SUGGESTIONS = [
    "Who owns 550 MT HALL RD",
    "Find Earl Hawks owning property",
    "PIN 5733-04-51-7482",
    "How many parcels on Woodleaf",
    "How many houses on Main Street",
    "How many addresses in subdivision Oak Hills",
];

function getConfig() {
    return window.GIS_CHATBOT_CONFIG || {};
}

function setMapStatus(message, isError = false) {
    const status = document.getElementById("map-status");
    if (!status) {
        return;
    }
    status.textContent = message;
    status.classList.toggle("error", isError);
    status.classList.toggle("hidden", !message);
}

function waitForArcGIS() {
    return new Promise((resolve, reject) => {
        const start = Date.now();
        const check = () => {
            if (typeof window.require === "function") {
                window.require(["esri/config"], (esriConfig) => {
                    esriConfig.request.useIdentityManager = false;
                    resolve();
                }, reject);
                return;
            }
            if (Date.now() - start > 30000) {
                reject(new Error("ArcGIS API failed to load"));
                return;
            }
            window.setTimeout(check, 100);
        };
        check();
    });
}

function loadModules(modules) {
    return new Promise((resolve, reject) => {
        window.require(modules, (...loaded) => resolve(loaded), reject);
    });
}

function walkCoordinates(coords, visit) {
    if (!coords) {
        return;
    }
    if (typeof coords[0] === "number") {
        visit(coords[0], coords[1]);
        return;
    }
    coords.forEach((part) => walkCoordinates(part, visit));
}

function bboxFromFeatures(features, geocode) {
    let xmin = Infinity;
    let ymin = Infinity;
    let xmax = -Infinity;
    let ymax = -Infinity;

    const addPoint = (x, y) => {
        if (!Number.isFinite(x) || !Number.isFinite(y)) {
            return;
        }
        xmin = Math.min(xmin, x);
        ymin = Math.min(ymin, y);
        xmax = Math.max(xmax, x);
        ymax = Math.max(ymax, y);
    };

    (features || []).forEach((feature) => {
        const props = feature.properties || {};
        if (props._lookup === "address_point") {
            return;
        }
        walkCoordinates(feature.geometry && feature.geometry.coordinates, addPoint);
    });

    if (geocode && geocode.location) {
        addPoint(geocode.location.x, geocode.location.y);
    }

    if (geocode && geocode.extent) {
        addPoint(geocode.extent.xmin, geocode.extent.ymin);
        addPoint(geocode.extent.xmax, geocode.extent.ymax);
    }

    if (!Number.isFinite(xmin)) {
        return null;
    }

    return { xmin, ymin, xmax, ymax };
}

function computeZoomTarget(geojson, geocode) {
    if (geocode && geocode.location) {
        const { x, y } = geocode.location;
        if (Number.isFinite(x) && Number.isFinite(y)) {
            return { longitude: x, latitude: y, span: null, source: "geocode" };
        }
    }

    const features = (geojson && geojson.features) || [];
    for (const feature of features) {
        const props = feature.properties || {};
        const coords = feature.geometry && feature.geometry.coordinates;
        if (props._lookup === "address_point" && coords && coords.length >= 2) {
            return {
                longitude: coords[0],
                latitude: coords[1],
                span: null,
                source: "address_point",
            };
        }
    }

    const parcelFeatures = features.filter((feature) => {
        const props = feature.properties || {};
        const type = feature.geometry && feature.geometry.type;
        return props._lookup !== "address_point"
            && (type === "Polygon" || type === "MultiPolygon");
    });

    const bbox = bboxFromFeatures(parcelFeatures.length ? parcelFeatures : features, null);
    if (!bbox) {
        return null;
    }

    const span = Math.max(bbox.xmax - bbox.xmin, bbox.ymax - bbox.ymin);
    return {
        longitude: (bbox.xmin + bbox.xmax) / 2,
        latitude: (bbox.ymin + bbox.ymax) / 2,
        span,
        source: "bbox",
    };
}

function scaleForSpan(span) {
    if (!span || !Number.isFinite(span)) {
        return 2400;
    }
    if (span > 0.05) {
        return 50000;
    }
    if (span > 0.01) {
        return 12000;
    }
    if (span > 0.003) {
        return 6000;
    }
    if (span > 0.001) {
        return 3000;
    }
    return 1800;
}

async function waitForResultLayers() {
    if (!mapView || !resultsLayer) {
        return;
    }

    await mapView.when();
    const waits = [];
    if (resultsLayer) {
        waits.push(mapView.whenLayerView(resultsLayer).catch(() => null));
    }
    if (geocodeLayer) {
        waits.push(mapView.whenLayerView(geocodeLayer).catch(() => null));
    }
    await Promise.all(waits);
}

function collectResultGraphics() {
    const graphics = [];
    if (resultsLayer && resultsLayer.graphics) {
        graphics.push(...resultsLayer.graphics.toArray());
    }
    if (geocodeLayer && geocodeLayer.graphics) {
        graphics.push(...geocodeLayer.graphics.toArray());
    }
    return graphics;
}

async function waitForBasemap() {
    if (!mapView || !mapView.map) {
        return;
    }
    await mapView.when();
    const waits = [];
    for (const layer of mapView.map.allLayers.items || []) {
        if (layer.type === "map-image" || layer.type === "tile") {
            waits.push(layer.load().catch(() => null));
            waits.push(mapView.whenLayerView(layer).catch(() => null));
        }
    }
    await Promise.all(waits);
}

async function projectGeometriesToView(geometries) {
    if (!geometries.length) {
        return [];
    }

    const [projection] = await loadModules(["esri/geometry/projection"]);
    await projection.load();
    const viewSR = mapView.spatialReference;

    return geometries
        .map((geometry) => {
            if (!geometry) {
                return null;
            }
            if (geometry.spatialReference && geometry.spatialReference.wkid === viewSR.wkid) {
                return geometry;
            }
            return projection.project(geometry, viewSR);
        })
        .filter(Boolean);
}

function zoomLevelForScale(scale) {
    if (!scale || scale > 12000) {
        return 14;
    }
    if (scale > 6000) {
        return 15;
    }
    if (scale > 3000) {
        return 16;
    }
    return 17;
}

async function zoomToGeographicCenter(mapTarget, zoomId) {
    if (!mapTarget || !mapTarget.center || zoomId !== activeZoomId || !mapView) {
        return false;
    }

    const zoom = zoomLevelForScale(mapTarget.scale);
    try {
        await mapView.goTo(
            {
                center: [mapTarget.center.x, mapTarget.center.y],
                zoom,
            },
            { duration: 700, easing: "ease-in-out" },
        );
        return true;
    } catch (error) {
        if (error && error.name !== "AbortError") {
            console.warn("Geographic center/zoom fallback failed:", error);
        }
    }
    return false;
}

async function zoomToMapTarget(mapTarget, zoomId) {
    if (!mapTarget || zoomId !== activeZoomId || !mapView) {
        return false;
    }

    await waitForBasemap();
    mapView.resize();

    const [Extent, Point, projection] = await loadModules([
        "esri/geometry/Extent",
        "esri/geometry/Point",
        "esri/geometry/projection",
    ]);
    await projection.load();

    const viewSR = mapView.spatialReference;
    const padding = { top: 60, bottom: 60, left: 60, right: 60 };

    if (mapTarget.extent) {
        const extent = new Extent({
            xmin: mapTarget.extent.xmin,
            ymin: mapTarget.extent.ymin,
            xmax: mapTarget.extent.xmax,
            ymax: mapTarget.extent.ymax,
            spatialReference: { wkid: mapTarget.extent.wkid || 4326 },
        });
        const projectedExtent = projection.project(extent, viewSR);
        if (projectedExtent) {
            try {
                await mapView.goTo(projectedExtent.expand(1.3), { duration: 700, easing: "ease-in-out" });
                return true;
            } catch (error) {
                if (error && error.name !== "AbortError") {
                    console.warn("Projected extent zoom failed:", error);
                }
            }
        }
    }

    if (mapTarget.center) {
        const point = new Point({
            longitude: mapTarget.center.x,
            latitude: mapTarget.center.y,
            spatialReference: { wkid: 4326 },
        });
        const projectedPoint = projection.project(point, viewSR);
        const scale = mapTarget.scale || 2400;
        if (projectedPoint) {
            try {
                await mapView.goTo({ target: projectedPoint, scale }, { duration: 700, easing: "ease-in-out" });
                return true;
            } catch (error) {
                if (error && error.name !== "AbortError") {
                    console.warn("Projected center zoom failed:", error);
                }
            }
        }
    }

    return zoomToGeographicCenter(mapTarget, zoomId);
}

async function zoomToGraphics(graphics, zoomId) {
    if (!graphics.length || zoomId !== activeZoomId) {
        return false;
    }

    await waitForBasemap();
    mapView.resize();

    const padding = { top: 60, bottom: 60, left: 60, right: 60 };
    const geometries = graphics.map((graphic) => graphic.geometry).filter(Boolean);
    const projectedGeometries = await projectGeometriesToView(geometries);

    try {
        if (projectedGeometries.length === 1 && projectedGeometries[0].type === "point") {
            await mapView.goTo({ target: projectedGeometries[0], scale: 2400 }, { duration: 700 });
            return true;
        }
        if (projectedGeometries.length) {
            await mapView.goTo({ target: projectedGeometries, padding }, { duration: 700, easing: "ease-in-out" });
            return true;
        }
    } catch (error) {
        if (error && error.name === "AbortError") {
            return true;
        }
        console.warn("Projected graphics goTo failed, trying extent:", error);
    }

    if (zoomId !== activeZoomId) {
        return false;
    }

    try {
        const [geometryEngine] = await loadModules(["esri/geometry/geometryEngine"]);
        if (projectedGeometries.length === 1 && projectedGeometries[0].type === "point") {
            await mapView.goTo({ target: projectedGeometries[0], zoom: 18 }, { duration: 600 });
            return true;
        }

        const union = geometryEngine.union(projectedGeometries);
        if (union && union.extent) {
            await mapView.goTo(union.extent.expand(1.35), { duration: 600 });
            return true;
        }
    } catch (error) {
        if (error && error.name !== "AbortError") {
            console.warn("Extent zoom failed:", error);
        }
    }

    return false;
}

async function zoomToCenterTarget(target, zoomId) {
    if (!target || zoomId !== activeZoomId) {
        return false;
    }

    const [Point, projection] = await loadModules([
        "esri/geometry/Point",
        "esri/geometry/projection",
    ]);

    await projection.load();

    const geographicPoint = new Point({
        longitude: target.longitude,
        latitude: target.latitude,
        spatialReference: { wkid: 4326 },
    });

    const scale = scaleForSpan(target.span);
    const viewPoint = projection.project(geographicPoint, mapView.spatialReference);

    try {
        if (viewPoint) {
            await mapView.goTo({ target: viewPoint, scale }, { duration: 700, easing: "ease-in-out" });
            return true;
        }
    } catch (error) {
        if (error && error.name === "AbortError") {
            return true;
        }
        console.warn("Projected center zoom failed:", error);
    }

    if (zoomId !== activeZoomId) {
        return false;
    }

    try {
        await mapView.goTo(
            {
                center: [target.longitude, target.latitude],
                zoom: scale <= 3000 ? 18 : 15,
            },
            { duration: 500 },
        );
        return true;
    } catch (fallbackError) {
        if (fallbackError && fallbackError.name !== "AbortError") {
            console.warn("Geographic center zoom failed:", fallbackError);
        }
    }

    return false;
}

async function zoomToResults(geojson, geocode, mapTarget) {
    if (!mapView) {
        return;
    }

    const zoomId = ++activeZoomId;

    await mapView.when();
    mapView.resize();
    await waitForResultLayers();

    if (zoomId !== activeZoomId) {
        return;
    }

    if (mapTarget && (await zoomToGeographicCenter(mapTarget, zoomId))) {
        return;
    }

    if (await zoomToMapTarget(mapTarget, zoomId)) {
        return;
    }

    const graphics = collectResultGraphics();
    if (await zoomToGraphics(graphics, zoomId)) {
        return;
    }

    const target = computeZoomTarget(geojson, geocode);
    if (!target) {
        console.warn("Map zoom skipped: no result geometry to target.");
        return;
    }

    await zoomToCenterTarget(target, zoomId);
    if (mapTarget) {
        await zoomToGeographicCenter(mapTarget, zoomId);
    }
}

function buildMapTargetFromFeature(feature) {
    if (!feature || !feature.geometry) {
        return null;
    }
    return _buildMapTargetFromGeojson({ type: "FeatureCollection", features: [feature] });
}

function _buildMapTargetFromGeojson(geojson) {
    const bbox = bboxFromFeatures((geojson && geojson.features) || [], null);
    if (!bbox) {
        return null;
    }
    const span = Math.max(bbox.xmax - bbox.xmin, bbox.ymax - bbox.ymin);
    return {
        center: {
            x: (bbox.xmin + bbox.xmax) / 2,
            y: (bbox.ymin + bbox.ymax) / 2,
        },
        extent: {
            xmin: bbox.xmin,
            ymin: bbox.ymin,
            xmax: bbox.xmax,
            ymax: bbox.ymax,
            wkid: 4326,
        },
        scale: scaleForSpan(span),
    };
}

function withTimeout(promise, ms, label) {
    let timeoutId;
    const timeout = new Promise((_, reject) => {
        timeoutId = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
    });
    return Promise.race([promise, timeout]).finally(() => {
        clearTimeout(timeoutId);
    });
}

async function goToInitialExtent(mapView) {
    try {
        await mapView.goTo({ center: [-80.4692, 35.6709], zoom: 11 }, { duration: 0 });
    } catch (error) {
        console.warn("Initial map extent failed:", error);
    }
}

function createMapWithBasemap(Map, MapImageLayer, config) {
    const map = new Map({ basemap: "gray-vector" });

    if (config.basemapUrl) {
        const rowanLayer = new MapImageLayer({
            url: config.basemapUrl,
            title: "Rowan County GIS",
            listMode: "show",
        });
        map.add(rowanLayer);
    }

    map.addMany([resultsLayer, geocodeLayer]);
    return map;
}

async function initMap() {
    setMapStatus("Loading Rowan County map...");

    try {
        await waitForArcGIS();
    } catch (error) {
        setMapStatus("ArcGIS map library failed to load. Check internet access.", true);
        throw error;
    }

    const config = getConfig();
    resultsLayer = null;
    geocodeLayer = null;

    try {
        const [Map, MapView, GraphicsLayer, MapImageLayer] = await loadModules([
            "esri/Map",
            "esri/views/MapView",
            "esri/layers/GraphicsLayer",
            "esri/layers/MapImageLayer",
        ]);

        resultsLayer = new GraphicsLayer({
            title: "Query Results",
            listMode: "hide",
            elevationInfo: { mode: "on-the-ground" },
        });
        geocodeLayer = new GraphicsLayer({
            title: "Geocoded Address",
            listMode: "hide",
            elevationInfo: { mode: "on-the-ground" },
        });

        let map;

        if (config.webmapItemId) {
            const [WebMap] = await loadModules(["esri/WebMap"]);
            const webmap = new WebMap({
                portalItem: {
                    id: config.webmapItemId,
                    portal: { url: config.portalUrl || "https://www.arcgis.com" },
                },
            });
            map = webmap;
            await withTimeout(webmap.load(), 20000, "Web map load");
            webmap.addMany([resultsLayer, geocodeLayer]);
            webmap.reorder(resultsLayer, webmap.layers.length - 1);
            webmap.reorder(geocodeLayer, webmap.layers.length - 1);
        } else {
            map = createMapWithBasemap(Map, MapImageLayer, config);
        }

        map.reorder(resultsLayer, map.layers.length - 1);
        map.reorder(geocodeLayer, map.layers.length - 1);

        mapView = new MapView({
            container: "map-view",
            map,
            center: [-80.4692, 35.6709],
            zoom: 11,
            constraints: { snapToZoom: false },
        });

        await mapView.when();
        mapView.resize();
        setMapStatus("");
        setupMapSelectionHandler();
        await goToInitialExtent(mapView);

        return mapView;
    } catch (error) {
        console.error("Map initialization failed:", error);
        if (mapView) {
            setMapStatus("");
            return mapView;
        }
        setMapStatus("Map failed to load. Check network access to gis.rowancountync.gov.", true);
        throw error;
    }
}

async function whenReady() {
    if (!mapReadyPromise) {
        mapReadyPromise = initMap().catch((error) => {
            mapReadyPromise = null;
            throw error;
        });
    }
    return mapReadyPromise;
}

async function clearResults() {
    if (resultsLayer) {
        resultsLayer.removeAll();
    }
    if (geocodeLayer) {
        geocodeLayer.removeAll();
    }
}

function featureKey(feature) {
    const props = (feature && feature.properties) || feature || {};
    return props.PIN || props.PARCEL_ID || "";
}

function symbolForGeometry(esriGeometry, props, { selected = false, dimmed = false } = {}) {
    if (esriGeometry.type === "polygon") {
        if (selected) {
            return {
                type: "simple-fill",
                color: [255, 193, 7, 0.45],
                outline: { color: [180, 83, 9, 1], width: 3 },
            };
        }
        if (dimmed) {
            return {
                type: "simple-fill",
                color: [45, 134, 89, 0.12],
                outline: { color: [107, 114, 128, 0.8], width: 1 },
            };
        }
        return {
            type: "simple-fill",
            color: props.SUBNAME ? [0, 90, 180, 0.12] : [45, 134, 89, 0.35],
            outline: { color: props.SUBNAME ? [0, 90, 180, 1] : [30, 95, 63, 1], width: 2 },
        };
    }

    if (esriGeometry.type === "polyline") {
        return {
            type: "simple-line",
            color: selected ? [180, 83, 9, 1] : [0, 112, 255, 0.9],
            width: selected ? 4 : 3,
        };
    }

    return {
        type: "simple-marker",
        color: selected ? [180, 83, 9, 1] : [45, 134, 89, 1],
        size: selected ? 12 : 10,
        outline: { color: [255, 255, 255, 1], width: 1.5 },
    };
}

async function drawResultFeatures(drawFeatures, selectedKey = "") {
    if (!resultsLayer) {
        return [];
    }

    resultsLayer.removeAll();
    if (!drawFeatures.length) {
        return [];
    }

    const [Graphic, Polygon, Polyline, Point] = await loadModules([
        "esri/Graphic",
        "esri/geometry/Polygon",
        "esri/geometry/Polyline",
        "esri/geometry/Point",
    ]);
    const modules = { Polygon, Polyline, Point };
    const added = [];

    drawFeatures.forEach((feature) => {
        const geometry = feature.geometry;
        const props = feature.properties || {};
        const esriGeometry = featureToEsriGeometry(geometry, modules);
        if (!esriGeometry) {
            return;
        }

        const key = featureKey(feature);
        const selected = Boolean(selectedKey && key && key === selectedKey);
        const dimmed = Boolean(selectedKey && key && key !== selectedKey);
        const symbol = symbolForGeometry(esriGeometry, props, { selected, dimmed });
        const title = props.OWNNAME || props.Address || props.Whole_Name || props.SUBNAME || "Result";

        try {
            const graphic = new Graphic({
                geometry: esriGeometry,
                symbol,
                attributes: props,
                popupTemplate: {
                    title,
                    content: buildPopupText(props),
                },
            });
            resultsLayer.add(graphic);
            added.push(graphic);
        } catch (graphicError) {
            console.warn("Skipped result graphic:", graphicError);
        }
    });

    return added;
}

function setupMapSelectionHandler() {
    if (!mapView || mapClickHandler) {
        return;
    }

    mapClickHandler = mapView.on("click", async (event) => {
        if (!lastDrawFeatures.length) {
            return;
        }

        try {
            const response = await mapView.hitTest(event);
            const hit = response.results.find(
                (result) => result.graphic && result.graphic.layer === resultsLayer,
            );
            if (!hit || !hit.graphic) {
                return;
            }

            const pin = featureKey({ properties: hit.graphic.attributes || {} });
            const feature = lastDrawFeatures.find((item) => featureKey(item) === pin);
            if (!feature) {
                return;
            }

            await renderMapSelection(feature);
            window.dispatchEvent(new CustomEvent("gis-parcel-selected", { detail: { pin } }));
        } catch (error) {
            console.warn("Map parcel selection failed:", error);
        }
    });
}

async function renderMapSelection(selectedFeature) {
    if (!selectedFeature || !mapView || !resultsLayer) {
        return;
    }

    const selectedKey = featureKey(selectedFeature);
    await drawResultFeatures(lastDrawFeatures, selectedKey);

    const zoomId = ++activeZoomId;
    await mapView.when();
    mapView.resize();
    await waitForResultLayers();

    if (zoomId !== activeZoomId) {
        return;
    }

    const selectedGraphic = resultsLayer.graphics.find(
        (graphic) => featureKey({ properties: graphic.attributes || {} }) === selectedKey,
    );

    const mapTarget = buildMapTargetFromFeature(selectedFeature) || lastMapTarget;

    if (await zoomToMapTarget(mapTarget, zoomId)) {
        return;
    }

    if (selectedGraphic && await zoomToGraphics([selectedGraphic], zoomId)) {
        return;
    }

    await zoomToResults(
        { type: "FeatureCollection", features: [selectedFeature] },
        lastGeocode,
        mapTarget,
    );
}

function buildPopupText(props) {
    if (props.PIN || props.PARCEL_ID) {
        const pin = props.PIN || props.PARCEL_ID || "";
        const owner = props.OWNNAME || "";
        const address = props.PROP_ADDRESS || props.TAXADD1 || "";
        const city = props.CITY || "";
        const value = props.TOT_VAL;
        const valueLine = typeof value === "number" ? `<br>Total value: $${value.toLocaleString()}` : "";
        return `${pin}<br>${owner}<br>${address}${city ? `, ${city}` : ""}${valueLine}`;
    }
    if (props.Address) {
        return props.Address;
    }
    if (props.Whole_Name) {
        return props.Whole_Name;
    }
    if (props.SUBNAME) {
        return props.SUBNAME;
    }
    return "Query result";
}

function featureToEsriGeometry(geometry, modules) {
    const { Polygon, Polyline, Point } = modules;
    if (!geometry) {
        return null;
    }

    try {
        if (geometry.type === "Polygon") {
            return new Polygon({
                rings: geometry.coordinates,
                spatialReference: { wkid: 4326 },
            });
        }

        if (geometry.type === "MultiPolygon" && geometry.coordinates.length) {
            return new Polygon({
                rings: geometry.coordinates[0],
                spatialReference: { wkid: 4326 },
            });
        }

        if (geometry.type === "LineString") {
            return new Polyline({
                paths: [geometry.coordinates],
                spatialReference: { wkid: 4326 },
            });
        }

        if (geometry.type === "MultiLineString") {
            return new Polyline({
                paths: geometry.coordinates,
                spatialReference: { wkid: 4326 },
            });
        }

        if (geometry.type === "Point") {
            return new Point({
                longitude: geometry.coordinates[0],
                latitude: geometry.coordinates[1],
                spatialReference: { wkid: 4326 },
            });
        }
    } catch (error) {
        console.warn("Could not build geometry:", error);
    }

    return null;
}

async function showGeocode(geocode) {
    if (!mapView || !geocodeLayer || !geocode || !geocode.location) {
        return;
    }

    const [Graphic, Point] = await loadModules([
        "esri/Graphic",
        "esri/geometry/Point",
    ]);

    const sourceLabel = geocode.source === "rowan_addressing"
        ? "Rowan County address"
        : geocode.source === "nconemap"
            ? "NC AddressNC match"
            : "Geocoded location";

    const point = new Point({
        longitude: geocode.location.x,
        latitude: geocode.location.y,
        spatialReference: { wkid: 4326 },
    });

    geocodeLayer.add(
        new Graphic({
            geometry: point,
            symbol: {
                type: "simple-marker",
                color: [220, 53, 69, 0.9],
                size: 12,
                outline: { color: [255, 255, 255, 1], width: 2 },
            },
            attributes: { address: geocode.address || "Geocoded location" },
            popupTemplate: {
                title: sourceLabel,
                content: geocode.address || "Geocoded location",
            },
        })
    );
}

async function showResults(geojson, overlayGeojson, geocode, mapTarget) {
    try {
        await whenReady();
    } catch (error) {
        console.warn("Map not ready:", error);
        return;
    }

    if (!mapView || !resultsLayer) {
        console.warn("Map is not ready yet.");
        return;
    }

    await clearResults();

    const features = [
        ...((geojson && geojson.features) || []),
        ...((overlayGeojson && overlayGeojson.features) || []),
    ];

    if (geocode) {
        try {
            await showGeocode(geocode);
        } catch (error) {
            console.warn("Geocode marker failed:", error);
        }
    }

    const drawFeatures = features.filter((feature) => {
        const props = feature.properties || {};
        return props._lookup !== "address_point";
    });

    lastDrawFeatures = drawFeatures;
    lastGeocode = geocode || null;
    lastMapTarget = mapTarget || _buildMapTargetFromGeojson(geojson) || null;

    if (drawFeatures.length) {
        try {
            await drawResultFeatures(drawFeatures);
        } catch (error) {
            console.warn("Result graphics failed:", error);
        }
    }

    try {
        mapView.resize();
        await zoomToResults(geojson, geocode, lastMapTarget);
    } catch (error) {
        console.warn("Map zoom failed:", error);
    }
}

async function zoomToFeature(feature) {
    if (!feature) {
        return;
    }
    await whenReady();
    if (!mapView || !resultsLayer) {
        return;
    }
    await renderMapSelection(feature);
}

window.GisMap = {
    whenReady,
    initMap,
    showResults,
    showGeocode,
    zoomToFeature,
    getDefaultSuggestions: () => DEFAULT_SUGGESTIONS,
};

document.addEventListener("DOMContentLoaded", () => {
    whenReady().catch((error) => {
        console.error("Failed to initialize map", error);
        setMapStatus("Map failed to load. Check network access to gis.rowancountync.gov.", true);
    });
});
