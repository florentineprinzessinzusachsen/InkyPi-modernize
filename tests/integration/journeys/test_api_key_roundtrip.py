# pyright: reportMissingImports=false
"""End-to-end journey test for the API keys lifecycle (JTN-722).

Covers the full multi-step round-trip that click-level tests in JTN-325/323
do not: add -> reload -> edit -> reload -> delete -> reload, asserting that
state persists across every reload. Intended to catch POST-200-but-not-saved
bugs, ghost rows, and delete resurrection.

All values used here are fake placeholders — see ``_FAKE_KEY_VALUE``.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = [
    pytest.mark.journey,
    pytest.mark.skipif(
        os.getenv("SKIP_UI", "").lower() in ("1", "true"),
        reason="UI interactions skipped by env",
    ),
]

# SECURITY: fake placeholder credentials only. Never put real API keys here —
# the .env written by this test lives in pytest's tmp_path but test logs and
# CI artifacts may still capture the value.
_FAKE_KEY_VALUE = "sk-test-fake-1234"
_FAKE_KEY_VALUE_EDITED = "sk-test-fake-edited-5678"


def _unique_key_name() -> str:
    """Return a key name that satisfies the backend's ^[A-Za-z_][A-Za-z0-9_]*$."""
    # Hyphens are rejected, so use underscores only and a hex-safe suffix.
    return f"TEST_ROUNDTRIP_{uuid.uuid4().hex[:8].upper()}"


def _read_env_keys(env_path: str) -> dict[str, str]:
    """Parse the .env file the way the blueprint does and return {key: value}."""
    from dotenv import dotenv_values

    if not os.path.exists(env_path):
        return {}
    return dict(dotenv_values(env_path))


