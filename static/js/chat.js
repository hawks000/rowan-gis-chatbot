function getSessionId() {
    const key = "gis-chatbot-session-id";
    let sessionId = localStorage.getItem(key);
    if (!sessionId) {
        sessionId = crypto.randomUUID();
        localStorage.setItem(key, sessionId);
    }
    return sessionId;
}

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
}

function renderSuggestions(examples) {
    const container = document.getElementById("suggestion-buttons");
    container.innerHTML = "";
    examples.forEach((example) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = example;
        button.addEventListener("click", () => {
            document.getElementById("chat-input").value = example;
            document.getElementById("chat-form").requestSubmit();
        });
        container.appendChild(button);
    });
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
        button.addEventListener("click", async () => {
            const pin = summary.PIN || summary.PARCEL_ID;
            setActiveResultItem(pin);
            const feature = findFeatureForSummary(geojson, summary, index);
            if (!feature) {
                console.warn("No map feature found for result", summary);
                return;
            }
            await window.GisMap.zoomToFeature(feature);
        });
        list.appendChild(button);
    });
}

async function submitQuery(message) {
    const config = window.GIS_CHATBOT_CONFIG;
    const submitButton = document.getElementById("chat-submit");
    submitButton.disabled = true;

    appendMessage(message, "user");

    try {
        const response = await fetch(config.queryUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                message,
                session_id: getSessionId(),
            }),
        });

        const data = await response.json();
        const role = response.ok ? "bot" : "error";
        appendMessage(data.message || "Unexpected response.", role);

        if (data.geojson && window.GisMap) {
            try {
                await window.GisMap.whenReady();
                await window.GisMap.showResults(
                    data.geojson,
                    data.overlay_geojson,
                    data.geocode,
                    data.map_target,
                );
                renderResultList(data.summaries || [], data.geojson);
            } catch (mapError) {
                console.error("Map update failed:", mapError);
                appendMessage("Results loaded, but the map could not update.", "error");
            }
        }
    } catch (error) {
        appendMessage("Network error. Please try again.", "error");
        console.error(error);
    } finally {
        submitButton.disabled = false;
    }
}

async function loadLayerExamples() {
    const config = window.GIS_CHATBOT_CONFIG;
    try {
        const response = await fetch(config.layersUrl);
        const data = await response.json();
        const examples = (data.layers || [])
            .flatMap((layer) => layer.examples || [])
            .filter(Boolean);
        renderSuggestions(examples.length ? examples.slice(0, 4) : window.GisMap.getDefaultSuggestions());
    } catch (error) {
        renderSuggestions(window.GisMap.getDefaultSuggestions());
    }
}

document.addEventListener("DOMContentLoaded", () => {
    appendMessage(
        "Hello! Ask about a parcel PIN, address, owner, street, or subdivision.",
        "bot"
    );
    loadLayerExamples();

    window.addEventListener("gis-parcel-selected", (event) => {
        setActiveResultItem(event.detail?.pin || "");
    });

    document.getElementById("chat-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const input = document.getElementById("chat-input");
        const message = input.value.trim();
        if (!message) {
            return;
        }
        input.value = "";
        await submitQuery(message);
    });
});
