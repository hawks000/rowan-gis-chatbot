#!/usr/bin/env python3
"""
Rowan County GIS Chatbot
Public-facing chat interface for querying county GIS parcel data.
"""

import csv
import io
import logging
import os
import time
from functools import wraps

import requests
from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from auth import create_auth_manager
from services.arcgis_client import (
    ArcGISQueryError,
    get_layer_catalog,
    query_layer,
    query_layer_at_point,
    summarize_features,
)
from services.chat_log import get_summary_stats, init_db, list_queries, log_query, set_needs_feature
from services.nconemap_geocoder import GeocodeError, geocode_address
from services.query_parser import build_where_clause, parse_query

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-fallback-key-change-in-production")

auth_manager = create_auth_manager()
auth_enabled = os.getenv("AUTH_ENABLED", "False").lower() == "true"
admin_auth_enabled = os.getenv("ADMIN_AUTH_ENABLED", "True").lower() == "true"

ALLOWED_TENANT_ID = os.getenv("ALLOWED_TENANT_ID", "977b42ab-7737-4552-86e7-b09ed296213d")
ALLOWED_EMAIL_DOMAIN = os.getenv("ALLOWED_EMAIL_DOMAIN", "@rowancountync.gov")
AUTH_MODE = os.getenv("AUTH_MODE", "allowlist")
ALLOWED_USERS = [
    email.strip().lower()
    for email in os.getenv("ALLOWED_USERS", "").split(",")
    if email.strip()
]

ARCGIS_BASEMAP_URL = os.getenv(
    "ARCGIS_BASEMAP_URL",
    "https://gis.rowancountync.gov/arcgis/rest/services/Public/Basemap/MapServer",
)

logger.info(
    "GIS Chatbot starting - AUTH_ENABLED: %s, ADMIN_AUTH_ENABLED: %s",
    auth_enabled,
    admin_auth_enabled,
)

init_db()


def is_user_authorized(id_token_claims):
    """Check if user is authorized based on AUTH_MODE."""
    if not id_token_claims:
        return False

    tenant_id = id_token_claims.get("tid", "")
    user_email = id_token_claims.get("preferred_username", "Unknown")
    user_name = id_token_claims.get("name", "Unknown")

    if tenant_id != ALLOWED_TENANT_ID:
        logger.warning("SECURITY: Unauthorized tenant - %s", tenant_id)
        return False

    if AUTH_MODE == "allowlist":
        if not ALLOWED_USERS:
            logger.warning("SECURITY: allowlist empty - denying admin access")
            return False
        if user_email.lower() not in ALLOWED_USERS:
            logger.warning("SECURITY: User not in allowlist - %s", user_email)
            return False
    elif not user_email.endswith(ALLOWED_EMAIL_DOMAIN):
        logger.warning("SECURITY: Invalid email domain - %s", user_email)
        return False

    logger.info("AUTHORIZED admin: %s (%s)", user_name, user_email)
    return True


