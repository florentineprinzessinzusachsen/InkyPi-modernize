# pyright: reportMissingImports=false
"""Per-plugin configure -> preview -> save -> reload journey (JTN-723).

Extends :mod:`tests.integration.test_plugin_preview_smoke` (JTN-691). That
smoke test proves Update Preview flips ``#previewImage`` src. This journey
proves the *full* edit-preview-save-return cycle persists the user's input:

0. Create a named playlist instance for the plugin directly via the model
   (the "Save instance" path needs an existing instance to target, unlike
   the old draft-save button which created one on first save).
1. Navigate to ``/plugin/<id>?instance=<name>``.
2. Fill the per-plugin form from :data:`PLUGIN_FORM_INPUTS`.
3. Click Update Preview -> assert ``#previewImage`` src changes.
4. Click Save instance -> assert the PUT /update_plugin_instance/<name>
   response is ok.
5. Navigate AWAY to ``/``.
6. Navigate BACK to ``/plugin/<id>?instance=<name>``.
7. Assert every configured field re-populates with the value submitted.
8. Click Update Preview again -> preview re-renders cleanly (no 5xx,
   no console errors) -- deterministic-input stability check.

Parametrized per plugin so each failure is attributable (a weather break
can't mask a clock break). Scope is the set of plugins that render
offline today (clock, year_progress, todo_list, countdown) -- see
``tests/integration/fixtures/plugin_inputs.py`` for the input dicts.

**Teardown:** the instance created in step 0 is deleted after the test to
avoid leaking state into sibling tests that assume a clean playlist.

Relation to sibling tests
-------------------------
- JTN-691 (``test_plugin_preview_smoke.py``): preview-only smoke.
- JTN-698 (``test_click_sweep.py``): bounded click sweep; skips the
  save/preview buttons via ``data-test-skip-click`` because validation
  400s look like regressions to its DOM-mutation heuristic. *This* test
  explicitly clicks those buttons after filling the form -- that's the
  whole point, so we do not honour ``data-test-skip-click`` here.
"""

from __future__ import annotations

import os

import pytest
from tests.integration.browser_helpers import RuntimeCollector, stub_leaflet
from tests.integration.fixtures.plugin_inputs import (
    PLUGIN_FORM_INPUTS,
    fill_form_inputs,
)

pytestmark = [
    pytest.mark.journey,
    pytest.mark.skipif(
        os.getenv("SKIP_UI", "").lower() in ("1", "true"),
        reason="UI interactions skipped by env",
    ),
]


# Playwright wait budgets. The update_now POST is synchronous on the dev
# server path (refresh task off); the save HTMX POST swaps a small partial.
# Budget generously for slow CI boxes.
_UPDATE_PREVIEW_TIMEOUT_MS = 15000
_SAVE_TIMEOUT_MS = 10000

# Plugin set this journey exercises. We parametrize over every plugin in
# :data:`PLUGIN_FORM_INPUTS` so that:
# - Each plugin is its own test id; a weather break can't mask a clock break.
# - Distinct input categories are covered across the set: color (clock),
#   hidden-driven widget (clock face picker), text (todo_list, countdown),
#   select (todo_list), date (countdown), and no-input baseline
#   (year_progress -- proves the save/reload cycle is still clean with an
#   empty settings dict, not just when the form has fields to re-hydrate).
_ROUNDTRIP_PLUGINS = sorted(PLUGIN_FORM_INPUTS.keys())


def _preview_src(page) -> str | None:
    """Return current ``#previewImage`` src (or None if element/attr missing)."""
    return page.evaluate(
        "() => document.getElementById('previewImage')?.getAttribute('src') || null"
    )


def _read_form_value(page, name: str):
    """Read back the current value of a named field inside ``#settingsForm``.

    Uses the same selector strategy as :func:`fill_form_inputs` so the
    round-trip read is symmetric with the write. Returns the ``.value``
    property (not ``.getAttribute('value')``) so dynamically-set hidden
    inputs like ``#selected-clock-face`` read back correctly.
    """
    return page.evaluate(
        """
        (name) => {
          const form = document.getElementById('settingsForm');
          if (!form) return null;
          const el = form.querySelector(
            `[name="${name}"], #${CSS.escape(name)}`
          );
          if (!el) return null;
          if (el.type === 'checkbox') return el.checked;
          return el.value;
        }
        """,
        name,
    )


