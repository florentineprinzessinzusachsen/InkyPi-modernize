"""API documentation blueprint — Swagger UI and OpenAPI spec endpoints."""

from __future__ import annotations

import json
import os

from flask import Blueprint, Response, render_template

api_docs_bp = Blueprint("api_docs", __name__)

# Resolved at import time — always relative to this module's location.
_SPEC_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "static", "openapi.json")
)


@api_docs_bp.route("/api/docs", methods=["GET"])  # type: ignore
def swagger_ui() -> Response:
    """Serve the Swagger UI HTML page pointing at /api/openapi.json.

    Rendered through Jinja (rather than a raw HTML string) so the inline
    initialisation ``<script>`` can carry the per-request ``csp_nonce`` and
    satisfy the app's default ``script-src 'self' 'nonce-{nonce}'`` CSP.
    swagger-ui-dist's CSS/JS are vendored locally under
    ``static/vendor/swagger-ui/`` (matching the ``vendor/leaflet`` pattern)
    so they're covered by ``script-src 'self'``/``style-src 'self'`` with no
    CSP allow-list changes needed.
    """
    return render_template("api_docs.html")


@api_docs_bp.route("/api/openapi.json", methods=["GET"])  # type: ignore
def openapi_spec() -> Response:
    """Serve the OpenAPI 3.0 spec as JSON."""
    with open(_SPEC_PATH, encoding="utf-8") as f:
        spec = json.load(f)
    return Response(
        json.dumps(spec, indent=2),
        status=200,
        mimetype="application/json",
    )