def require_admin_auth(f):
    """Require Azure AD auth for staff admin pages (independent of public AUTH_ENABLED)."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not admin_auth_enabled:
            return f(*args, **kwargs)

        if "admin_user" not in session or not session.get("admin_authorized", False):
            session["post_login_redirect"] = request.path
            return redirect(url_for("admin_login"))

        return f(*args, **kwargs)

    return decorated_function


def _format_summary_message(intent, summaries, result_count, geocode=None):
    if result_count == 0:
        if geocode and geocode.get("address"):
            return (
                f"NC OneMap geocoded '{intent.value}' to {geocode['address']}, "
                "but no matching parcel polygon was found nearby."
            )
        return f"No parcels found for: {intent.description}."

    if result_count == 1:
        row = summaries[0]
        pin = row.get("PIN") or row.get("PARCEL_ID") or "Unknown"
        owner = row.get("OWNNAME") or "Unknown owner"
        address = row.get("PROP_ADDRESS") or row.get("TAXADD1") or "No address on file"
        city = row.get("CITY") or ""
        value = row.get("TOT_VAL")
        value_text = f" Total value: ${value:,.0f}." if isinstance(value, (int, float)) else ""
        location = f"{address}, {city}".strip(", ")
        geocode_note = ""
        if geocode and geocode.get("address"):
            geocode_note = f" Located via NC OneMap at {geocode['address']}."
        return f"Found parcel {pin} — {owner}. {location}.{value_text}{geocode_note}"

    geocode_note = ""
    if geocode and geocode.get("address"):
        geocode_note = f" Geocoded via NC OneMap: {geocode['address']}."
    return f"Found {result_count} parcels matching: {intent.description}.{geocode_note}"


def _query_parcels_for_intent(intent):
    """Run parcel query; geocode address intents via NC OneMap first."""
    geocode = None

    if intent.intent_type == "address":
        try:
            geocode = geocode_address(intent.value)
        except GeocodeError as exc:
            logger.warning("Geocoder error, falling back to attribute search: %s", exc)

        if geocode and geocode.get("location"):
            location = geocode["location"]
            geojson = query_layer_at_point(location["x"], location["y"])
            if geojson.get("features"):
                return geojson, geocode

        where = build_where_clause(intent)
        geojson = query_layer(where)
        return geojson, geocode

    where = build_where_clause(intent)
    geojson = query_layer(where)
    return geojson, geocode


@app.route("/login")
def login():
    """Legacy login route — redirects to admin login."""
    return redirect(url_for("admin_login"))


@app.route("/admin/login")
def admin_login():
    """Initiate Microsoft login for admin pages."""
    if not admin_auth_enabled:
        flash("Admin auth is disabled in this environment.", "info")
        return redirect(url_for("admin_queries"))

    if not auth_manager:
        flash("Authentication is not configured. Set Azure AD env vars.", "error")
        return render_template("error.html", error="Authentication system unavailable"), 500

    try:
        auth_result = auth_manager.build_auth_url()
        session["auth_state"] = auth_result["state"]
        session["auth_flow"] = "admin"
        return redirect(auth_result["auth_url"])
    except Exception as exc:
        logger.error("Admin login error: %s", exc)
        flash("Failed to initiate login.", "error")
        return render_template("error.html", error="Login initiation failed"), 500


@app.route("/getAToken")
def get_a_token():
    """Handle OAuth callback for admin authentication."""
    if not auth_manager:
        flash("Authentication system unavailable.", "error")
        return render_template("error.html", error="Authentication system unavailable"), 500

    try:
        authorization_code = request.args.get("code")
        state = request.args.get("state")
        error = request.args.get("error")

        if error:
            flash(f"Authentication failed: {request.args.get('error_description', error)}", "error")
            return redirect(url_for("admin_login"))

        if not state or state != session.get("auth_state"):
            flash("Invalid authentication state.", "error")
            return redirect(url_for("admin_login"))

        session.pop("auth_state", None)

        if not authorization_code:
            flash("No authorization code received.", "error")
            return redirect(url_for("admin_login"))

        token_response = auth_manager.acquire_token_by_authorization_code(authorization_code, state)
        id_token_claims = token_response.get("id_token_claims", {})

        if not is_user_authorized(id_token_claims):
            flash("You are not authorized to access admin pages.", "error")
            return redirect(url_for("index"))

        session["admin_user"] = {
            "name": id_token_claims.get("name", "Unknown"),
            "email": id_token_claims.get("preferred_username", "Unknown"),
            "id": id_token_claims.get("oid", "Unknown"),
        }
        session["admin_authorized"] = True
        session.pop("auth_flow", None)

        redirect_target = session.pop("post_login_redirect", None) or url_for("admin_queries")
        flash(f"Welcome, {session['admin_user']['name']}!", "success")
        return redirect(redirect_target)

    except Exception as exc:
        logger.error("Auth callback error: %s", exc)
        flash("Authentication failed.", "error")
        return redirect(url_for("admin_login"))


@app.route("/admin/logout")
def admin_logout():
    """Log out admin user."""
    user_name = session.get("admin_user", {}).get("name", "Unknown")
    session.pop("admin_user", None)
    session.pop("admin_authorized", None)
    logger.info("Admin logged out: %s", user_name)
    flash("Signed out of admin.", "info")

    if auth_manager and admin_auth_enabled:
        try:
            return redirect(auth_manager.get_logout_url())
        except Exception as exc:
            logger.error("Logout URL error: %s", exc)

    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    return redirect(url_for("admin_logout"))


@app.route("/")
def index():
    """Public GIS chatbot page."""
    return render_template(
        "chat.html",
        basemap_url=ARCGIS_BASEMAP_URL,
    )


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "app": "rowan-gis-chatbot"})


@app.route("/api/layers")
def api_layers():
    """Return queryable layer catalog and example prompts."""
    return jsonify({"layers": get_layer_catalog()})


@app.route("/api/query", methods=["POST"])
def api_query():
    """Parse user message, query ArcGIS REST, log interaction."""
    started = time.perf_counter()
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    session_id = (payload.get("session_id") or "anonymous").strip()[:64]

    if not message:
        return jsonify({"error": "Message is required."}), 400

    intent = parse_query(message)
    if not intent:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log_query(
            session_id=session_id,
            user_message=message,
            parse_method="none",
            intent_type=None,
            intent=None,
            status="parse_failed",
            error_message="Could not understand the question.",
            response_ms=elapsed_ms,
        )
        return jsonify(
            {
                "status": "parse_failed",
                "message": (
                    "I couldn't understand that question yet. Try one of the suggested "
                    "examples, such as a PIN, street address, owner name, or street name."
                ),
                "geojson": {"type": "FeatureCollection", "features": []},
                "summaries": [],
                "result_count": 0,
            }
        ), 422

    try:
        geojson, geocode = _query_parcels_for_intent(intent)
        summaries = summarize_features(geojson)
        result_count = len(geojson.get("features", []))
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        status = "success" if result_count else "no_results"
        log_query(
            session_id=session_id,
            user_message=message,
            parse_method="regex",
            intent_type=intent.intent_type,
            intent=intent.to_dict(),
            status=status,
            result_count=result_count,
            layer_used="RowanTaxParcels",
            response_ms=elapsed_ms,
        )

        summary_message = _format_summary_message(intent, summaries, result_count, geocode)
        return jsonify(
            {
                "status": status,
                "message": summary_message,
                "intent": intent.to_dict(),
                "geojson": geojson,
                "summaries": summaries,
                "result_count": result_count,
                "geocode": geocode,
            }
        )

    except (ArcGISQueryError, GeocodeError, requests.RequestException, ValueError) as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.error("Query failed: %s", exc)
        log_query(
            session_id=session_id,
            user_message=message,
            parse_method="regex",
            intent_type=intent.intent_type,
            intent=intent.to_dict(),
            status="error",
            error_message=str(exc),
            response_ms=elapsed_ms,
        )
        return jsonify(
            {
                "status": "error",
                "message": "Something went wrong querying GIS data. Please try again.",
                "error": str(exc),
            }
        ), 502


@app.route("/admin/queries")
@require_admin_auth
def admin_queries():
    """Staff view of logged chat queries."""
    unmatched_only = request.args.get("unmatched") == "1"
    status_filter = request.args.get("status") or None
    entries = list_queries(status=status_filter, unmatched_only=unmatched_only, limit=300)
    stats = get_summary_stats()
    return render_template(
        "admin_queries.html",
        entries=entries,
        stats=stats,
        unmatched_only=unmatched_only,
        status_filter=status_filter or "",
        admin_user=session.get("admin_user", {}),
        admin_auth_enabled=admin_auth_enabled,
    )


@app.route("/admin/queries/export")
@require_admin_auth
def admin_queries_export():
    """Export query log as CSV for LLM tuning / feature planning."""
    unmatched_only = request.args.get("unmatched") == "1"
    entries = list_queries(unmatched_only=unmatched_only, limit=5000)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "created_at",
            "session_id",
            "user_message",
            "parse_method",
            "intent_type",
            "status",
            "result_count",
            "layer_used",
            "error_message",
            "response_ms",
            "needs_feature",
        ]
    )
    for row in entries:
        writer.writerow(
            [
                row.get("id"),
                row.get("created_at"),
                row.get("session_id"),
                row.get("user_message"),
                row.get("parse_method"),
                row.get("intent_type"),
                row.get("status"),
                row.get("result_count"),
                row.get("layer_used"),
                row.get("error_message"),
                row.get("response_ms"),
                row.get("needs_feature"),
            ]
        )

    filename = "gis-chatbot-queries.csv"
    return (
        output.getvalue(),
        200,
        {
            "Content-Type": "text/csv",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.route("/admin/queries/<int:entry_id>/flag", methods=["POST"])
@require_admin_auth
def admin_flag_query(entry_id):
    """Mark a logged query as needing a new feature."""
    payload = request.get_json(silent=True) or {}
    needs_feature = bool(payload.get("needs_feature", True))
    if set_needs_feature(entry_id, needs_feature):
        return jsonify({"ok": True, "needs_feature": needs_feature})
    return jsonify({"error": "Entry not found."}), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    debug_mode = os.getenv("ENVIRONMENT", "production") == "development"
    app.run(debug=debug_mode, host="0.0.0.0", port=port)
