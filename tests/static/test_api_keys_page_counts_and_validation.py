"""Regression guards for the API Keys page.

Covers three related dogfood findings, now against the unified card-based
page (the separate "generic" list-row page/addRow/deleteRow/saveGenericKeys
these tests originally targeted was merged away):
  - ISSUE-003: the "X providers / Y configured" badges did not update when
    the user added a preset row, even though the editor row was visible.
  - ISSUE-004: the badges always rendered identical numbers (because both
    were derived from `entries|length` server-side), so the pair was
    redundant.
  - ISSUE-005: clicking Save with an empty new-row value showed only a
    corner toast and never set `aria-invalid` / inline error on the
    offending input.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_KEYS_JS = ROOT / "src" / "static" / "scripts" / "api_keys_page.js"


def _read_js() -> str:
    return API_KEYS_JS.read_text(encoding="utf-8")


def _function_body(js: str, name: str) -> str:
    """Return the source from `function name(` up to the next top-level
    function/closure declaration. Simple substring slicing rather than
    brace-matching, which breaks on any function containing a nested
    `if { ... }` block (the closing `}` of an inner block can share the
    same indentation as the function's own closing brace)."""
    start = js.index(f"function {name}(")
    next_decl = re.search(r"\n  (?:async )?function \w+\(", js[start + 1 :])
    end = start + 1 + next_decl.start() if next_decl else len(js)
    return js[start:end]


def test_refresh_key_counts_function_exists_and_updates_both_chips():
    """The page must have a function that recomputes both badges from the
    current DOM. Without this the labels stay stale after add/delete."""
    js = _read_js()
    assert (
        "function refreshKeyCounts" in js
    ), "refreshKeyCounts() helper missing — badges will not update on add/delete"
    # Make sure both chip ids are touched.
    assert 'getElementById("providerCountSummary")' in js
    assert 'getElementById("configuredCountSummary")' in js


def test_refresh_key_counts_uses_distinct_semantics_for_provider_vs_configured():
    """The two badges MUST distinguish 'has a key entered' from 'has a value
    saved'. Otherwise they remain redundantly identical (ISSUE-004)."""
    js = _read_js()
    body = _function_body(js, "refreshKeyCounts")
    assert "totalProviders" in body and "totalConfigured" in body
    # "Configured" counts cards actually flagged data-configured="true" (set
    # only once a save/delete round-trip completes) - a distinct, narrower
    # signal than "providers" (every rendered card, fixed + custom).
    assert 'data-configured="true"' in body, (
        "configured count must depend on the data-configured flag, not just "
        "the total number of rendered cards"
    )


def test_refresh_key_counts_called_after_add_delete_and_value_input():
    """The badges must update on every structural state-change path: adding
    a custom-secret draft, cancelling/deleting one. Otherwise they go stale.

    Unlike the old addRow/deleteRow pair, counts are no longer recomputed on
    every keystroke — "configured" only changes when data-configured flips,
    which itself only happens after a real save/delete round-trip, so a
    per-keystroke refresh would have nothing new to report anyway.
    """
    js = _read_js()
    assert "refreshKeyCounts();" in _function_body(js, "addCustomSecretCard")
    assert "refreshKeyCounts();" in _function_body(js, "cancelInput"), (
        "cancelling a not-yet-saved draft removes its card and must refresh "
        "the provider count"
    )
    assert "refreshKeyCounts();" in _function_body(js, "updateDeletedStatus")


def test_save_generic_keys_marks_empty_value_input_aria_invalid():
    """On submit, an empty new-draft value must produce an inline
    aria-invalid + toast on the field — not just a silent no-op (ISSUE-005).
    """
    js = _read_js()
    body = _function_body(js, "validateCustomSecretDrafts")
    assert (
        'valueInput?.setAttribute("aria-invalid", "true")' in body
    ), "An empty value input must get aria-invalid='true' on submit"
    assert (
        "Enter a value for" in body
    ), "A toast must tell the user which entry needs a value"
    # Focus moves to the invalid input so keyboard users land there.
    assert "_safeFocus(valueInput)" in body


def test_save_generic_clears_prior_aria_invalid_at_start_of_each_submit():
    """Each new submit must clear stale aria-invalid from the previous run
    so a fixed input doesn't keep its old error state visually."""
    js = _read_js()
    body = _function_body(js, "validateCustomSecretDrafts")
    assert 'nameInput?.setAttribute("aria-invalid", "false")' in body
    assert 'valueInput?.setAttribute("aria-invalid", "false")' in body, (
        "validateCustomSecretDrafts must reset aria-invalid='false' on every "
        "submit before re-validating, otherwise old errors stick around."
    )
