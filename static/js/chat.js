function getSessionId() {
    const key = "gis-chatbot-session-id";
    let sessionId = localStorage.getItem(key);
    if (!sessionId) {
        sessionId = crypto.randomUUID();
        localStorage.setItem(key, sessionId);
    }
    return sessionId;
}

function appendMessage(text, role) {
    const container = document.getElementById("chat-messages");
    const bubble = document.createElement("div");
    bubble.className = `message ${role}`;
    bubble.textContent = text;
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
        const pin = summary.PIN || summary.PARCEL_ID || "Unknown PIN";
        const address = summary.PROP_ADDRESS || summary.TAXADD1 || "No address";
        button.innerHTML = `<strong>${pin}</strong><span>${address}</span>`;
        button.addEventListener("click", async () => {
            document.querySelectorAll(".result-item").forEach((item) => item.classList.remove("active"));
            button.classList.add("active");
            const feature = geojson.features[index];
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

        if (data.geojson) {
            if (data.geocode) {
                await window.GisMap.showGeocode(data.geocode);
            }
            await window.GisMap.showResults(data.geojson, data.summaries || []);
            renderResultList(data.summaries || [], data.geojson);
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
        "Hello! Ask about a parcel PIN, street address, owner name, or street.",
        "bot"
    );
    loadLayerExamples();

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
