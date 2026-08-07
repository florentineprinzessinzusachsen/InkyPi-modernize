import io
import json


def test_import_restores_plugins_in_playlist(client, device_config_dev):
    """Bug: import_settings applied playlist_config to Config.config, but
    write_config() unconditionally re-derives playlist_config from the
    in-memory (stale) PlaylistManager, silently discarding imported plugins
    before they ever reach disk."""
    backup_playlist_config = {
        "playlists": [
            {
                "name": "Default",
                "start_time": "00:00",
                "end_time": "24:00",
                "plugins": [
                    {
                        "plugin_id": "clock",
                        "name": "My Clock",
                        "plugin_settings": {"foo": "bar"},
                    }
                ],
                "current_plugin_index": 0,
            }
        ],
        "active_playlist": "Default",
    }
    payload = {"config": {"playlist_config": backup_playlist_config}}

    resp = client.post(
        "/settings/import",
        data={"file": (io.BytesIO(json.dumps(payload).encode("utf-8")), "backup.json")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True

    # The live playlist manager (what refresh_task/dashboard actually use)
    # must reflect the restored plugin...
    manager = device_config_dev.get_playlist_manager()
    restored = manager.find_plugin("clock", "My Clock")
    assert restored is not None
    assert restored.settings == {"foo": "bar"}

    # ...and it must actually be persisted to disk, not just held in memory.
    with open(device_config_dev.config_file) as f:
        on_disk = json.load(f)
    disk_plugins = on_disk["playlist_config"]["playlists"][0]["plugins"]
    assert any(p["plugin_id"] == "clock" for p in disk_plugins)


def test_import_rejects_malformed_playlist_config(client, device_config_dev):
    payload = {"config": {"playlist_config": {"playlists": "not-a-list"}}}

    resp = client.post(
        "/settings/import",
        data={"file": (io.BytesIO(json.dumps(payload).encode("utf-8")), "backup.json")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_export_excludes_api_keys_by_default(client, device_config_dev, monkeypatch):
    """Bug 1: Export should NOT include API keys unless explicitly requested."""
    device_config_dev.set_env_key("OPEN_AI_SECRET", "sk-test")
    resp = client.get("/settings/export")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    payload = data["data"]
    assert "env_keys" not in payload or not payload.get("env_keys")


def test_export_includes_api_keys_when_opted_in(client, device_config_dev, monkeypatch):
    # Arrange env keys
    device_config_dev.set_env_key("OPEN_AI_SECRET", "sk-test")
    device_config_dev.set_env_key("OPEN_WEATHER_MAP_SECRET", "owm")

    # Act
    resp = client.post("/settings/export", json={"include_keys": True})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    payload = data["data"]
    assert isinstance(payload.get("config"), dict)
    env_keys = payload.get("env_keys")
    assert env_keys and env_keys.get("OPEN_AI_SECRET") == "sk-test"
    assert env_keys.get("OPEN_WEATHER_MAP_SECRET") == "owm"


def test_export_includes_custom_secrets_when_opted_in(client, device_config_dev):
    """Custom secrets (anything in .env that isn't one of the 6 fixed
    providers or an internal app secret - e.g. calendar_auth's per-instance
    CALENDAR_AUTH_PASSWORD_<LABEL> credentials) must be exported too, not
    just the fixed provider cards."""
    device_config_dev.set_env_key("CALENDAR_AUTH_PASSWORD_WORK", "custom-secret")
    device_config_dev.set_env_key("SECRET_KEY", "internal-should-not-export")

    resp = client.post("/settings/export", json={"include_keys": True})
    assert resp.status_code == 200
    env_keys = resp.get_json()["data"]["env_keys"]
    assert env_keys.get("CALENDAR_AUTH_PASSWORD_WORK") == "custom-secret"
    assert "SECRET_KEY" not in env_keys


def test_import_restores_plugin_order_and_isolation(client, device_config_dev):
    """plugin_order/isolated_plugins/history settings are real, active
    top-level config keys (see config.py::get_plugins, refresh_task/task.py,
    display/display_manager.py) - a backup that silently drops them on
    restore reads as data loss even though the response reports success."""
    payload = {
        "config": {
            "plugin_order": ["clock", "weather"],
            "isolated_plugins": ["ai_image"],
            "history_enabled": False,
            "history_cleanup": {"enabled": True, "retention_value": 10},
        }
    }
    resp = client.post("/settings/import", json=payload)
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True
    assert device_config_dev.get_config("plugin_order") == ["clock", "weather"]
    assert device_config_dev.get_config("isolated_plugins") == ["ai_image"]
    assert device_config_dev.get_config("history_enabled") is False
    assert device_config_dev.get_config("history_cleanup") == {
        "enabled": True,
        "retention_value": 10,
    }


def test_export_excludes_api_keys_when_opted_out(client):
    resp = client.get("/settings/export?include_keys=0")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    payload = data["data"]
    assert "env_keys" not in payload or not payload["env_keys"]


def test_export_get_ignores_include_keys_query(client, device_config_dev):
    device_config_dev.set_env_key("OPEN_AI_SECRET", "sk-test")
    resp = client.get("/settings/export?include_keys=1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    payload = data["data"]
    assert "env_keys" not in payload or not payload["env_keys"]


def test_import_round_trip_updates_config_and_keys(client, device_config_dev):
    # Build an export-like payload
    cfg = device_config_dev.get_config().copy()
    cfg["name"] = "RoundTrip"
    payload = {
        "config": cfg,
        "env_keys": {"NASA_SECRET": "nasa", "UNSPLASH_ACCESS_KEY": "u"},
    }

    resp = client.post(
        "/settings/import",
        data={"file": (io.BytesIO(json.dumps(payload).encode("utf-8")), "backup.json")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    # Verify config changed
    assert device_config_dev.get_config("name") == "RoundTrip"
    # Verify keys set
    assert device_config_dev.load_env_key("NASA_SECRET") == "nasa"
    assert device_config_dev.load_env_key("UNSPLASH_ACCESS_KEY") == "u"


def test_import_ignores_unknown_config_and_env_keys(client, device_config_dev):
    """Bug 6: import_settings should filter unknown config keys and internal
    env keys, while still accepting arbitrary custom secrets (e.g.
    calendar_auth's CALENDAR_AUTH_PASSWORD_<LABEL>) - those are legitimate
    plugin data, not an unknown/dangerous key, and must round-trip through a
    backup (see also test_settings_import.py::test_import_accepts_custom_secret_env_keys).
    """
    payload = {
        "config": {
            "name": "Allowed",
            "time_format": "24h",
            "plugin_cycle_interval_seconds": 1800,
            "secret_backdoor": "should_be_ignored",
            "__class__": "should_be_ignored",
        },
        "env_keys": {
            "OPEN_AI_SECRET": "sk-ok",
            "CALENDAR_AUTH_PASSWORD_WORK": "custom-secret-ok",
            "SECRET_KEY": "should_be_ignored",
        },
    }

    resp = client.post(
        "/settings/import",
        data={"file": (io.BytesIO(json.dumps(payload).encode("utf-8")), "backup.json")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True
    assert device_config_dev.get_config("name") == "Allowed"
    # Legitimate config keys should be imported
    assert device_config_dev.get_config("time_format") == "24h"
    assert device_config_dev.get_config("plugin_cycle_interval_seconds") == 1800
    # Unknown config keys should not be set
    assert device_config_dev.get_config("secret_backdoor") is None
    assert device_config_dev.get_config("__class__") is None
    # Internal app secrets should never be settable via import
    assert device_config_dev.load_env_key("SECRET_KEY") != "should_be_ignored"
    # Fixed-provider and custom-secret env keys should both be set
    assert device_config_dev.load_env_key("OPEN_AI_SECRET") == "sk-ok"
    assert (
        device_config_dev.load_env_key("CALENDAR_AUTH_PASSWORD_WORK")
        == "custom-secret-ok"
    )