def _click_update_preview(page, plugin_id: str) -> str:
    """Click Update Preview and wait for ``#previewImage`` src to flip.

    Returns the new (post-click) src so callers can compare across clicks.
    Raises via ``assert`` on missing button or unchanged src (the JTN-681
    silent no-op class of failure).
    """
    before_src = _preview_src(page)
    assert (
        before_src is not None
    ), f"{plugin_id}: #previewImage missing src attribute before click"

    btn = page.locator('[data-plugin-action="update_now"]').first
    assert (
        btn.count() > 0
    ), f"{plugin_id}: no Update Preview button on /plugin/{plugin_id}"
    btn.wait_for(state="visible", timeout=5000)
    # force=True: matches JTN-691 (workflow-mode transitions can briefly
    # cover the button) and the click-sweep pattern. We explicitly click
    # data-test-skip-click="true" here -- that attribute is advisory for
    # the sweep, not a hard skip for targeted tests.
    btn.click(timeout=5000, force=True)

    page.wait_for_function(
        """
        (prev) => {
          const img = document.getElementById('previewImage');
          return img && img.getAttribute('src') !== prev;
        }
        """,
        arg=before_src,
        timeout=_UPDATE_PREVIEW_TIMEOUT_MS,
    )
    after_src = _preview_src(page)
    assert after_src and after_src != before_src, (
        f"{plugin_id}: #previewImage src did not change after Update Preview "
        f"(before={before_src!r}, after={after_src!r})"
    )
    return after_src


def _create_instance(flask_app, plugin_id: str) -> tuple[str, str]:
    """Create a named playlist instance for ``plugin_id`` with empty settings.

    The removed "Save Settings" draft button used to give every plugin an
    anonymous ``<plugin_id>_saved_settings`` instance to save into. Now the
    only save path is "Save instance" (PUT /update_plugin_instance/<name>),
    which requires a real, already-playlisted instance to target -- so the
    journey has to create one up front rather than relying on the save step
    itself to create it. Returns ``(playlist_name, instance_name)``.
    """
    device_config = flask_app.config["DEVICE_CONFIG"]
    playlist_manager = device_config.get_playlist_manager()
    playlist_name = playlist_manager.get_playlist_names()[0]
    instance_name = f"{plugin_id}_journey_instance"
    added = playlist_manager.add_plugin_to_playlist(
        playlist_name,
        {
            "plugin_id": plugin_id,
            "name": instance_name,
            "plugin_settings": {},
            "refresh": {"interval": 300},
        },
    )
    assert added, f"{plugin_id}: failed to create journey instance for round-trip test"
    device_config.update_atomic(
        lambda cfg: cfg.__setitem__("playlist_config", playlist_manager.to_dict())
    )
    return playlist_name, instance_name


def _click_save_instance(page, plugin_id: str) -> None:
    """Click Save instance and wait for the PUT /update_plugin_instance response.

    Unlike the removed draft-save button, this path is a plain fetch (see
    ``sendForm`` in plugin_form.js) that resolves via its JSON response, not
    an HX-Trigger DOM event -- so we wait on the network response itself.
    """
    save_btn = page.locator('[data-plugin-action="update_instance"]').first
    assert (
        save_btn.count() > 0
    ), f"{plugin_id}: no Save instance button on /plugin/{plugin_id}"
    save_btn.wait_for(state="visible", timeout=5000)

    with page.expect_response(
        lambda r: "/update_plugin_instance/" in r.url and r.request.method == "PUT",
        timeout=_SAVE_TIMEOUT_MS,
    ) as resp_info:
        save_btn.click(timeout=5000, force=True)
    response = resp_info.value
    assert response.ok, (
        f"{plugin_id}: update_plugin_instance PUT failed with "
        f"{response.status}"
    )


def _purge_instance(flask_app, plugin_id: str, playlist_name: str, instance_name: str) -> None:
    """Remove the journey's playlist instance so it doesn't leak into sibling tests."""
    device_config = flask_app.config.get("DEVICE_CONFIG")
    if device_config is None:
        return
    playlist_manager = device_config.get_playlist_manager()
    playlist = playlist_manager.get_playlist(playlist_name)
    if not playlist:
        return
    if playlist.find_plugin(plugin_id, instance_name):
        playlist.delete_plugin(plugin_id, instance_name)
        # Persist the mutation so a subsequent reader sees the clean state.
        try:
            device_config.update_atomic(
                lambda cfg: cfg.__setitem__(
                    "playlist_config", playlist_manager.to_dict()
                )
            )
        except Exception:
            # Teardown is best-effort; a failure here must not mask the
            # test's own success/failure signal.
            pass