def test_api_key_add_edit_delete_roundtrip(live_server, browser_page, client):
    """Full lifecycle: add, edit, delete — persisted at each step.

    Rewritten for the unified card-based API-keys page (the separate
    "generic" list-row UI/`/api-keys/save` JSON flow this test used to drive
    was merged away; `/api-keys/save` itself still exists as a real route
    with its own coverage in test_apikeys_blueprint.py/test_apikeys_xss.py,
    it's just no longer what this page's own UI calls). The real UI now
    saves via a single form POST to /settings/save_api_keys and deletes
    immediately (no separate "Save" click needed) via /settings/delete_api_key.
    """
    from tests.integration.browser_helpers import navigate_and_wait

    key_name = _unique_key_name()
    # Resolve the .env path the server is actually using so the browser flow
    # and backend assertions agree on what "persisted" means.
    project_dir = os.environ.get("PROJECT_DIR")
    assert project_dir, "PROJECT_DIR should be set by the device_config_dev fixture"
    env_path = os.path.join(project_dir, ".env")

    try:
        # ---- Step 1: navigate to /settings/api-keys ----
        page = browser_page
        rc = navigate_and_wait(page, live_server, "/settings/api-keys")
        # Stub window.confirm so deleteKey()'s confirm() doesn't hang, and
        # window.location.reload so the post-save reload doesn't race
        # Playwright — we reload explicitly so we control timing.
        page.evaluate("window.confirm = () => true;" "window.location.reload = () => {};")

        # ---- Step 2: add a new custom secret via the UI ----
        page.locator("#addCustomSecretBtn").click()
        draft_card = page.locator('.api-key-card[data-custom-draft="true"]').last
        draft_card.locator(".custom-secret-name-input").fill(key_name)
        draft_card.locator(".custom-secret-value-input").fill(_FAKE_KEY_VALUE)

        save_btn = page.locator("#saveApiKeysBtn")
        with page.expect_response(
            lambda r: "/settings/save_api_keys" in r.url and r.request.method == "POST"
        ) as save_info:
            save_btn.click()
        assert save_info.value.status == 200, "initial save should succeed"

        # ---- Step 3: verify it appears on-disk (the round-trip). ----
        env_after_add = _read_env_keys(env_path)
        assert (
            env_after_add.get(key_name) == _FAKE_KEY_VALUE
        ), f"key {key_name} should be written to .env after save"

        # No console errors / client-log posts from the add flow.
        rc.assert_no_errors(name="api_keys_after_add")

        # ---- Step 4+5: reload and confirm the new card is still listed,
        # now finalized (its name is a static label, not an editable input). ----
        rc = navigate_and_wait(page, live_server, "/settings/api-keys")
        page.evaluate(
            "window.confirm = () => true;" "window.location.reload = () => {};"
        )
        card = page.locator(f'.api-key-card[data-key-name="{key_name}"]')
        assert card.count() == 1, f"key {key_name} should persist across reload"
        assert card.get_attribute("data-configured") == "true"

        # ---- Step 6+7: edit the value via the same card's "Change key"
        # toggle -> reveal the (now-empty, write-only) value input -> save. ----
        card.locator(".api-key-toggle").click()
        card.locator('input[type="password"]').fill(_FAKE_KEY_VALUE_EDITED)
        with page.expect_response(
            lambda r: "/settings/save_api_keys" in r.url and r.request.method == "POST"
        ) as edit_info:
            save_btn.click()
        assert edit_info.value.status == 200, "edit save should succeed"

        # ---- Step 8: edit is reflected — no duplicate entry, value updated. ----
        env_after_edit = _read_env_keys(env_path)
        assert (
            env_after_edit.get(key_name) == _FAKE_KEY_VALUE_EDITED
        ), "edit should replace the stored value"
        # dotenv_values() collapses duplicate keys in a dict, so to catch the
        # "edit wrote a second KEY= row" regression we inspect the raw file.
        with open(env_path, encoding="utf-8") as _env_file:
            key_lines_after_edit = [
                line
                for line in _env_file.read().splitlines()
                if line.startswith(f"{key_name}=")
            ]
        assert (
            len(key_lines_after_edit) == 1
        ), f"editing must not create a duplicate key; found {key_lines_after_edit!r}"

        # ---- Step 9+10: reload and verify the edit persisted. ----
        rc = navigate_and_wait(page, live_server, "/settings/api-keys")
        page.evaluate(
            "window.confirm = () => true;" "window.location.reload = () => {};"
        )
        card = page.locator(f'.api-key-card[data-key-name="{key_name}"]')
        assert card.count() == 1, "edited key should still be present after reload"
        assert _read_env_keys(env_path).get(key_name) == _FAKE_KEY_VALUE_EDITED

        # ---- Step 11+12: delete via the UI. The Delete button lives in the
        # same .api-key-actions row as the password input, hidden again after
        # the reload above — reveal it via the toggle first. Unlike the old
        # flow, deleteKey() fires its own request immediately on confirm, no
        # separate "Save" click needed to persist a deletion. ----
        card.locator(".api-key-toggle").click()
        with page.expect_response(
            lambda r: "/settings/delete_api_key" in r.url and r.request.method == "POST"
        ) as del_info:
            card.locator('[data-api-action="delete-key"]').click()
        assert del_info.value.status == 200, "delete should succeed"

        # Backend confirms the key is gone; custom-secret cards are removed
        # from the DOM entirely on delete (fixed providers stay, "not set").
        env_after_delete = _read_env_keys(env_path)
        assert (
            key_name not in env_after_delete
        ), f"key {key_name} should be removed from .env after delete"
        assert (
            page.locator(f'.api-key-card[data-key-name="{key_name}"]').count() == 0
        ), "custom-secret card should be removed from the DOM immediately on delete"
        rc.assert_no_errors(name="api_keys_after_delete")

        # ---- Step 13+14: reload; the key must stay gone (no resurrection). ----
        rc = navigate_and_wait(page, live_server, "/settings/api-keys")
        assert (
            page.locator(f'.api-key-card[data-key-name="{key_name}"]').count() == 0
        ), "deletion must not resurrect the key after reload"
        assert _read_env_keys(env_path).get(key_name) is None
        rc.assert_no_errors(name="api_keys_after_delete_reload")

    finally:
        # Teardown: make sure the test key is gone even if an assertion above
        # raised. /settings/delete_api_key removes just this one key without
        # touching any others, unlike the old /api-keys/save's replace-
        # everything semantics — no need to reconstruct the rest of .env.
        try:
            client.post("/settings/delete_api_key", data={"key": key_name})
        except Exception:
            # Teardown best-effort; don't mask the original assertion failure.
            pass
