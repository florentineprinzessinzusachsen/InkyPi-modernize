import re


def test_generic_api_keys_page_uses_canonical_template(client):
    resp = client.get("/settings/api-keys")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'id="themeToggle"' in html
    assert "API Keys" in html
    # "Add API Key" was the old generic-page button label; the unified page's
    # equivalent action is "+ Add Custom Secret".
    assert "Add Custom Secret" in html


def test_generic_api_keys_list_delete_button_has_aria_label(
    client, monkeypatch, tmp_path
):
    """JTN-202: list-view delete buttons include the key name in aria-label."""
    monkeypatch.setenv("PROJECT_DIR", str(tmp_path))
    env_file = tmp_path / ".env"
    env_file.write_text("MY_TEST_KEY=somevalue\n")

    resp = client.get("/settings/api-keys")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Format is now shared with the managed-provider cards: "Delete X key
    # permanently" (previously "Delete X API key", no "permanently").
    assert 'aria-label="Delete MY_TEST_KEY key permanently"' in html


def test_generic_api_keys_list_inputs_have_aria_labels(client, monkeypatch, tmp_path):
    """JTN-202: a custom secret's value input has an accessible name.

    The old separate "key name" / "value, hidden" aria-label pair described
    a since-removed list-style layout. Custom secrets now render through the
    same api_key_card macro as fixed providers: a <label for=input_id> gives
    the input its accessible name instead of a dynamic aria-label string.
    """
    monkeypatch.setenv("PROJECT_DIR", str(tmp_path))
    env_file = tmp_path / ".env"
    env_file.write_text("ANOTHER_KEY=secretvalue\n")

    resp = client.get("/settings/api-keys")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    match = re.search(
        r'<input type="password" id="([^"]+)" name="ANOTHER_KEY"', html
    )
    assert match, "Expected a password input named ANOTHER_KEY"
    input_id = match.group(1)
    assert f'for="{input_id}">ANOTHER_KEY</label>' in html, (
        f"Expected a <label for={input_id!r}> giving the input its accessible name"
    )


def test_managed_api_keys_card_delete_button_has_aria_label(client, device_config_dev):
    """JTN-202: card-view delete button includes provider label in aria-label."""
    device_config_dev.set_env_key("NASA_SECRET", "nasa-test-value")

    resp = client.get("/settings/api-keys")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'aria-label="Delete NASA APOD key permanently"' in html


def test_managed_unconfigured_provider_has_empty_input(client):
    """JTN-215: unconfigured providers render an empty password input (no masked value)."""
    resp = client.get("/settings/api-keys")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Unsplash is not configured by default — its input must have an empty value
    # so that the JS toggle-visibility button is skipped for unconfigured providers.
    assert 'id="unsplash-input"' in html
    # The masked placeholder is only set when a key is configured; empty value means
    # value="" (the attribute is omitted or blank) — NOT the bullet mask string.
    assert 'id="unsplash-input"' in html
    # Confirm the bullet-mask string is absent for the unconfigured Unsplash input.
    # We check that the mask dots do NOT immediately follow the unsplash input id.
    import re

    unsplash_input_pattern = re.compile(
        r'id="unsplash-input"[^>]*value="[^"]+"', re.DOTALL
    )
    assert not unsplash_input_pattern.search(
        html
    ), "Unsplash input should have an empty value when no key is configured"


def test_managed_configured_provider_has_no_clear_button(client, device_config_dev):
    """JTN-598: The Clear input button was removed because inputs now start empty
    (no literal bullet-character pre-fill). There's nothing to clear, so the
    button itself was removed to avoid confusing users. This test ensures the
    removal sticks."""
    device_config_dev.set_env_key("NASA_SECRET", "nasa-test-value")

    resp = client.get("/settings/api-keys")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "clear-button" not in html
    assert 'data-api-action="clear-field"' not in html


def test_managed_configured_provider_delete_button_has_title(client, device_config_dev):
    """JTN-215: delete button has a tooltip explaining it permanently removes the key."""
    device_config_dev.set_env_key("NASA_SECRET", "nasa-test-value")

    resp = client.get("/settings/api-keys")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'title="Permanently remove key from .env"' in html


def test_managed_configured_provider_delete_button_aria_label_says_permanently(
    client, device_config_dev
):
    """JTN-215: delete button aria-label includes 'permanently' to distinguish from clear."""
    device_config_dev.set_env_key("NASA_SECRET", "nasa-test-value")

    resp = client.get("/settings/api-keys")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'aria-label="Delete NASA APOD key permanently"' in html
