/* global require, window */

let mapView = null;
let resultsLayer = null;
let geocodeLayer = null;
let mapReadyPromise = null;
let lastDrawFeatures = [];
let lastGeocode = null;
let lastMapTarget = null;
let mapClickHandler = null;
let basemapLayer = null;
let pictometryLayer = null;

const DEFAULT_SUGGESTIONS = [
    "How many subdivisions are in Rowan County",
    "Who owns 550 MT HALL RD",
    "How many parcels on Woodleaf",
    "PIN 5733-04-51-7482",
    "Parcels in Grand Oaks",
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

const GO_TO_OPTIONS = { duration: 0 };

function scaleForZoomLevel(zoom) {
    return 591657527.591555 / (2 ** zoom);
}

function zoomLevelForSpan(span) {
    if (!span || !Number.isFinite(span)) {
        return 18;
    }
    if (span > 0.08) {
        return 11;
    }
    if (span > 0.03) {
        return 13;
    }
    if (span > 0.01) {
        return 15;
    }
    if (span > 0.003) {
        return 16;
    }
    return 18;
}

async function forceViewToLonLat(longitude, latitude, zoom = 18) {
    if (!mapView || !Number.isFinite(longitude) || !Number.isFinite(latitude)) {
        return;
    }

    const scale = 591657527.591555 / (2 ** zoom);

    try {
        await mapView.when();

        const [Point] = await loadModules(["esri/geometry/Point"]);
        const point = new Point({
            longitude,
            latitude,
            spatialReference: { wkid: 4326 },
        });

        await mapView.goTo({ center: point, scale }, { animate: false, duration: 0 });
        mapView.center = point;
        mapView.scale = scale;
    } catch (error) {
        if (error && error.name !== "AbortError") {
            console.warn("Map zoom failed:", error);
        }
    }
}

function geometryExtent(geometry) {
    return geometry && geometry.extent ? geometry.extent : null;
}

async function zoomToResultGraphics() {
    if (!mapView) {
        return;
    }

    await mapView.when();
    await waitForResultLayers();

    const graphics = [];
    if (resultsLayer && resultsLayer.graphics) {
        graphics.push(...resultsLayer.graphics.toArray());
    }
    if (geocodeLayer && geocodeLayer.graphics) {
        graphics.push(...geocodeLayer.graphics.toArray());
    }

    if (!graphics.length) {
        if (lastGeocode && lastGeocode.location) {
            await forceViewToLonLat(lastGeocode.location.x, lastGeocode.location.y, 18);
        }
        return;
    }

    try {
        if (graphics.length === 1) {
            const geometry = graphics[0].geometry;
            if (geometry && geometry.type === "point") {
                await mapView.goTo({ target: geometry, zoom: 18 }, { animate: false, duration: 0 });
            } else {
                const extent = geometryExtent(geometry);
                if (extent) {
                    await mapView.goTo(extent.expand(1.8), { animate: false, duration: 0 });
                }
            }
            return;
        }

        let extent = geometryExtent(graphics[0].geometry);
        for (let i = 1; i < graphics.length; i += 1) {
            const next = geometryExtent(graphics[i].geometry);
            if (next) {
                extent = extent ? extent.union(next) : next;
            }
        }
        if (extent) {
            await mapView.goTo(extent.expand(1.2), { animate: false, duration: 0 });
        }
    } catch (error) {
        if (error && error.name !== "AbortError") {
            console.warn("Graphic zoom failed:", error);
        }
        if (lastGeocode && lastGeocode.location) {
            await forceViewToLonLat(lastGeocode.location.x, lastGeocode.location.y, 18);
        }
    }
}

async function forceViewToExtent(extent4326, paddingFactor = 1.4) {
    if (!extent4326) {
        return;
    }

    const span = Math.max(
        (extent4326.xmax - extent4326.xmin) * paddingFactor,
        (extent4326.ymax - extent4326.ymin) * paddingFactor,
    );
    const centerLon = (extent4326.xmin + extent4326.xmax) / 2;
    const centerLat = (extent4326.ymin + extent4326.ymax) / 2;
    await forceViewToLonLat(centerLon, centerLat, zoomLevelForSpan(span));
}

async function waitForMapFrame() {
    await new Promise((resolve) => window.requestAnimationFrame(resolve));
    await new Promise((resolve) => window.requestAnimationFrame(resolve));
}

async function zoomToDrawnResults(geojson, geocode, mapTarget) {
    if (!mapView) {
        return;
    }

    await waitForMapFrame();
    await zoomToResultGraphics();

    if (mapView.zoom > 14) {
        return;
    }

    const target = mapTarget || lastMapTarget || _buildMapTargetFromGeojson(geojson);
    if (geocode && geocode.location) {
        await forceViewToLonLat(geocode.location.x, geocode.location.y, 18);
        return;
    }
    if (target && target.extent) {
        await forceViewToExtent(target.extent, lastDrawFeatures.length > 1 ? 1.25 : 1.5);
    }
}

async function zoomToResults(geojson, geocode, mapTarget) {
    await zoomToDrawnResults(geojson, geocode, mapTarget);
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

async function waitForBasemapLayer() {
    return waitForBasemap();
}

function createRowanMap(EsriMap, mapViewerLayer, pictometry) {
    const layers = [pictometry, mapViewerLayer, resultsLayer, geocodeLayer].filter(Boolean);
    return new EsriMap({
        basemap: mapViewerLayer ? null : "gray-vector",
        layers,
    });
}

async function createMapImageLayer(MapImageLayer, url, title) {
    if (!url) {
        return null;
    }

    const layer = new MapImageLayer({
        url,
        title,
        listMode: "hide",
    });

    try {
        await layer.load();
    } catch (error) {
        console.warn(`${title} failed to load:`, error);
        return null;
    }

    return layer;
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
    basemapLayer = null;
    pictometryLayer = null;

    try {
        const [EsriMap, MapViewClass, GraphicsLayer, MapImageLayer] = await loadModules([
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

        basemapLayer = await createMapImageLayer(
            MapImageLayer,
            config.basemapUrl,
            "Rowan County Map",
        );
        pictometryLayer = await createMapImageLayer(
            MapImageLayer,
            config.pictometryUrl,
            "Pictometry 2025",
        );

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
            if (pictometryLayer) {
                webmap.add(pictometryLayer, 0);
            }
            if (basemapLayer) {
                webmap.add(basemapLayer, pictometryLayer ? 1 : 0);
            }
            webmap.addMany([resultsLayer, geocodeLayer]);
            webmap.reorder(resultsLayer, webmap.layers.length - 1);
            webmap.reorder(geocodeLayer, webmap.layers.length - 1);
        } else {
            map = createRowanMap(EsriMap, basemapLayer, pictometryLayer);
        }

        map.reorder(resultsLayer, map.layers.length - 1);
        map.reorder(geocodeLayer, map.layers.length - 1);

        mapView = new MapViewClass({
            container: "map-view",
            map,
            center: [-80.4692, 35.6709],
            zoom: 10,
            constraints: { snapToZoom: false },
            popupEnabled: false,
        });

        await mapView.when();
        if (typeof mapView.goTo !== "function") {
            throw new Error("MapView did not initialize correctly");
        }
        setMapStatus("");
        setupMapSelectionHandler();

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

function formatOwnerName(props) {
    const primary = String(props.OWNNAME || "").replace(/&\s*$/, "").trim();
    const secondary = String(props.OWN2 || "").trim();
    if (primary && secondary) {
        return `${primary} & ${secondary}`;
    }
    return primary || secondary || "";
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
        const title = formatOwnerName(props) || props.Address || props.Whole_Name || props.SUBNAME || "Result";

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

let mapClickInFlight = false;
let mapClickResetTimer = null;

function releaseMapClickLock() {
    mapClickInFlight = false;
    if (mapClickResetTimer) {
        window.clearTimeout(mapClickResetTimer);
        mapClickResetTimer = null;
    }
}

function lockMapClick() {
    releaseMapClickLock();
    mapClickInFlight = true;
    mapClickResetTimer = window.setTimeout(releaseMapClickLock, 45000);
}

async function resolveMapCoordinates(event) {
    if (!mapView) {
        return null;
    }

    await mapView.when();
    let mapPoint = event.mapPoint;
    if (!mapPoint && Number.isFinite(event.x) && Number.isFinite(event.y)) {
        mapPoint = mapView.toMap({ x: event.x, y: event.y });
    }
    if (!mapPoint) {
        return null;
    }

    const [projection] = await loadModules(["esri/geometry/projection"]);
    await projection.load();

    const wgs84 = { wkid: 4326 };
    let geoPoint = mapPoint;
    if (!mapPoint.spatialReference || mapPoint.spatialReference.wkid !== 4326) {
        geoPoint = projection.project(mapPoint, wgs84);
    }
    if (!geoPoint) {
        return null;
    }

    const longitude = geoPoint.longitude ?? geoPoint.x;
    const latitude = geoPoint.latitude ?? geoPoint.y;
    if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) {
        return null;
    }

    return { longitude, latitude };
}

async function handleMapInteraction(event) {
    if (mapClickInFlight || !mapView) {
        return;
    }

    try {
        if (resultsLayer && lastDrawFeatures.length) {
            const response = await mapView.hitTest(event, { include: resultsLayer });
            const hit = response.results.find(
                (result) => result.graphic && result.graphic.layer === resultsLayer,
            );

            if (hit && hit.graphic) {
                const pin = featureKey({ properties: hit.graphic.attributes || {} });
                const feature = lastDrawFeatures.find((item) => featureKey(item) === pin);
                if (feature) {
                    await renderMapSelection(feature);
                    window.dispatchEvent(new CustomEvent("gis-parcel-selected", { detail: { pin } }));
                    return;
                }
            }
        }

        const coords = await resolveMapCoordinates(event);
        if (!coords) {
            setMapStatus("Could not read map location. Try clicking again.", true);
            window.setTimeout(() => setMapStatus(""), 2500);
            return;
        }

        setMapStatus("Looking up parcel...");
        lockMapClick();
        window.dispatchEvent(new CustomEvent("gis-map-parcel-click", {
            detail: coords,
        }));
    } catch (error) {
        console.warn("Map click failed:", error);
        setMapStatus("");
        releaseMapClickLock();
    }
}

function setupMapSelectionHandler() {
    if (!mapView || mapClickHandler) {
        return;
    }

    mapClickHandler = mapView.on("click", handleMapInteraction);
    mapView.on("pointer-down", (event) => {
        if (event.button === 2) {
            event.stopPropagation();
            handleMapInteraction(event);
        }
    });

    if (mapView.container) {
        mapView.container.addEventListener("contextmenu", (event) => {
            event.preventDefault();
        });
    }
}

async function renderMapSelection(selectedFeature) {
    if (!selectedFeature || !mapView || !resultsLayer) {
        return;
    }

    const selectedKey = featureKey(selectedFeature);
    await drawResultFeatures(lastDrawFeatures, selectedKey);

    await mapView.when();
    await waitForResultLayers();

    const mapTarget = buildMapTargetFromFeature(selectedFeature) || lastMapTarget;
    lastDrawFeatures = lastDrawFeatures.length ? lastDrawFeatures : [selectedFeature];
    await zoomToDrawnResults(
        { type: "FeatureCollection", features: [selectedFeature] },
        lastGeocode,
        mapTarget,
    );
}

function buildPopupText(props) {
    if (props.PIN || props.PARCEL_ID) {
        const pin = props.PIN || props.PARCEL_ID || "";
        const owner = formatOwnerName(props);
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

    try {
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

        await waitForMapFrame();
        await zoomToDrawnResults(geojson, geocode, lastMapTarget);
        window.setTimeout(() => {
            zoomToResultGraphics().catch(() => null);
        }, 800);
    } catch (error) {
        console.warn("showResults failed:", error);
    }
}

async function zoomToMapTarget(mapTarget, geocode) {
    try {
        await whenReady();
        if (!mapView) {
            return;
        }
        await zoomToDrawnResults(null, geocode, mapTarget || lastMapTarget);
    } catch (error) {
        console.warn("zoomToMapTarget failed:", error);
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
    zoomToMapTarget,
    clearMapStatus: () => setMapStatus(""),
    releaseMapClick: releaseMapClickLock,
    getDefaultSuggestions: () => DEFAULT_SUGGESTIONS,
};

document.addEventListener("DOMContentLoaded", () => {
    whenReady().catch((error) => {
        console.error("Failed to initialize map", error);
        setMapStatus("Map failed to load. Check network access to gis.rowancountync.gov.", true);
    });
});
