"""Tests for JTN-323: + Add Custom Secret button must use data-api-action delegation.

Button/action names were renamed ("Add API Key"/#addApiKeyBtn/'add-row' ->
"Add Custom Secret"/#addCustomSecretBtn/'add-custom-secret') when fixed
providers stopped needing an "add" affordance, but the underlying
delegation contract this file guards is unchanged.
"""


def test_add_button_has_data_api_action_attribute(client):
    """JTN-323: the + Add Custom Secret button must have data-api-action='add-custom-secret'."""
    resp = client.get("/settings/api-keys")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert (
        'data-api-action="add-custom-secret"' in html
    ), "Add Custom Secret button must use data-api-action='add-custom-secret' for delegation"


def test_js_delegation_handler_covers_add_row_action(client):
    """JTN-323: the delegated click handler must handle the 'add-custom-secret' action."""
    resp = client.get("/static/scripts/api_keys_page.js")
    assert resp.status_code == 200
    js = resp.get_data(as_text=True)

    assert '"add-custom-secret"' in js, "JS must include an 'add-custom-secret' action case"
    assert (
        "addCustomSecretCard();" in js
    ), "The 'add-custom-secret' action must call addCustomSecretCard()"


def test_add_button_has_both_id_and_data_action(client):
    """JTN-323: the button should have both id and data-api-action for robustness."""
    resp = client.get("/settings/api-keys")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'id="addCustomSecretBtn"' in html
    assert 'data-api-action="add-custom-secret"' in html
