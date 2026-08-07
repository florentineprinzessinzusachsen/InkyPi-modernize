"""Settings pages, save, import/export, API keys, isolation, and safe-reset route handlers."""

import os
import re
import unicodedata
from typing import Any
from zoneinfo import available_timezones

from flask import current_app, redirect, render_template, request

import blueprints.apikeys as _apikeys_mod
import blueprints.settings as _mod
from app_setup.logging_setup import configure_log_timezone
from utils.backend_errors import ClientInputError, route_error_boundary
from utils.config_schema import ConfigValidationError, validate_device_config
from utils.http_utils import json_error, json_success
from utils.time_utils import calculate_seconds

_DEVICE_NAME_MAX_LEN = 64

# The fixed set of well-known provider keys shown as their own cards.
# Anything else in .env (that isn't an internal app secret) is a "custom
# secret" - the free-form mechanism plugins like calendar_auth rely on for
# per-instance credentials (e.g. CALENDAR_AUTH_PASSWORD_<LABEL>).
_FIXED_PROVIDER_KEYS = (
    "OPEN_AI_SECRET",
    "OPEN_WEATHER_MAP_SECRET",
    "NASA_SECRET",
    "UNSPLASH_ACCESS_KEY",
    "GITHUB_SECRET",
    "GOOGLE_AI_SECRET",
)
_CUSTOM_KEY_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _isolation_state() -> tuple[Any, list[Any], set[Any]]:
    device_config = current_app.config["DEVICE_CONFIG"]
    isolated = device_config.get_config("isolated_plugins", default=[])
    if not isinstance(isolated, list):
        isolated = []
    registered_ids = {plugin["id"] for plugin in device_config.get_plugins()}
    return device_config, isolated, registered_ids


def _validated_isolation_plugin_id(registered_ids: set[Any]) -> str:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ClientInputError("Request body must be a JSON object", status=400)
    plugin_id = body.get("plugin_id")
    if not isinstance(plugin_id, str) or not plugin_id.strip():
        raise ClientInputError(
            "plugin_id is required and must be a non-empty string",
            status=422,
            code="validation_error",
            field="plugin_id",
        )
    normalized_plugin_id = plugin_id.strip()
    if normalized_plugin_id not in registered_ids:
        raise ClientInputError(
            "plugin_id must reference a registered plugin",
            status=422,
            code="validation_error",
            field="plugin_id",
        )
    return normalized_plugin_id


def plugin_isolation() -> Any:
    _, isolated, _ = _isolation_state()
    return json_success(isolated_plugins=sorted(set(isolated)))


def add_plugin_isolation() -> Any:
    with route_error_boundary(
        "add plugin isolation",
        logger=_mod.logger,
        hint="Provide a valid registered plugin_id in a JSON object.",
    ):
        device_config, isolated, registered_ids = _isolation_state()
        normalized_plugin_id = _validated_isolation_plugin_id(registered_ids)

        if normalized_plugin_id not in isolated:
            isolated.append(normalized_plugin_id)
            device_config.update_value(
                "isolated_plugins", sorted(set(isolated)), write=True
            )
        return json_success(isolated_plugins=sorted(set(isolated)))


def remove_plugin_isolation() -> Any:
    with route_error_boundary(
        "remove plugin isolation",
        logger=_mod.logger,
        hint="Provide a valid registered plugin_id in a JSON object.",
    ):
        device_config, isolated, registered_ids = _isolation_state()
        normalized_plugin_id = _validated_isolation_plugin_id(registered_ids)

        isolated = [p for p in isolated if p != normalized_plugin_id]
        device_config.update_value(
            "isolated_plugins", sorted(set(isolated)), write=True
        )
        return json_success(isolated_plugins=sorted(set(isolated)))


_mod.settings_bp.add_url_rule(
    "/settings/isolation", view_func=plugin_isolation, methods=["GET"]
)
_mod.settings_bp.add_url_rule(
    "/settings/isolation", view_func=add_plugin_isolation, methods=["POST"]
)
_mod.settings_bp.add_url_rule(
    "/settings/isolation", view_func=remove_plugin_isolation, methods=["DELETE"]
)


