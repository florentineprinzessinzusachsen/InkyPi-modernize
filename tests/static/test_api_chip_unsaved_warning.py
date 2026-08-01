# pyright: reportMissingImports=false
"""Inline API key entry on the plugin page (replaces JTN-629).

The "API Required" chip / "Manage keys" control used to be a plain link to
/settings/api-keys. A user who had typed a long prompt (e.g. in AI Image)
and tapped it lost everything without warning — JTN-629 patched that with a
"you'll lose your changes, leave anyway?" confirmation modal.

This locks in the actual fix instead of the patch: the control now opens an
inline modal that lets the key be entered without ever navigating away, so
nothing is ever at risk of being lost. The old confirm-and-discard modal is
gone entirely — there's nothing left to confirm.
"""

from pathlib import Path

_PLUGIN_JS = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "static"
    / "scripts"
    / "plugin_page.js"
)


def _read_plugin_html(client, plugin_id: str = "ai_image") -> str:
    resp = client.get(f"/plugin/{plugin_id}")
    assert resp.status_code == 200, f"/plugin/{plugin_id} returned {resp.status_code}"
    return resp.get_data(as_text=True)


# --------------------------------------------------------------------------
# Template — inline modal, no navigate-away control
# --------------------------------------------------------------------------


def test_manage_keys_control_opens_modal_not_a_link(client):
    """"Manage keys" must open the inline modal, not navigate away."""
    html = _read_plugin_html(client)
    assert 'data-open-modal="pluginApiKeyModal"' in html, (
        "The Manage keys control must open the inline API key modal via "
        "data-open-modal, instead of navigating to /settings/api-keys."
    )


def test_inline_api_key_modal_rendered(client):
    """The inline API key modal must be present when a key is required."""
    html = _read_plugin_html(client)
    assert 'id="pluginApiKeyModal"' in html
    assert 'id="pluginApiKeySaveBtn"' in html
    assert 'data-api-key-modal-input' in html


def test_inline_api_key_modal_has_accessibility_attrs(client):
    html = _read_plugin_html(client)
    idx = html.find('id="pluginApiKeyModal"')
    assert idx != -1
    opening = html[idx : idx + 400]
    assert 'role="dialog"' in opening
    assert 'aria-modal="true"' in opening


def test_inline_api_key_modal_offers_new_tab_fallback(client):
    """A "manage all keys" link must still exist, opening in a new tab so it
    never risks discarding the current tab's unsaved settings."""
    html = _read_plugin_html(client)
    idx = html.find('id="pluginApiKeyModal"')
    assert idx != -1
    # Modal content runs to the matching close of its containing div; a
    # generous window comfortably covers it without needing a real parser.
    window = html[idx : idx + 3000]
    assert "/settings/api-keys" in window
    assert 'target="_blank"' in window
    assert 'rel="noopener' in window


def test_no_leave_confirmation_modal_anymore(client):
    """The old discard-changes confirmation modal must be fully gone."""
    html = _read_plugin_html(client)
    assert "apiKeysLeaveConfirmModal" not in html
    assert "Leave and discard changes" not in html


def test_no_modal_when_plugin_has_no_api_requirement(client):
    """The clock plugin has no api_key requirement; no API key modal at all."""
    html = _read_plugin_html(client, plugin_id="clock")
    assert "pluginApiKeyModal" not in html, (
        "The inline API key modal should only render when an API key is "
        "required. Other plugins should not get the modal markup."
    )


# --------------------------------------------------------------------------
# JS — inline modal wiring, dead dirty-tracking code removed
# --------------------------------------------------------------------------


def test_plugin_js_has_api_key_modal_init():
    js = _PLUGIN_JS.read_text(encoding="utf-8")
    assert "initApiKeyModal" in js
    assert "initApiKeyModal()" in js, "must be called from init()"


def test_plugin_js_saves_key_via_save_api_keys_endpoint():
    js = _PLUGIN_JS.read_text(encoding="utf-8")
    assert "saveApiKeyFromModal" in js
    assert "config.urls.save_api_keys" in js


def test_plugin_js_checks_api_key_presence_live():
    """The old page-load-snapshot check must be replaced by a live one."""
    js = _PLUGIN_JS.read_text(encoding="utf-8")
    assert "checkApiKeyPresence" in js
    assert "api_keys_status" in js
    # The stale, page-load-only flag must not gate anything anymore.
    assert "apiKeyMissing" not in js

def test_plugin_js_dirty_tracking_removed():
    """The old snapshot/dirty-check machinery is dead code now — removed."""
    js = _PLUGIN_JS.read_text(encoding="utf-8")
    assert "getSettingsFormSnapshot" not in js
    assert "isSettingsFormDirty" not in js
    assert "initApiKeysLeaveGuard" not in js
    assert "apiKeysLeaveConfirmModal" not in js
