# Rowan County GIS Chatbot

Public-facing web app for querying Rowan County GIS parcel data using natural-language-style questions. Results appear in a chat panel and on an interactive map using the county [Public/Basemap](https://gis.rowancountync.gov/arcgis/rest/services/Public/Basemap/MapServer) service.

## Phase 1 features

- Pattern-based query parser (PIN, address, owner, street) — no LLM required
- ArcGIS REST queries against `Public/RowanTaxParcels`
- Map zoom and highlight on query results
- SQLite chat history for staff review at `/admin/queries`
- Docker + rowan-webapp-template deploy pattern

## Quick start (local)

```powershell
cd G:\Automate\Cursor\rowan-gis-chatbot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python scripts\init_db.py
set PORT=5002
set ADMIN_AUTH_ENABLED=false
python app.py
```

Open http://localhost:5002

## Docker

```powershell
docker compose up --build
```

Chat history persists in `./data/chatbot.db`.

## Example questions

- `PIN 1234567890`
- `123 Main St Salisbury`
- `Find parcels owned by Smith`
- `Show parcels on Oak Street`

## Admin query log

Browse logged public questions at `/admin/queries`. When `ADMIN_AUTH_ENABLED=true`, staff must sign in with Azure AD (allowlist).

Export unmatched queries as CSV to plan new features or refine LLM prompts in Phase 2.

## Environment variables

See `.env.example` for the full list. Key values:

| Variable | Purpose |
|----------|---------|
| `ARCGIS_BASEMAP_URL` | Rowan County basemap MapServer |
| `PARCEL_LAYER_URL` | Parcel query layer |
| `CHAT_DB_PATH` | SQLite log database path |
| `ADMIN_AUTH_ENABLED` | Require Azure AD for admin pages |
| `AZURE_OPENAI_*` | Phase 2 LLM integration (optional) |

## Project layout

```
rowan-gis-chatbot/
├── app.py                 # Flask routes
├── services/
│   ├── arcgis_client.py   # ArcGIS REST queries
│   ├── query_parser.py    # Phase 1 intent parser
│   └── chat_log.py        # SQLite history
├── templates/chat.html
├── static/js/map.js       # ArcGIS JS map
└── data/chatbot.db        # Query log (gitignored)
```