@pytest.mark.parametrize(
    "plugin_id",
    _ROUNDTRIP_PLUGINS,
    ids=lambda pid: pid,
)
def test_plugin_preview_save_roundtrip(
    live_server, browser_page, flask_app, monkeypatch, plugin_id: str
):
    """Configure -> preview -> save -> leave -> return -> values persist.

    Each assertion is its own failure site so the logs point at the exact
    step that broke (preview flip vs save handshake vs reload hydration
    vs re-preview).
    """
    # Synchronous update_now path; stub display so no hardware is touched.
    # Matches JTN-691's setup exactly -- we want identical preview behaviour.
    flask_app.config["REFRESH_TASK"].running = False
    dm = flask_app.config["DISPLAY_MANAGER"]
    monkeypatch.setattr(dm.display, "display_image", lambda *a, **kw: None)

    page = browser_page
    stub_leaflet(page)
    collector = RuntimeCollector(page, live_server)
    inputs = PLUGIN_FORM_INPUTS[plugin_id]

    # "Save instance" (the only remaining save path) requires an existing
    # named instance to target, unlike the removed draft-save button which
    # created its own anonymous instance on first save.
    playlist_name, instance_name = _create_instance(flask_app, plugin_id)
    instance_url = f"{live_server}/plugin/{plugin_id}?instance={instance_name}"

    try:
        # --- Step 1: land on the plugin page --------------------------------
        page.goto(instance_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("#settingsForm", timeout=10000)
        page.wait_for_selector("#previewImage", timeout=10000)

        # --- Step 2: configure ---------------------------------------------
        fill_form_inputs(page, inputs)
        # Sanity: the values we typed actually landed in the form fields.
        for name, expected in inputs.items():
            actual = _read_form_value(page, name)
            assert str(actual) == str(expected), (
                f"{plugin_id}: form field {name!r} did not accept input "
                f"(wrote {expected!r}, read back {actual!r})"
            )

        # --- Step 3: Update Preview ----------------------------------------
        _click_update_preview(page, plugin_id)

        # --- Step 4: Save instance -------------------------------------------
        _click_save_instance(page, plugin_id)

        # --- Step 5/6: navigate away, then back ----------------------------
        page.goto(f"{live_server}/", wait_until="domcontentloaded", timeout=30000)
        page.goto(instance_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("#settingsForm", timeout=10000)
        page.wait_for_selector("#previewImage", timeout=10000)

        # --- Step 7: round-trip assertion (the heart of JTN-723) -----------
        # Every value we submitted in step 2 must come back re-hydrated in
        # the form. Catches silent serialization losses in save_plugin_settings
        # or renderer drift between plugin.html and the schema template.
        for name, expected in inputs.items():
            actual = _read_form_value(page, name)
            assert str(actual) == str(expected), (
                f"{plugin_id}: field {name!r} did not persist round-trip "
                f"(submitted {expected!r}, got {actual!r} after reload) -- "
                "settings serialization or rehydration regression."
            )

        # --- Step 8: re-preview stability ----------------------------------
        # Deterministic inputs should produce a stable, error-free second
        # preview. Cache-busting means the src *string* changes (timestamp),
        # but the click itself must complete without 5xx or console errors.
        second_src = _click_update_preview(page, plugin_id)
        assert second_src, f"{plugin_id}: second preview returned empty src"

        # --- Runtime tripwires: no JS errors / 5xx anywhere in the journey -
        assert not collector.page_errors, (
            f"{plugin_id}: pageerror during round-trip journey: "
            f"{collector.page_errors[:3]}"
        )
        assert not collector.console_errors, (
            f"{plugin_id}: console.error during round-trip journey: "
            f"{collector.console_errors[:3]}"
        )
        server_5xx = [
            r
            for r in collector.response_failures
            if 500 <= int(r.get("status", 0)) < 600
        ]
        assert not server_5xx, (
            f"{plugin_id}: round-trip journey triggered 5xx response(s): "
            f"{server_5xx[:3]}"
        )
    finally:
        # Always clean up the journey instance, even on failure, so a broken
        # test does not pollute sibling tests that read the same playlist.
        _purge_instance(flask_app, plugin_id, playlist_name, instance_name)