@_mod.settings_bp.route("/settings/safe_reset", methods=["POST"])  # type: ignore[untyped-decorator]
def safe_reset() -> Any:
    with route_error_boundary(
        "safe reset",
        logger=_mod.logger,
        hint="Check config readability and write permissions.",
    ):
        device_config = current_app.config["DEVICE_CONFIG"]
        config = device_config.get_config().copy()
        keep = {
            "playlist_config": config.get("playlist_config"),
            "plugins_enabled": config.get("plugins_enabled"),
            "name": config.get("name"),
            "timezone": config.get("timezone"),
            "time_format": config.get("time_format"),
            "display_type": config.get("display_type"),
            "resolution": config.get("resolution"),
            "orientation": config.get("orientation"),
            "preview_size_mode": config.get("preview_size_mode"),
        }
        # Reset selected runtime controls to safe defaults while preserving plugins/playlists.
        keep["plugin_cycle_interval_seconds"] = 3600
        keep["log_system_stats"] = False
        keep["isolated_plugins"] = []
        device_config.update_config(keep)
        return json_success(message="Safe reset applied.")


@_mod.settings_bp.route("/settings", methods=["GET"])  # type: ignore[untyped-decorator]
def settings_page() -> Any:
    device_config = current_app.config["DEVICE_CONFIG"]
    timezones = sorted(available_timezones())
    return render_template(
        "settings.html",
        device_settings=device_config.get_config(),
        timezones=timezones,
        active_nav="settings",
    )


@_mod.settings_bp.route("/settings/diagnostics", methods=["GET"])  # type: ignore[untyped-decorator]
def diagnostics_redirect() -> Any:
    """Redirect /settings/diagnostics to the Diagnostics accordion on /settings.

    Diagnostics is an accordion embedded in the main settings page rather than
    a standalone page. Users who bookmark or follow direct links to
    ``/settings/diagnostics`` previously hit a 404 (JTN-627); redirect them to
    the anchor on the settings page instead.
    """
    return redirect("/settings#diagnostics", code=302)


@_mod.settings_bp.route("/settings/backup", methods=["GET"])  # type: ignore[untyped-decorator]
def backup_restore_page() -> Any:
    device_config = current_app.config["DEVICE_CONFIG"]
    # For now, reuse the main settings page and anchor to a section; separate template can be added later
    return render_template(
        "settings.html",
        device_settings=device_config.get_config(),
        timezones=sorted(available_timezones()),
        active_nav="settings",
    )


def _include_export_keys() -> bool:
    """Allow keyed exports only on POST requests."""
    if request.method != "POST":
        return False

    body = request.get_json(silent=True)
    if isinstance(body, dict):
        value = body.get("include_keys")
    else:
        value = request.form.get("include_keys")

    if isinstance(value, bool):
        return value

    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


@_mod.settings_bp.route("/settings/export", methods=["GET"])  # type: ignore[untyped-decorator]
@_mod.settings_bp.route(  # type: ignore[untyped-decorator]
    "/settings/export", methods=["POST"], endpoint="export_settings_post"
)
def export_settings() -> Any:
    with route_error_boundary(
        "export settings",
        logger=_mod.logger,
        hint="Check config readability.",
    ):
        include_keys = _include_export_keys()
        device_config = current_app.config["DEVICE_CONFIG"]

        # Build export object with config plus env keys when requested
        data = {
            "config": device_config.get_config(),
        }
        if include_keys:
            # Export every provider/custom secret in .env, not just the fixed
            # provider cards - otherwise plugins that rely on free-form custom
            # secrets (e.g. calendar_auth's CALENDAR_AUTH_PASSWORD_<LABEL>,
            # set via /settings/api-keys' "Custom secrets" section) silently
            # never round-trip through a backup. Internal app secrets
            # (SECRET_KEY, etc.) are still excluded.
            env_path = _apikeys_mod.get_env_path()
            keys = {
                key: value
                for key, value in _apikeys_mod.parse_env_file(env_path)
                if value and key not in _apikeys_mod._INTERNAL_KEYS
            }
            data["env_keys"] = keys

        # JSON response for now; a file download route can be added if needed
        return json_success(data=data)


