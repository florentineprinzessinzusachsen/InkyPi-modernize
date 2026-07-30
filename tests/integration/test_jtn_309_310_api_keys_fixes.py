"""Tests for JTN-309 (internal secrets filtered) and JTN-310 (Add Key button)."""

# --- JTN-309: internal secrets must not appear in the API Keys UI ---


def test_secret_key_not_shown_in_generic_api_keys_page(client, tmp_path, monkeypatch):
    """JTN-309: SECRET_KEY must not appear in the /settings/api-keys response."""
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET_KEY=super-secret\nCALENDAR_AUTH_PASSWORD_HOME=nasa123\n")
    monkeypatch.setattr("blueprints.apikeys.get_env_path", lambda: str(env_file))

    resp = client.get("/settings/api-keys")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "SECRET_KEY" not in html, "SECRET_KEY must be filtered from the API Keys UI"
    assert "super-secret" not in html, "The value of SECRET_KEY must never be shown"


def test_test_key_not_shown_in_generic_api_keys_page(client, tmp_path, monkeypatch):
    """JTN-309: TEST_KEY must not appear in the /settings/api-keys response."""
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_KEY=test-value\nCALENDAR_AUTH_PASSWORD_WORK=gh-token\n")
    monkeypatch.setattr("blueprints.apikeys.get_env_path", lambda: str(env_file))

    resp = client.get("/settings/api-keys")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "TEST_KEY" not in html, "TEST_KEY must be filtered from the API Keys UI"


def test_wtf_csrf_secret_key_not_shown_in_generic_api_keys_page(
    client, tmp_path, monkeypatch
):
    """JTN-309: WTF_CSRF_SECRET_KEY must not appear in the /settings/api-keys response."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "WTF_CSRF_SECRET_KEY=csrf-secret\nCALENDAR_AUTH_PASSWORD_TRIP=openai-key\n"
    )
    monkeypatch.setattr("blueprints.apikeys.get_env_path", lambda: str(env_file))

    resp = client.get("/settings/api-keys")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert (
        "WTF_CSRF_SECRET_KEY" not in html
    ), "WTF_CSRF_SECRET_KEY must be filtered from the API Keys UI"


def test_provider_keys_still_shown_after_internal_filtering(
    client, tmp_path, monkeypatch
):
    """JTN-309: filtering internal keys must not hide other custom secrets.

    Fixed provider keys (NASA_SECRET etc.) now always render as managed
    cards regardless of this custom .env, so this checks that an unrelated
    custom secret survives the internal-key filter instead.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SECRET_KEY=internal\nCALENDAR_AUTH_PASSWORD_HOME=nasa123\n"
        "CALENDAR_AUTH_PASSWORD_WORK=unsplash-token\n"
    )
    monkeypatch.setattr("blueprints.apikeys.get_env_path", lambda: str(env_file))

    resp = client.get("/settings/api-keys")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "SECRET_KEY" not in html
    assert "CALENDAR_AUTH_PASSWORD_HOME" in html
    assert "CALENDAR_AUTH_PASSWORD_WORK" in html


def test_internal_keys_constant_contains_expected_names():
    """JTN-309: _INTERNAL_KEYS frozenset must contain all known internal secrets."""
    from blueprints.apikeys import _INTERNAL_KEYS

    assert "SECRET_KEY" in _INTERNAL_KEYS
    assert "TEST_KEY" in _INTERNAL_KEYS
    assert "WTF_CSRF_SECRET_KEY" in _INTERNAL_KEYS


# --- JTN-310: Add API Key button must be wired up ---


def test_add_api_key_button_present_in_generic_page(client):
    """JTN-310: the + Add API Key button must be rendered for custom secrets."""
    resp = client.get("/settings/api-keys")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'id="addApiKeyBtn"' in html, "Add API Key button must be present in DOM"
    assert "Add API Key" in html


def test_add_row_guard_in_js(client):
    """JTN-310: api_keys_page.js addRow must guard against missing #apikeys-list."""
    resp = client.get("/static/scripts/api_keys_page.js")
    assert resp.status_code == 200
    js = resp.get_data(as_text=True)

    assert 'getElementById("apikeys-list")' in js
    assert "api_keys_page: #apikeys-list not found in DOM" in js


def test_init_wires_add_button_click_handler(client):
    """JTN-310: init() must attach a click listener on #addApiKeyBtn."""
    resp = client.get("/static/scripts/api_keys_page.js")
    assert resp.status_code == 200
    js = resp.get_data(as_text=True)

    assert 'addBtn.addEventListener("click"' in js
    assert "() => addRow()" in js
