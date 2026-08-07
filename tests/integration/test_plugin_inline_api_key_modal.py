"""End-to-end coverage for the inline "Add API key" modal on the plugin page.

Reproduces the reported journey: a plugin needing an API key is opened, the
key is missing, and the user needs a way to add it *without* losing whatever
they've already typed into the settings form and without hitting a stale
"greyed out" button. This exercises the real plugin_page.js handlers (not a
test-only stand-in) against the real /plugin/unsplash markup.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("SKIP_UI", "").lower() in ("1", "true"),
    reason="UI interactions skipped by env",
)


def _load_real_plugin_page(page, client, plugin_id, fetch_router_js):
    resp = client.get(f"/plugin/{plugin_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    page.set_content(html)

    for path in (
        "src/static/scripts/ui_helpers.js",
        "src/static/scripts/response_modal.js",
        "src/static/scripts/form_validator.js",
        "src/static/scripts/plugin_form.js",
        "src/static/scripts/plugin_page/shared.js",
        "src/static/scripts/plugin_page/progress.js",
        "src/static/scripts/plugin_page.js",
    ):
        with open(path, encoding="utf-8") as f:
            page.add_script_tag(content=f.read())

    page.evaluate(f"""
        window.__requests__ = [];
        {fetch_router_js}
        window.__INKYPI_PLUGIN_BOOT__.pluginId = {plugin_id!r};
        window.InkyPiPluginPage.create(window.__INKYPI_PLUGIN_BOOT__).init();
    """)


def test_manage_keys_opens_inline_modal_without_losing_typed_settings(
    client, device_config_dev
):
    """Clicking "Manage keys" must never discard what the user has typed —
    it opens the inline modal in place, no navigation, nothing lost."""
    pytest.importorskip("playwright.sync_api", reason="playwright not available")
    device_config_dev.unset_env_key("UNSPLASH_ACCESS_KEY")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        _load_real_plugin_page(
            page,
            client,
            "unsplash",
            """
            window.fetch = (url) => Promise.resolve(
                new Response('{"success":true,"data":{}}', {status:200, headers:{"Content-Type":"application/json"}})
            );
            """,
        )

        # Simulate the user having typed something into a settings field.
        title_input = page.locator('[name="title"]').first
        if title_input.count():
            title_input.fill("My unsaved title")

        page.click('[data-open-modal="pluginApiKeyModal"]')
        page.wait_for_selector("#pluginApiKeyModal.is-open", timeout=3000)

        # The old confirm-and-discard modal must never appear.
        assert page.locator("#apiKeysLeaveConfirmModal").count() == 0

        # Nothing typed was lost — proof the click never navigated away.
        if title_input.count():
            assert title_input.input_value() == "My unsaved title"

        browser.close()


def test_inline_save_unblocks_add_to_playlist_without_reload(client, device_config_dev):
    """The core reported bug: add the key from "another tab" (simulated via
    the live status endpoint now reporting present), then a single click on
    the still-enabled header button must go through — no reload needed."""
    pytest.importorskip("playwright.sync_api", reason="playwright not available")
    device_config_dev.unset_env_key("UNSPLASH_ACCESS_KEY")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        _load_real_plugin_page(
            page,
            client,
            "unsplash",
            """
            window.__keyNowPresent__ = false;
            window.fetch = (url, opts) => {
                opts = opts || {};
                const urlStr = (url && url.toString ? url.toString() : String(url));
                try { window.__requests__.push({url: urlStr, method: opts.method || 'GET'}); } catch(e) {}
                if (urlStr.includes('/settings/api-keys/status')) {
                    return Promise.resolve(new Response(
                        JSON.stringify({success: true, data: {UNSPLASH_ACCESS_KEY: window.__keyNowPresent__}}),
                        {status: 200, headers: {'Content-Type': 'application/json'}}
                    ));
                }
                if (urlStr.includes('/settings/save_api_keys')) {
                    window.__keyNowPresent__ = true;
                    return Promise.resolve(new Response(
                        JSON.stringify({success: true, message: 'API keys saved.', updated: ['UNSPLASH_ACCESS_KEY']}),
                        {status: 200, headers: {'Content-Type': 'application/json'}}
                    ));
                }
                return Promise.resolve(new Response('{"success":true}', {status:200, headers:{'Content-Type':'application/json'}}));
            };
            """,
        )

        # The header "Add to playlist" button stays enabled (not `disabled`)
        # even though the key is missing at page-load.
        add_btn = page.locator('button[data-plugin-action="add_to_playlist"]')
        assert add_btn.get_attribute("disabled") is None

        # First click: live check reports missing -> modal opens instead of
        # silently failing or switching to an unusable schedule tab.
        add_btn.click()
        page.wait_for_selector("#pluginApiKeyModal.is-open", timeout=3000)

        # Type the key inline and save.
        page.fill("[data-api-key-modal-input]", "unsplash-test-key-123")
        page.click("#pluginApiKeySaveBtn")
        page.wait_for_function(
            "() => document.getElementById('pluginApiKeyModal').hidden === true",
            timeout=3000,
        )

        # The stale gating attribute must be cleared after a successful save.
        page.wait_for_function(
            """() => !document.querySelector('button[data-plugin-action="add_to_playlist"]')
                     .hasAttribute('data-api-key-check')""",
            timeout=3000,
        )

        # Second click now proceeds past the (now-passing) live check into
        # the normal add-to-playlist flow, revealing the Schedule tab.
        add_btn.click()
        page.wait_for_function(
            """() => !document.getElementById('pluginSchedulePanel').hidden""",
            timeout=3000,
        )

        browser.close()
