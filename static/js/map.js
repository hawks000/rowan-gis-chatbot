/* global require, window */

let mapView = null;
let resultsLayer = null;
let geocodeLayer = null;

const DEFAULT_SUGGESTIONS = [
    "Who owns 550 MT HALL RD",
    "Find Earl Hawks owning property",
    "PIN 5733-04-51-7482",
    "Show parcels on Woodleaf",
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

async function initMap() {
    setMapStatus("Loading Rowan County map...");
    await waitForArcGIS();

    const config = getConfig();
    const [Map, MapView, MapImageLayer, GraphicsLayer] = await loadModules([
        "esri/Map",
        "esri/views/MapView",
        "esri/layers/MapImageLayer",
        "esri/layers/GraphicsLayer",
    ]);

    const basemapLayer = new MapImageLayer({
        url: config.basemapUrl,
        title: "Rowan County Basemap",
    });

    resultsLayer = new GraphicsLayer({ title: "Query Results" });
    geocodeLayer = new GraphicsLayer({ title: "Geocoded Address" });

    const map = new Map({
        layers: [basemapLayer, resultsLayer, geocodeLayer],
    });

    mapView = new MapView({
        container: "map-view",
        map,
        constraints: { snapToZoom: false },
    });

    await mapView.when();
    await basemapLayer.when();
    if (basemapLayer.fullExtent) {
        await mapView.goTo(basemapLayer.fullExtent.expand(1.05));
    } else {
        await mapView.goTo({ center: [-80.4692, 35.6709], zoom: 10 });
    }

    setMapStatus("");
    return mapView;
}

async function clearResults() {
    if (resultsLayer) {
        resultsLayer.removeAll();
    }
}

async function showGeocode(geocode) {
    if (!mapView || !geocodeLayer || !geocode || !geocode.location) {
        return;
    }

    const [Graphic, Point] = await loadModules(["esri/Graphic", "esri/geometry/Point"]);
    geocodeLayer.removeAll();

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
                title: "NC OneMap match",
                content: "{address}",
            },
        })
    );

    if (geocode.extent) {
        await mapView.goTo({
            target: [
                geocode.extent.xmin,
                geocode.extent.ymin,
                geocode.extent.xmax,
                geocode.extent.ymax,
            ],
            spatialReference: { wkid: 4326 },
        }, { duration: 800 });
    } else {
        await mapView.goTo({ target: point, zoom: 17 }, { duration: 800 });
    }
}

async function showResults(geojson) {
    if (!mapView || !resultsLayer) {
        return;
    }

    await clearResults();
    const features = (geojson && geojson.features) || [];
    if (!features.length) {
        return;
    }

    const [Graphic, Polygon, Point, geometryEngine] = await loadModules([
        "esri/Graphic",
        "esri/geometry/Polygon",
        "esri/geometry/Point",
        "esri/geometry/geometryEngine",
    ]);

    const targets = [];
    features.forEach((feature) => {
        const geometry = feature.geometry;
        const props = feature.properties || {};
        let esriGeometry = null;

        if (geometry && geometry.type === "Polygon") {
            esriGeometry = new Polygon({
                rings: geometry.coordinates,
                spatialReference: { wkid: 4326 },
            });
        } else if (geometry && geometry.type === "MultiPolygon" && geometry.coordinates.length) {
            esriGeometry = new Polygon({
                rings: geometry.coordinates[0],
                spatialReference: { wkid: 4326 },
            });
        } else if (geometry && geometry.type === "Point") {
            esriGeometry = new Point({
                longitude: geometry.coordinates[0],
                latitude: geometry.coordinates[1],
                spatialReference: { wkid: 4326 },
            });
        }

        if (!esriGeometry) {
            return;
        }

        resultsLayer.add(
            new Graphic({
                geometry: esriGeometry,
                symbol: esriGeometry.type === "polygon"
                    ? {
                        type: "simple-fill",
                        color: [45, 134, 89, 0.35],
                        outline: { color: [30, 95, 63, 1], width: 2 },
                    }
                    : {
                        type: "simple-marker",
                        color: [45, 134, 89, 1],
                        size: 10,
                        outline: { color: [255, 255, 255, 1], width: 1.5 },
                    },
                attributes: props,
                popupTemplate: {
                    title: "{OWNNAME}",
                    content: [
                        {
                            type: "fields",
                            fieldInfos: [
                                { fieldName: "PIN", label: "PIN" },
                                { fieldName: "PROP_ADDRESS", label: "Address" },
                                { fieldName: "CITY", label: "City" },
                                { fieldName: "TOT_VAL", label: "Total Value" },
                            ],
                        },
                    ],
                },
            })
        );
        targets.push(esriGeometry);
    });

    if (targets.length === 1) {
        const target = targets[0];
        if (target.type === "polygon") {
            await mapView.goTo(geometryEngine.extent(target).expand(1.8), { duration: 800 });
        } else {
            await mapView.goTo({ target, zoom: 17 }, { duration: 800 });
        }
    } else if (targets.length > 1) {
        let extent = geometryEngine.extent(targets[0]);
        for (let i = 1; i < targets.length; i += 1) {
            extent = extent.union(geometryEngine.extent(targets[i]));
        }
        await mapView.goTo(extent.expand(1.2), { duration: 800 });
    }
}

async function zoomToFeature(feature) {
    if (!mapView || !feature || !feature.geometry) {
        return;
    }

    const [geometryEngine, Polygon, Point] = await loadModules([
        "esri/geometry/geometryEngine",
        "esri/geometry/Polygon",
        "esri/geometry/Point",
    ]);

    const geometry = feature.geometry;
    let target = null;

    if (geometry.type === "Polygon") {
        target = new Polygon({
            rings: geometry.coordinates,
            spatialReference: { wkid: 4326 },
        });
    } else if (geometry.type === "Point") {
        target = new Point({
            longitude: geometry.coordinates[0],
            latitude: geometry.coordinates[1],
            spatialReference: { wkid: 4326 },
        });
    } else if (geometry.type === "MultiPolygon" && geometry.coordinates.length) {
        target = new Polygon({
            rings: geometry.coordinates[0],
            spatialReference: { wkid: 4326 },
        });
    }

    if (!target) {
        return;
    }

    if (target.type === "polygon") {
        await mapView.goTo(geometryEngine.extent(target).expand(1.8), { duration: 800 });
    } else {
        await mapView.goTo({ target, zoom: 17 }, { duration: 800 });
    }
}

window.GisMap = {
    initMap,
    showResults,
    showGeocode,
    zoomToFeature,
    getDefaultSuggestions: () => DEFAULT_SUGGESTIONS,
};

document.addEventListener("DOMContentLoaded", () => {
    initMap().catch((error) => {
        console.error("Failed to initialize map", error);
        setMapStatus("Map failed to load. Check network access to gis.rowancountync.gov.", true);
    });
});
