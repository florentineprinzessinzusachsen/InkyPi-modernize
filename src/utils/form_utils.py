"""Pure utility helpers for form input validation and sanitization.

All functions in this module are pure (no Flask imports, no request globals)
so they can be unit-tested without an application context.
"""

from __future__ import annotations

import logging
from html import escape
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sanitization helpers
# ---------------------------------------------------------------------------


def sanitize_log_field(value: Any, max_len: int = 200) -> str:
    """Strip control characters from a value for safe log output.

    Removes newline, carriage-return, and null bytes that would allow log
    injection, then truncates to *max_len* characters.

    Args:
        value: The value to sanitize.  Will be coerced to ``str``.
        max_len: Maximum length of the returned string (default 200).

    Returns:
        A sanitized string safe for inclusion in log messages.
    """
    text = str(value) if not isinstance(value, str) else value
    text = text.replace("\n", "").replace("\r", "").replace("\x00", "")
    return text[:max_len]


def sanitize_response_value(value: Any) -> str:
    """Sanitize a user-controlled value before reflecting it in a JSON response.

    Applies :func:`sanitize_log_field` for control-character stripping, then
    HTML-escapes the result to prevent XSS when the string is embedded in HTML
    contexts.  Angle brackets and ampersands are escaped; quotes are left
    unescaped so JSON serialisers can still handle the string normally.

    Args:
        value: The value to sanitize.  Will be coerced to ``str``.

    Returns:
        A sanitized, HTML-escaped string.
    """
    return escape(sanitize_log_field(str(value)), quote=False)


# ---------------------------------------------------------------------------
# Validation error
# ---------------------------------------------------------------------------


class ValidationError(ValueError):
    """Raised when input fails range, type, or schema validation.

    Attributes:
        message: Human-readable description of the failure.
        field: Optional field name associated with the failure.
    """

    def __init__(self, message: str, *, field: str | None = None) -> None:
        self.message = message
        self.field: str | None = field
        super().__init__(message)


# ---------------------------------------------------------------------------
# Schema-based validation
# ---------------------------------------------------------------------------

try:
    import jsonschema as _jsonschema
except ImportError:  # pragma: no cover
    _jsonschema = None


def validate_json_schema(data: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Validate *data* against *schema* (JSON Schema draft 2020-12).

    Uses ``jsonschema`` when available; falls back to a no-op when the
    library is absent (library is listed in requirements, so this is a
    safety net only).

    Args:
        data: The dictionary to validate.
        schema: A JSON Schema dict.

    Returns:
        A list of human-readable error strings.  An empty list means
        validation passed.
    """
    if _jsonschema is None:  # pragma: no cover
        logger.debug("jsonschema not available; skipping schema validation")
        return []

    errors: list[str] = []
    try:
        validator = _jsonschema.Draft202012Validator(schema)
        for err in validator.iter_errors(data):
            try:
                path = ".".join(str(p) for p in err.path)
                msg = f"{path}: {err.message}" if path else err.message
            except Exception:
                msg = str(err)
            errors.append(msg)
    except Exception as exc:
        logger.debug("JSON schema validation encountered an error: %s", exc)
    return errors


# ---------------------------------------------------------------------------
# Plugin schema validation
# ---------------------------------------------------------------------------


def validate_plugin_required_fields(
    plugin: Any, form_data: dict[str, Any]
) -> str | None:
    """Validate required fields from a plugin schema against form data.

    Walks the ``sections`` / ``items`` tree returned by
    ``plugin.build_settings_schema()`` and checks that every field with
    ``required=True`` has a non-empty value in *form_data*.

    This is a pure extraction of the inline ``_validate_required_fields``
    helper that previously lived in ``src/blueprints/plugin.py``.

    Args:
        plugin: A plugin instance that may expose ``build_settings_schema()``.
        form_data: The parsed form values to validate against.

    Returns:
        An error message string if validation fails, or ``None`` on success.
    """
    if not hasattr(plugin, "build_settings_schema"):
        return None
    try:
        schema = plugin.build_settings_schema()
    except Exception:
        return None

    missing: list[str] = []

    def _check_items(items: list[dict[str, Any]]) -> None:
        for item in items:
            kind = item.get("kind", "")
            if kind == "row":
                _check_items(item.get("items", []))
            elif kind == "field":
                name = item.get("name", "")
                if item.get("required") and not str(form_data.get(name, "")).strip():
                    missing.append(item.get("label", name))

    for section in schema.get("sections", []):
        _check_items(section.get("items", []))

    if missing:
        return f"Required fields missing: {', '.join(missing)}"
    return None
