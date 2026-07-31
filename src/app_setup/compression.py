"""Response body compression (gzip / brotli).

Single auditable home for HTTP compression: an after_request-style step
function (``apply_response_compression``) that security_middleware.py's
existing header pipeline calls last, so it sees the final headers/body
before compressing.

Prefers brotli (better ratio) when the client's Accept-Encoding allows it
*and* the optional `brotli` package is importable; otherwise falls back to
gzip, which is always available (stdlib). Neither is a hard dependency —
this mirrors how build_assets.py treats rjsmin and image_utils.py treats
Playwright: use the better tool if present, degrade gracefully if not.

Only compresses text-ish content (HTML/CSS/JS/JSON/SVG) above a small size
floor — recompressing already-binary formats (images, fonts) wastes CPU for
no gain, and compressing tiny bodies costs more than it saves.
"""

from __future__ import annotations

import gzip
import logging

from flask import Response, request

logger = logging.getLogger(__name__)

try:
    import brotli as _brotli  # type: ignore[import-untyped]
except ImportError:
    _brotli = None

_COMPRESSIBLE_MIMETYPES = frozenset(
    {
        "text/html",
        "text/css",
        "text/javascript",
        "application/javascript",
        "application/json",
        "image/svg+xml",
        "text/plain",
        "text/xml",
        "application/xml",
    }
)

# Below this, compression overhead (CPU + the gzip/brotli container header)
# isn't worth it relative to the bytes saved.
_MIN_COMPRESS_BYTES = 500

# Content-hashed bundles under /static/dist/ (scripts/build_assets.py) are
# requested identically on every page load for as long as this process runs
# — dist/manifest.json is only read once at startup (app_setup/asset_helpers.py),
# so a running process can only ever see a fixed, small set of dist paths
# (today: one CSS + two JS bundles). Caching their compressed bytes here is
# therefore bounded by construction, not just in practice — no eviction
# logic needed. Non-hashed assets (main.css, individual scripts, rendered
# HTML/JSON) are recompressed fresh per request instead: their content can
# change without their URL changing, so caching them would risk staleness,
# and compressing a few hundred KB of text is cheap regardless.
_static_compressed_cache: dict[str, dict[str, bytes]] = {}


def _pick_content_encoding(accept_encoding: str) -> str | None:
    """Return "br" or "gzip" per what the client and this process support."""
    accept_encoding = accept_encoding.lower()
    if _brotli is not None and "br" in accept_encoding:
        return "br"
    if "gzip" in accept_encoding:
        return "gzip"
    return None


def _compress(data: bytes, encoding: str) -> bytes:
    if encoding == "br":
        return _brotli.compress(data)
    return gzip.compress(data, compresslevel=6)


def _add_vary_header(response: Response) -> None:
    existing = response.headers.get("Vary")
    if not existing:
        response.headers["Vary"] = "Accept-Encoding"
    elif "Accept-Encoding" not in existing:
        response.headers["Vary"] = f"{existing}, Accept-Encoding"


def apply_response_compression(response: Response) -> None:
    """Gzip/brotli-compress the response body in place, when it's worth it.

    Skips: responses that are already encoded, partial-content (Range)
    responses, and anything whose mimetype isn't in the compressible set
    (this alone excludes SSE's text/event-stream, so streaming responses
    are never buffered/compressed here).
    """
    if response.headers.get("Content-Encoding"):
        return
    if response.status_code == 206 or "Content-Range" in response.headers:
        return
    mimetype = (response.mimetype or "").lower()
    if mimetype not in _COMPRESSIBLE_MIMETYPES:
        return

    encoding = _pick_content_encoding(request.headers.get("Accept-Encoding", ""))
    if not encoding:
        return

    is_static_bundle = request.path.startswith("/static/dist/")
    if is_static_bundle:
        cached = _static_compressed_cache.get(request.path)
        if cached is not None and encoding in cached:
            compressed = cached[encoding]
            response.set_data(compressed)
            response.headers["Content-Encoding"] = encoding
            response.headers["Content-Length"] = str(len(compressed))
            _add_vary_header(response)
            return

    # send_file()/send_from_directory() (used for /static/*) default to
    # direct_passthrough=True, streaming the file straight to the WSGI
    # server without buffering — fine for large binary files, but we need
    # the bytes in hand to compress them. Only compressible (text) mimetypes
    # ever reach this point, and those are all small in this app.
    response.direct_passthrough = False
    data = response.get_data()
    if len(data) < _MIN_COMPRESS_BYTES:
        return

    compressed = _compress(data, encoding)
    response.set_data(compressed)
    response.headers["Content-Encoding"] = encoding
    response.headers["Content-Length"] = str(len(compressed))
    _add_vary_header(response)

    if is_static_bundle:
        _static_compressed_cache.setdefault(request.path, {})[encoding] = compressed