def _extract_import_payload() -> dict[str, Any] | None:
    payload = None
    if request.is_json:
        payload = request.get_json(silent=True)
    if payload is None:
        file = request.files.get("file")
        if file:
            import json as _json

            try:
                payload = _json.loads(file.stream.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return None
    if not payload or not isinstance(payload, dict):
        return None
    return dict(payload)


@_mod.settings_bp.route("/settings/import", methods=["POST"])  # type: ignore[untyped-decorator]
def import_settings() -> Any:
    with route_error_boundary(
        "import settings",
        logger=_mod.logger,
        hint="Verify JSON structure and file permissions.",
    ):
        device_config = current_app.config["DEVICE_CONFIG"]
        # Accept JSON body or form upload with a JSON file
        payload = _extract_import_payload()
        if payload is None:
            raise ClientInputError("Invalid import payload", status=400)

        cfg_keys_applied = 0
        cfg = payload.get("config")
        if isinstance(cfg, dict):
            # Filter to allowed keys only
            filtered_cfg = {
                k: v for k, v in cfg.items() if k in _mod._ALLOWED_IMPORT_CONFIG_KEYS
            }
            if filtered_cfg:
                # playlist_config/refresh_info get deserialized into live
                # PlaylistManager/RefreshInfo model objects (see
                # Config.update_config) — validate shape against the device
                # config schema first so a malformed backup fails cleanly
                # with a 400 instead of raising deep inside model parsing.
                try:
                    validate_device_config(filtered_cfg)
                except ConfigValidationError as exc:
                    raise ClientInputError(
                        f"Invalid backup contents: {exc}", status=400
                    ) from exc
                device_config.update_config(filtered_cfg)
                cfg_keys_applied = len(filtered_cfg)

        env_keys_applied = 0
        env_keys = payload.get("env_keys") or {}
        if isinstance(env_keys, dict):
            for k, v in env_keys.items():
                # Same validation as /settings/save_api_keys' custom-secret
                # path: any safe-looking env-var name that isn't an internal
                # app secret is importable, not just the 6 fixed providers -
                # otherwise a correctly-exported custom secret (e.g.
                # calendar_auth's CALENDAR_AUTH_PASSWORD_<LABEL>) would still
                # get silently dropped on restore.
                if (
                    not isinstance(k, str)
                    or not _CUSTOM_KEY_NAME_RE.match(k)
                    or k in _apikeys_mod._INTERNAL_KEYS
                    or v is None
                ):
                    continue
                str_v = str(v)
                if _apikeys_mod._has_invalid_control_chars(str_v):
                    _mod.logger.warning(
                        "Skipped env key during import: %s (invalid control "
                        "characters in value)",
                        k,
                    )
                    continue
                try:
                    device_config.set_env_key(k, str_v)
                    env_keys_applied += 1
                except Exception:
                    _mod.logger.exception("Failed setting env key during import: %s", k)

        if cfg_keys_applied == 0 and env_keys_applied == 0:
            raise ClientInputError(
                "Backup contained no recognizable settings or API keys",
                status=400,
            )

        parts = []
        if cfg_keys_applied:
            parts.append(
                f"{cfg_keys_applied} setting{'s' if cfg_keys_applied != 1 else ''}"
            )
        if env_keys_applied:
            parts.append(
                f"{env_keys_applied} API key{'s' if env_keys_applied != 1 else ''}"
            )
        return json_success(message=f"Restored {' and '.join(parts)}")


def _mask_key_value(value: str | None) -> str | None:
    if not value:
        return None
    try:
        if len(value) >= 4:
            return f"...{value[-4:]} ({len(value)} chars)"
        return f"set ({len(value)} chars)"
    except Exception:
        return "set"


@_mod.settings_bp.route("/settings/api-keys", methods=["GET"])  # type: ignore[untyped-decorator]
def api_keys_page() -> Any:
    device_config = current_app.config["DEVICE_CONFIG"]

    masked = {
        key: _mask_key_value(device_config.load_env_key(key))
        for key in _FIXED_PROVIDER_KEYS
    }
    api_key_plugins = {
        "OPEN_AI_SECRET": ["AI Image", "AI Text"],
        "OPEN_WEATHER_MAP_SECRET": ["Weather"],
        "NASA_SECRET": ["NASA APOD"],
        "UNSPLASH_ACCESS_KEY": ["Unsplash Background"],
        "GITHUB_SECRET": ["GitHub"],
        "GOOGLE_AI_SECRET": ["AI Image", "AI Text"],
    }

    # Custom secrets: any .env entry that isn't one of the fixed providers
    # above or an internal app secret. This is the free-form mechanism
    # plugins like calendar_auth rely on for per-instance credentials
    # (e.g. CALENDAR_AUTH_PASSWORD_<LABEL>) that can't be declared as a
    # single static provider key. Rendered as cards via the same
    # api_key_card macro as the fixed providers (hence sharing one `masked`
    # dict), reusing blueprints.apikeys' env-file read helpers rather than
    # duplicating them.
    env_path = _apikeys_mod.get_env_path()
    all_entries = _apikeys_mod.parse_env_file(env_path)
    fixed_keys = set(_FIXED_PROVIDER_KEYS)
    custom_key_names = [
        key
        for key, _value in all_entries
        if key not in fixed_keys and key not in _apikeys_mod._INTERNAL_KEYS
    ]
    for key, value in all_entries:
        if key in custom_key_names:
            masked[key] = _mask_key_value(value)

    return render_template(
        "api_keys.html",
        custom_key_names=custom_key_names,
        env_exists=os.path.exists(env_path),
        masked=masked,
        api_key_plugins=api_key_plugins,
        active_nav="api-keys",
    )


@_mod.settings_bp.route("/settings/api-keys/status", methods=["GET"])  # type: ignore[untyped-decorator]
def api_keys_status() -> Any:
    """Live presence check for one or more env-var keys, by name.

    Pages that render an API key's presence at load time (e.g. the plugin
    editor's action buttons) go stale the moment the key is added or removed
    from a *different* tab. This lets them re-check freshly, right before an
    action that needs the key, instead of trusting a page-load snapshot.
    Booleans only — never returns key values.
    """
    device_config = current_app.config["DEVICE_CONFIG"]
    requested = request.args.get("keys", "")
    names = [k.strip() for k in requested.split(",") if k.strip()]
    status: dict[str, bool] = {}
    for name in names:
        if not _CUSTOM_KEY_NAME_RE.match(name) or name in _apikeys_mod._INTERNAL_KEYS:
            continue
        status[name] = device_config.load_env_key(name) is not None
    return json_success(data=status)


def _resolve_key_value_for_save(key: str, value: str) -> tuple[str | None, bool]:
    """Return (cleaned_value, was_placeholder) for a submitted key value.

    cleaned_value is None when the field should be left unchanged
    (empty/whitespace-only, or a stale bullet-placeholder value - JTN-598).

    Raises ClientInputError if the value contains newlines/control
    characters, which `dotenv.set_key(..., quote_mode="never")` would
    otherwise write verbatim into .env, letting an embedded "\\nKEY=value"
    inject arbitrary additional env vars (including internal secrets).
    """
    if not value:
        return None, False
    stripped = value.strip()
    if not stripped:
        return None, False
    if set(stripped) <= {"•"}:
        _mod.logger.warning(
            "Rejected save_api_keys value for %s: value is only U+2022 "
            "placeholder characters (likely a stale cached page or a "
            "client that appended to the legacy bullet pre-fill).",
            key,
        )
        return None, True
    if _apikeys_mod._has_invalid_control_chars(value):
        raise ClientInputError(f"Invalid characters in value for {key}", status=400)
    return value, False


@_mod.settings_bp.route("/settings/save_api_keys", methods=["POST"])  # type: ignore[untyped-decorator]
def save_api_keys() -> Any:
    device_config = current_app.config["DEVICE_CONFIG"]
    with route_error_boundary(
        "saving API keys",
        logger=_mod.logger,
        hint="Ensure .env is writable and values are valid; check disk space and permissions.",
    ):
        form_data = request.form.to_dict()
        updated = []
        skipped_placeholder = []

        for key in _FIXED_PROVIDER_KEYS:
            raw_value = form_data.get(key)
            if raw_value is None:
                continue
            value, was_placeholder = _resolve_key_value_for_save(key, raw_value)
            if was_placeholder:
                skipped_placeholder.append(key)
                continue
            if value is None:
                continue
            device_config.set_env_key(key, value)
            updated.append(key)

        # Custom secrets: any other submitted field that looks like a safe
        # env-var name and isn't an internal app secret. New custom-secret
        # cards have no `name` attribute on their key-name input - only the
        # paired value input's `name` is set (dynamically, client-side) to
        # the typed key - so this naturally only ever sees real candidates,
        # never the raw "what did the user type as a name" field itself.
        for key, raw_value in form_data.items():
            if key in _FIXED_PROVIDER_KEYS:
                continue
            if not _CUSTOM_KEY_NAME_RE.match(key):
                continue
            if key in _apikeys_mod._INTERNAL_KEYS:
                continue
            value, was_placeholder = _resolve_key_value_for_save(key, raw_value)
            if was_placeholder:
                skipped_placeholder.append(key)
                continue
            if value is None:
                continue
            device_config.set_env_key(key, value)
            updated.append(key)

        if skipped_placeholder:
            return json_success(
                message="API keys saved.",
                updated=updated,
                skipped_placeholder=skipped_placeholder,
            )
        return json_success(message="API keys saved.", updated=updated)


@_mod.settings_bp.route("/settings/delete_api_key", methods=["POST"])  # type: ignore[untyped-decorator]
def delete_api_key() -> Any:
    device_config = current_app.config["DEVICE_CONFIG"]
    key = request.form.get("key")
    if (
        not key
        or not _CUSTOM_KEY_NAME_RE.match(key)
        or key in _apikeys_mod._INTERNAL_KEYS
    ):
        raise ClientInputError("Invalid key name", status=400)
    with route_error_boundary(
        "deleting API key",
        logger=_mod.logger,
        hint="Verify .env file permissions and that the key exists.",
    ):
        device_config.unset_env_key(key)
        return json_success(message=f"Deleted {key}.")


def _field_error(message: str, field: str) -> Any:
    """Return a 422 validation error response for *field*."""
    return json_error(
        message,
        status=422,
        code="validation_error",
        details={"field": field},
    )


def _validate_interval(form_data: dict[str, str]) -> Any | None:
    """Validate and return the parsed interval, or a JSON error response."""
    interval = form_data.get("interval")
    unit = form_data.get("unit")

    if not unit or unit not in ("minute", "hour"):
        return _field_error("Plugin cycle interval unit is required", "unit")
    if not interval or not interval.strip():
        return _field_error("Refresh interval is required", "interval")
    try:
        interval_int = int(interval)
    except (ValueError, TypeError):
        return _field_error("Refresh interval must be a number", "interval")
    if interval_int < 1:
        return _field_error("Refresh interval must be at least 1", "interval")

    plugin_cycle_interval_seconds = calculate_seconds(interval_int, unit)
    if plugin_cycle_interval_seconds > 86400 or plugin_cycle_interval_seconds <= 0:
        return _field_error(
            "Plugin cycle interval must be less than 24 hours", "interval"
        )
    return None


def _validate_device_name(form_data: dict[str, str]) -> tuple[str | None, Any | None]:
    """Validate and normalize the submitted device name."""
    raw_device_name = form_data.get("deviceName", "")
    device_name = raw_device_name.strip()
    if not device_name:
        return None, _field_error("Device Name is required", "deviceName")
    if len(raw_device_name) > _DEVICE_NAME_MAX_LEN:
        return None, _field_error(
            f"Device Name must be {_DEVICE_NAME_MAX_LEN} characters or fewer",
            "deviceName",
        )
    if any(unicodedata.category(ch) == "Cc" and ch != "\t" for ch in raw_device_name):
        return None, _field_error(
            "Device Name may not contain control characters",
            "deviceName",
        )
    return device_name, None


def _validate_enum_field(
    form_data: dict[str, str],
    field: str,
    allowed: tuple[str, ...],
    *,
    required: bool = True,
) -> Any | None:
    """Validate that *field* is one of *allowed* values.

    When *required* is True the field must be present and non-empty.
    When False the field is only checked if present.
    """
    value = form_data.get(field)
    if required:
        if not value or value not in allowed:
            return _field_error(
                f"{field} is required",
                field,
            )
    else:
        if value is not None and value not in allowed:
            return _field_error(
                f"{field} must be one of {', '.join(repr(a) for a in allowed)}",
                field,
            )
    return None


def _validate_image_settings(form_data: dict[str, str]) -> Any | None:
    """Validate numeric image-adjustment fields in *form_data*."""
    import math

    _IMAGE_SETTING_MIN = 0.0
    _IMAGE_SETTING_MAX = 10.0
    for field in (
        "saturation",
        "brightness",
        "sharpness",
        "contrast",
        "inky_saturation",
    ):
        raw = form_data.get(field)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (ValueError, TypeError):
            return _field_error(f"Invalid numeric value for {field}", field)
        if not math.isfinite(value):
            return _field_error(f"Invalid numeric value for {field}", field)
        if value < _IMAGE_SETTING_MIN or value > _IMAGE_SETTING_MAX:
            return _field_error(
                f"{field} must be between {_IMAGE_SETTING_MIN} and {_IMAGE_SETTING_MAX}",
                field,
            )
    return None


def _validate_history_retention(form_data: dict[str, str]) -> Any | None:
    """Validate the history auto-cleanup retention value/unit, when enabled."""
    if form_data.get("historyCleanupEnabled") != "on":
        return None

    unit = form_data.get("historyRetentionUnit")
    if unit not in ("hours", "days"):
        return _field_error(
            "History retention unit must be hours or days", "historyRetentionUnit"
        )

    raw_value = form_data.get("historyRetentionValue")
    if not raw_value or not raw_value.strip():
        return _field_error(
            "History retention value is required", "historyRetentionValue"
        )
    try:
        value = int(raw_value)
    except (ValueError, TypeError):
        return _field_error(
            "History retention value must be a whole number", "historyRetentionValue"
        )
    if value < 1:
        return _field_error(
            "History retention value must be at least 1", "historyRetentionValue"
        )
    return None


def _validate_settings_form(form_data: dict[str, str]) -> tuple[Any | None, str | None]:
    """Validate settings form data and return any error plus normalized fields."""
    normalized_device_name, err = _validate_device_name(form_data)
    if err:
        return err, None

    err = _validate_interval(form_data)
    if err:
        return err, None

    # Timezone
    timezone_name = form_data.get("timezoneName")
    if not timezone_name:
        return _field_error("Time Zone is required", "timezoneName"), None
    if timezone_name not in available_timezones():
        return (
            _field_error(
                "Time Zone must be a valid IANA timezone (e.g. UTC, America/New_York)",
                "timezoneName",
            ),
            None,
        )

    time_format = form_data.get("timeFormat")
    if not time_format or time_format not in ("12h", "24h"):
        return _field_error("Time format is required", "timeFormat"), None

    err = _validate_enum_field(
        form_data, "orientation", ("horizontal", "vertical"), required=False
    )
    if err:
        return err, None
    err = _validate_enum_field(
        form_data, "previewSizeMode", ("native", "scaled", "fit"), required=False
    )
    if err:
        return err, None

    err = _validate_history_retention(form_data)
    if err:
        return err, None

    return _validate_image_settings(form_data), normalized_device_name


def _build_settings_dict(
    form_data: dict[str, str],
    normalized_device_name: str,
    device_config: Any,
) -> tuple[dict[str, Any], int]:
    """Build the persisted settings payload from validated form data."""
    unit = form_data.get("unit")
    interval = form_data.get("interval")
    assert interval is not None
    assert unit is not None
    plugin_cycle_interval_seconds = calculate_seconds(int(interval), unit)

    image_settings: dict[str, float] = {
        "saturation": float(form_data.get("saturation", "1.0")),
        "brightness": float(form_data.get("brightness", "1.0")),
        "sharpness": float(form_data.get("sharpness", "1.0")),
        "contrast": float(form_data.get("contrast", "1.0")),
    }
    # Preserve any existing max_count/min_free_bytes overrides (not exposed
    # in this form) rather than dropping them on every settings save.
    existing_history_cleanup = device_config.get_config("history_cleanup")
    history_cleanup: dict[str, Any] = (
        dict(existing_history_cleanup)
        if isinstance(existing_history_cleanup, dict)
        else {}
    )
    history_cleanup["enabled"] = form_data.get("historyCleanupEnabled") == "on"
    history_cleanup["retention_unit"] = form_data.get("historyRetentionUnit", "days")
    try:
        history_cleanup["retention_value"] = int(
            form_data.get("historyRetentionValue", "30")
        )
    except (TypeError, ValueError):
        history_cleanup["retention_value"] = 30

    settings: dict[str, Any] = {
        "name": normalized_device_name,
        "orientation": form_data.get("orientation"),
        "inverted_image": form_data.get("invertImage") == "on",
        "log_system_stats": form_data.get("logSystemStats") == "on",
        "timezone": form_data.get("timezoneName"),
        "time_format": form_data.get("timeFormat"),
        "plugin_cycle_interval_seconds": plugin_cycle_interval_seconds,
        "image_settings": image_settings,
        "preview_size_mode": form_data.get("previewSizeMode", "native"),
        "history_enabled": form_data.get("historyEnabled") == "on",
        "history_cleanup": history_cleanup,
    }
    if "inky_saturation" in form_data:
        image_settings["inky_saturation"] = float(
            form_data.get("inky_saturation", "0.5")
        )
    return settings, plugin_cycle_interval_seconds


@_mod.settings_bp.route("/save_settings", methods=["POST"])  # type: ignore[untyped-decorator]
def save_settings() -> Any:
    device_config = current_app.config["DEVICE_CONFIG"]

    with route_error_boundary(
        "saving device settings",
        logger=_mod.logger,
        hint="Check numeric values and config file permissions.",
    ):
        form_data = request.form.to_dict()

        error, normalized_device_name = _validate_settings_form(form_data)
        if error:
            return error
        assert normalized_device_name is not None

        previous_interval_seconds = device_config.get_config(
            "plugin_cycle_interval_seconds"
        )
        settings, plugin_cycle_interval_seconds = _build_settings_dict(
            form_data, normalized_device_name, device_config
        )
        device_config.update_config(settings)
        configure_log_timezone(settings.get("timezone"))

        if plugin_cycle_interval_seconds != previous_interval_seconds:
            # wake the background thread up to signal interval config change
            refresh_task = current_app.config["REFRESH_TASK"]
            refresh_task.signal_config_change()
    return json_success(message="Saved settings.")


# Legacy route aliases used by older UI/tests.
def device_settings_page() -> Any:
    return settings_page()


def save_device_settings() -> Any:
    return save_settings()


def display_settings_page() -> Any:
    return settings_page()


def save_display_settings() -> Any:
    return save_settings()


def network_settings_page() -> Any:
    return settings_page()


def save_network_settings() -> Any:
    return save_settings()


_mod.settings_bp.add_url_rule(
    "/settings/device", view_func=device_settings_page, methods=["GET"]
)
_mod.settings_bp.add_url_rule(
    "/settings/device", view_func=save_device_settings, methods=["POST"]
)
_mod.settings_bp.add_url_rule(
    "/settings/display", view_func=display_settings_page, methods=["GET"]
)
_mod.settings_bp.add_url_rule(
    "/settings/display", view_func=save_display_settings, methods=["POST"]
)
_mod.settings_bp.add_url_rule(
    "/settings/network", view_func=network_settings_page, methods=["GET"]
)
_mod.settings_bp.add_url_rule(
    "/settings/network", view_func=save_network_settings, methods=["POST"]
)
