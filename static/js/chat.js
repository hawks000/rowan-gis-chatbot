function getSessionId() {
    const key = "gis-chatbot-session-id";
    let sessionId = localStorage.getItem(key);
    if (!sessionId) {
        sessionId = crypto.randomUUID();
        localStorage.setItem(key, sessionId);
    }
    return sessionId;
}

const RECENT_KEY = "gis-chatbot-recent";
const FEATURED_EXAMPLES = [
    "How many subdivisions are in Rowan County",
    "Who owns 550 MT HALL RD",
    "How many parcels on Woodleaf",
    "PIN 5733-04-51-7482",
];

function escapeHtml(text) {
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function linkifyText(text) {
    const lines = escapeHtml(text).split("\n");
    return lines.map((line, index) => {
        const urlMatch = line.match(/^(https?:\/\/[^\s<]+)$/);
        if (!urlMatch) {
            return line;
        }
        const previousLine = lines[index - 1] || "";
        let label = "View on Register of Deeds";
        if (previousLine.includes("Plat record:")) {
            label = "View plat on Register of Deeds";
        } else if (previousLine.includes("Deed record:")) {
            label = "View deed on Register of Deeds";
        }
        return `<a href="${urlMatch[1]}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    }).join("\n");
}

function parcelFeatures(geojson) {
    return (geojson?.features || []).filter((feature) => {
        const props = feature.properties || {};
        return props._lookup !== "address_point";
    });
}

function findFeatureForSummary(geojson, summary, index) {
    const features = parcelFeatures(geojson);
    const pin = summary.PIN || summary.PARCEL_ID;
    if (pin) {
        const match = features.find((feature) => {
            const props = feature.properties || {};
            return props.PIN === pin || props.PARCEL_ID === pin;
        });
        if (match) {
            return match;
        }
    }
    return features[index] || null;
}

function setActiveResultItem(pin) {
    document.querySelectorAll(".result-item").forEach((item) => {
        const label = item.querySelector("strong");
        item.classList.toggle("active", Boolean(pin && label && label.textContent === pin));
    });
}

function appendMessage(text, role) {
    const container = document.getElementById("chat-messages");
    const bubble = document.createElement("div");
    bubble.className = `message ${role}`;
    if (role === "bot") {
        bubble.innerHTML = linkifyText(text);
    } else {
        bubble.textContent = text;
    }
    container.appendChild(bubble);
    container.scrollTop = container.scrollHeight;
    return bubble;
}

function appendPropertyCard(card) {
    if (!card || !card.pin) {
        return;
    }

    const container = document.getElementById("chat-messages");
    const wrapper = document.createElement("div");
    wrapper.className = "property-card";

    const factsHtml = (card.facts || [])
        .map((item) => `<dt>${escapeHtml(item.label)}</dt><dd>${escapeHtml(item.value)}</dd>`)
        .join("");
    const contextHtml = (card.context || [])
        .map((item) => `<dt>${escapeHtml(item.label)}</dt><dd>${escapeHtml(item.value)}</dd>`)
        .join("");

    wrapper.innerHTML = `
        <div class="property-card-header">
            <strong>${escapeHtml(card.pin)}</strong>
            <span>${escapeHtml(card.address || "No address on file")}</span>
        </div>
        <p class="property-card-owner">${escapeHtml(card.owner || "Unknown owner")}</p>
        ${factsHtml ? `<dl class="property-card-grid">${factsHtml}</dl>` : ""}
        ${contextHtml ? `<dl class="property-card-grid property-card-context">${contextHtml}</dl>` : ""}
    `;
    container.appendChild(wrapper);
    container.scrollTop = container.scrollHeight;
}

function appendSuggestionChips(suggestions) {
    if (!suggestions || !suggestions.length) {
        return;
    }

    const container = document.getElementById("chat-messages");
    const row = document.createElement("div");
    row.className = "suggestion-chips";

    const label = document.createElement("span");
    label.className = "suggestion-chips-label";
    label.textContent = "Did you mean:";
    row.appendChild(label);

    suggestions.forEach((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "chip-button";
        button.textContent = item.label || item.query;
        button.title = item.query;
        button.addEventListener("click", () => {
            submitQuery(item.query);
        });
        row.appendChild(button);
    });

    container.appendChild(row);
    container.scrollTop = container.scrollHeight;
}

function renderSuggestions(examples) {
    const container = document.getElementById("suggestion-buttons");
    container.innerHTML = "";
    examples.forEach((example) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = example;
        button.addEventListener("click", () => {
            submitQuery(example);
        });
        container.appendChild(button);
    });
}

function saveRecentSearch(query) {
    const trimmed = query.trim();
    if (!trimmed) {
        return;
    }
    const recent = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    const updated = [trimmed, ...recent.filter((item) => item !== trimmed)].slice(0, 8);
    localStorage.setItem(RECENT_KEY, JSON.stringify(updated));
    renderRecentSearches();
}

function renderRecentSearches() {
    const recent = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    const section = document.getElementById("recent-searches");
    const container = document.getElementById("recent-buttons");
    if (!recent.length) {
        section.classList.add("hidden");
        return;
    }

    section.classList.remove("hidden");
    container.innerHTML = "";
    recent.forEach((example) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = example;
        button.addEventListener("click", () => {
            submitQuery(example);
        });
        container.appendChild(button);
    });
}

function updateShareUrl(query) {
    const url = new URL(window.location.href);
    url.searchParams.set("q", query);
    window.history.replaceState({}, "", url);
}

function readInitialQuery() {
    return new URLSearchParams(window.location.search).get("q");
}

function renderResultList(summaries, geojson) {
    const list = document.getElementById("result-list");
    list.innerHTML = "";

    if (!summaries || summaries.length <= 1) {
        list.classList.add("hidden");
        return;
    }

    list.classList.remove("hidden");
    summaries.forEach((summary, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "result-item";
        const pin = summary.PIN || summary.PARCEL_ID;
        const address = summary.PROP_ADDRESS || summary.TAXADD1 || summary.Address || "No address";
        const label = pin || summary.Whole_Name || summary.SUBNAME || "Result";
        button.innerHTML = `<strong>${label}</strong><span>${address}</span>`;
        button.addEventListener("click", async (event) => {
            event.preventDefault();
            event.stopPropagation();
            const selectedPin = summary.PIN || summary.PARCEL_ID;
            setActiveResultItem(selectedPin);
            const feature = findFeatureForSummary(geojson, summary, index);
            if (!feature || !window.GisMap) {
                return;
            }
            try {
                await window.GisMap.whenReady();
                await window.GisMap.zoomToFeature(feature);
            } catch (error) {
                console.error("Result list zoom failed:", error);
            }
        });
        list.appendChild(button);
    });
}

async function applyQueryResponse(data, responseOk = true) {
    const role = responseOk ? "bot" : "error";
    appendMessage(data.message || "Unexpected response.", role);

    if (data.property_card) {
        appendPropertyCard(data.property_card);
    }
    if (data.suggestions && data.suggestions.length) {
        appendSuggestionChips(data.suggestions);
    }

    const skipMap = (data.intent && data.intent.intent_type === "list_subdivisions")
        || !(data.geojson && data.geojson.features && data.geojson.features.length);

    if (!skipMap && data.geojson && window.GisMap) {
        try {
            await window.GisMap.whenReady();
            await window.GisMap.showResults(
                data.geojson,
                data.overlay_geojson,
                data.geocode,
                data.map_target,
            );
            renderResultList(data.summaries || [], data.geojson);

            const pin = (data.summaries && data.summaries[0] && (data.summaries[0].PIN || data.summaries[0].PARCEL_ID)) || "";
            if (pin) {
                setActiveResultItem(pin);
            }
        } catch (mapError) {
            console.error("Map update failed:", mapError);
            appendMessage("Results loaded, but the map could not update.", "error");
        }
    }

    if (window.GisMap && window.GisMap.clearMapStatus) {
        window.GisMap.clearMapStatus();
    }
}

async function submitQuery(message) {
    const config = window.GIS_CHATBOT_CONFIG;
    const submitButton = document.getElementById("chat-submit");
    submitButton.disabled = true;

    appendMessage(message, "user");
    saveRecentSearch(message);
    updateShareUrl(message);

    try {
        const response = await fetch(config.queryUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                message,
                session_id: getSessionId(),
            }),
        });

        let data;
        try {
            data = await response.json();
        } catch (parseError) {
            appendMessage(
                `Server error (${response.status}). Restart start-local.bat and try again.`,
                "error",
            );
            console.error(parseError);
            return;
        }

        if (response.status === 429) {
            await applyQueryResponse(data, false);
            return;
        }

        await applyQueryResponse(data, response.ok);
    } catch (error) {
        appendMessage("Network error. Please try again.", "error");
        console.error(error);
    } finally {
        submitButton.disabled = false;
    }
}

async function lookupParcelAtPoint(longitude, latitude) {
    const config = window.GIS_CHATBOT_CONFIG;
    if (!config.parcelAtPointUrl) {
        return;
    }

    appendMessage("Map parcel lookup", "user");
    appendMessage("Looking up parcel at that location…", "bot");

    try {
        const response = await fetch(config.parcelAtPointUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                longitude,
                latitude,
                session_id: getSessionId(),
            }),
        });

        let data;
        try {
            data = await response.json();
        } catch (parseError) {
            appendMessage(
                `Server error (${response.status}). Restart start-local.bat and try again.`,
                "error",
            );
            console.error(parseError);
            return;
        }

        await applyQueryResponse(data, response.ok);
    } catch (error) {
        appendMessage("Network error. Please try again.", "error");
        console.error(error);
    } finally {
        if (window.GisMap && window.GisMap.clearMapStatus) {
            window.GisMap.clearMapStatus();
        }
        if (window.GisMap && window.GisMap.releaseMapClick) {
            window.GisMap.releaseMapClick();
        }
    }
}

async function loadLayerExamples() {
    const config = window.GIS_CHATBOT_CONFIG;
    try {
        const response = await fetch(config.layersUrl);
        const data = await response.json();
        const fromLayers = (data.layers || [])
            .flatMap((layer) => (layer.examples || []).slice(0, 1))
            .filter(Boolean);
        const merged = [...FEATURED_EXAMPLES];
        fromLayers.forEach((example) => {
            if (!merged.some((item) => item.toLowerCase() === example.toLowerCase())) {
                merged.push(example);
            }
        });
        renderSuggestions(merged.slice(0, 6));
    } catch (error) {
        renderSuggestions(FEATURED_EXAMPLES);
    }
}

let autocompleteTimer = null;
let cachedSubdivisions = null;

async function loadSubdivisionsCache() {
    if (cachedSubdivisions) {
        return cachedSubdivisions;
    }
    const config = window.GIS_CHATBOT_CONFIG;
    try {
        const response = await fetch(config.subdivisionsUrl);
        const data = await response.json();
        cachedSubdivisions = data.names || [];
    } catch (error) {
        cachedSubdivisions = [];
    }
    return cachedSubdivisions;
}

async function updateAutocompleteOptions(value) {
    const datalist = document.getElementById("search-suggestions");
    const needle = value.trim();
    if (needle.length < 2) {
        datalist.innerHTML = "";
        return;
    }

    const options = new Set();
    const subdivisions = await loadSubdivisionsCache();
    subdivisions
        .filter((name) => name.toUpperCase().includes(needle.toUpperCase()))
        .slice(0, 8)
        .forEach((name) => options.add(`how many parcels in ${name}`));

    try {
        const config = window.GIS_CHATBOT_CONFIG;
        const response = await fetch(`${config.autocompleteUrl}?q=${encodeURIComponent(needle)}`);
        const data = await response.json();
        (data.streets || []).slice(0, 5).forEach((street) => {
            options.add(`How many parcels on ${street}`);
        });
        (data.subdivisions || []).slice(0, 5).forEach((name) => {
            options.add(`how many parcels in ${name}`);
        });
    } catch (error) {
        console.warn("Autocomplete failed:", error);
    }

    datalist.innerHTML = "";
    [...options].slice(0, 10).forEach((option) => {
        const node = document.createElement("option");
        node.value = option;
        datalist.appendChild(node);
    });
}

document.addEventListener("DOMContentLoaded", () => {
    appendMessage(
        "Hello! Ask about a parcel PIN, address, owner, street, or subdivision — or click a parcel on the map.",
        "bot"
    );
    loadLayerExamples();
    renderRecentSearches();
    loadSubdivisionsCache();

    const input = document.getElementById("chat-input");
    input.addEventListener("input", () => {
        window.clearTimeout(autocompleteTimer);
        autocompleteTimer = window.setTimeout(() => {
            updateAutocompleteOptions(input.value);
        }, 250);
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "/" && document.activeElement !== input) {
            event.preventDefault();
            input.focus();
        }
    });

    const pictometryToggle = document.getElementById("toggle-pictometry");
    if (pictometryToggle) {
        pictometryToggle.addEventListener("change", async () => {
            if (!window.GisMap) {
                return;
            }
            await window.GisMap.whenReady();
            window.GisMap.setPictometryVisible(pictometryToggle.checked);
        });
    }

    window.addEventListener("gis-parcel-selected", (event) => {
        setActiveResultItem(event.detail?.pin || "");
    });

    window.addEventListener("gis-map-parcel-click", (event) => {
        const { longitude, latitude } = event.detail || {};
        if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) {
            return;
        }
        lookupParcelAtPoint(longitude, latitude);
    });

    document.getElementById("chat-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const message = input.value.trim();
        if (!message) {
            return;
        }
        input.value = "";
        await submitQuery(message);
    });

    const initialQuery = readInitialQuery();
    if (initialQuery) {
        input.value = initialQuery;
        submitQuery(initialQuery);
    }
});
