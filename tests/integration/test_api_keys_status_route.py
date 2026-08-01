"""Tests for GET /settings/api-keys/status.

This endpoint powers a live (not page-load-cached) API-key presence check —
used by the plugin editor so its action buttons can re-verify a required key
right before an action, instead of trusting whatever was true when the page
was first rendered (see the plugin-page live-check feature).
"""


def test_status_reports_present_and_missing_keys(client, device_config_dev):
    device_config_dev.set_env_key("OPEN_AI_SECRET", "sk-test")
    device_config_dev.unset_env_key("NASA_SECRET")

    resp = client.get("/settings/api-keys/status?keys=OPEN_AI_SECRET,NASA_SECRET")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["OPEN_AI_SECRET"] is True
    assert data["NASA_SECRET"] is False


def test_status_reflects_key_added_after_initial_check(client, device_config_dev):
    """The whole point: a second check after a key is added must flip to True."""
    device_config_dev.unset_env_key("NASA_SECRET")
    first = client.get("/settings/api-keys/status?keys=NASA_SECRET").get_json()["data"]
    assert first["NASA_SECRET"] is False

    device_config_dev.set_env_key("NASA_SECRET", "nasa-key")
    second = client.get("/settings/api-keys/status?keys=NASA_SECRET").get_json()["data"]
    assert second["NASA_SECRET"] is True


def test_status_never_returns_key_values(client, device_config_dev):
    device_config_dev.set_env_key("OPEN_AI_SECRET", "sk-super-secret-value")
    resp = client.get("/settings/api-keys/status?keys=OPEN_AI_SECRET")
    body = resp.get_data(as_text=True)
    assert "sk-super-secret-value" not in body


def test_status_ignores_internal_and_malformed_key_names(client, device_config_dev):
    resp = client.get(
        "/settings/api-keys/status?keys=SECRET_KEY,not-a-valid-name,OPEN_AI_SECRET"
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert "SECRET_KEY" not in data
    assert "not-a-valid-name" not in data
    assert "OPEN_AI_SECRET" in data


def test_status_empty_keys_param_returns_empty_object(client):
    resp = client.get("/settings/api-keys/status")
    assert resp.status_code == 200
    assert resp.get_json()["data"] == {}


def test_status_is_get_only(client):
    resp = client.post("/settings/api-keys/status")
    assert resp.status_code == 405
