#!/usr/bin/env python3
"""Bundle and optionally minify JS and CSS assets for production.

Usage:
    python scripts/build/build_assets.py              # bundle + minify JS and CSS
    python scripts/build/build_assets.py --no-minify  # concatenate only, no minification
    python scripts/build/build_assets.py --check      # dry-run: print stats, no write

Outputs:
    src/static/dist/common-sync.<hash>.min.js       (or .js without --no-minify)
    src/static/dist/common-deferred.<hash>.min.js
    src/static/dist/common.<hash>.min.css
    src/static/dist/manifest.json

The manifest maps logical names to versioned filenames:
    {
        "common.css":      "common.abc12345.min.css",
        "common-sync.js":  "common-sync.abc12345.min.js",
        "common-deferred.js": "common-deferred.def67890.min.js"
    }

Two JS bundles, not one, because base.html loads its global scripts in two
groups with different execution timing: theme.js/csrf.js/client_errors.js
run synchronously (no `defer`) before the rest of the page, then everything
else runs deferred. Collapsing both groups into a single bundle would still
be *valid* HTML, but it silently changes the sync scripts to deferred
execution — merging them here would need to re-verify nothing relies on that
ordering. Keeping two bundles preserves base.html's exact current semantics.

If either list below drifts from base.html's actual <script> tags (a script
added to one but not the other), the bundled and unbundled code paths will
silently behave differently. `--check` reports bundled files by name so a
diff against base.html is easy.

If rjsmin is installed it is used for JS minification; otherwise a simple
stdlib-based strip of // comments and blank lines is applied instead.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "src" / "static" / "scripts"
STYLES_DIR = REPO_ROOT / "src" / "static" / "styles"
DIST_DIR = REPO_ROOT / "src" / "static" / "dist"

# ---------------------------------------------------------------------------
# JS bundle manifests — files loaded on EVERY page (base.html), split to match
# base.html's own sync-vs-defer grouping. Page-specific scripts (e.g.
# plugin.html's ui_helpers.js, settings.html's dark_mode.js) are intentionally
# excluded; they stay as individual <script> tags on their own pages.
# ---------------------------------------------------------------------------

JS_MANIFEST_SYNC: list[str] = [
    "theme.js",
    "csrf.js",
    "client_errors.js",
]

JS_MANIFEST_DEFERRED: list[str] = [
    "client_error_reporter.js",
    "client_log_reporter.js",
    "status_badge.js",
    "debug_console.js",
    "form_validator.js",
    "response_modal.js",
    "form_state.js",
    "lightbox.js",
    "sidebar_preview.js",
    "sidebar_connectivity.js",
    "tweaks_panel.js",
    "update_indicator.js",
]

# ---------------------------------------------------------------------------
# CSS source — reuse the already-bundled main.css produced by build_css.py.
# We further minify it (or just copy) into the dist directory with a hash.
# ---------------------------------------------------------------------------

CSS_SOURCE = STYLES_DIR / "main.css"


# ---------------------------------------------------------------------------
# Simple JS minifier (no external deps)
# ---------------------------------------------------------------------------


def _minify_js_simple(js: str) -> str:
    """Best-effort pure-stdlib JS minification.

    Removes single-line comments (// ...) that appear on their own line,
    strips blank lines, and collapses leading whitespace.  Intentionally
    conservative to avoid breaking string literals that contain "//".
    """
    lines = js.splitlines()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        # Drop lines that are *only* a // comment (safe heuristic)
        if stripped.startswith("//"):
            continue
        # Drop blank lines
        if not stripped:
            continue
        out.append(stripped)
    return "\n".join(out)


def _minify_js(js: str) -> str:
    """Minify JS using rjsmin if available, else fall back to simple strip."""
    try:
        import rjsmin  # type: ignore[import]

        return rjsmin.jsmin(js)
    except ImportError:
        return _minify_js_simple(js)


# ---------------------------------------------------------------------------
# Simple CSS minifier (same algorithm as build_css.py)
# ---------------------------------------------------------------------------


def _minify_css(css: str) -> str:
    """Lightweight minification: strip comments, collapse whitespace."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    css = re.sub(r"\s+", " ", css)
    css = re.sub(r"\s*([{}:;,>~+])\s*", r"\1", css)
    css = css.replace(";}", "}")
    return css.strip()


# ---------------------------------------------------------------------------
# Hash helper
# ---------------------------------------------------------------------------


def _content_hash(content: str, length: int = 8) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:length]


# ---------------------------------------------------------------------------
# Bundle builders
# ---------------------------------------------------------------------------


def build_js_bundle(manifest: list[str], minify: bool = True) -> tuple[str, list[str]]:
    """Concatenate JS files from the given manifest list and optionally minify.

    Returns (bundled_content, list_of_included_filenames).
    """
    parts: list[str] = []
    included: list[str] = []

    for filename in manifest:
        path = SCRIPTS_DIR / filename
        if not path.is_file():
            print(f"WARNING: JS file not found, skipping: {path}", file=sys.stderr)
            continue
        source = path.read_text(encoding="utf-8")
        parts.append(f"// === {filename} ===\n{source}")
        included.append(filename)

    bundled = "\n\n".join(parts)
    if minify:
        bundled = _minify_js(bundled)
    return bundled, included


def build_css_bundle(minify: bool = True) -> str:
    """Read the pre-built main.css and optionally minify it."""
    if not CSS_SOURCE.is_file():
        sys.exit(
            f"ERROR: {CSS_SOURCE} not found. "
            "Run 'python scripts/build/build_css.py' first."
        )
    css = CSS_SOURCE.read_text(encoding="utf-8")
    if minify:
        css = _minify_css(css)
    return css


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Bundle JS/CSS assets for production.")
    parser.add_argument(
        "--no-minify",
        dest="minify",
        action="store_false",
        help="Concatenate only, skip minification",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Dry-run: print stats without writing files",
    )
    args = parser.parse_args(argv)
    suffix = "min.js" if args.minify else "js"
    css_suffix = "min.css" if args.minify else "css"

    # --- JS (two bundles - see module docstring for why) ---
    sync_content, sync_included = build_js_bundle(JS_MANIFEST_SYNC, minify=args.minify)
    sync_hash = _content_hash(sync_content)
    sync_filename = f"common-sync.{sync_hash}.{suffix}"

    deferred_content, deferred_included = build_js_bundle(
        JS_MANIFEST_DEFERRED, minify=args.minify
    )
    deferred_hash = _content_hash(deferred_content)
    deferred_filename = f"common-deferred.{deferred_hash}.{suffix}"

    # --- CSS ---
    css_content = build_css_bundle(minify=args.minify)
    css_hash = _content_hash(css_content)
    css_filename = f"common.{css_hash}.{css_suffix}"

    # --- Manifest ---
    manifest = {
        "common.css": css_filename,
        "common-sync.js": sync_filename,
        "common-deferred.js": deferred_filename,
    }

    if args.check:
        print(f"Sync JS bundle:     {len(sync_content):>9,} bytes  ->  {sync_filename}")
        print(f"  files: {', '.join(sync_included)}")
        print(f"Deferred JS bundle: {len(deferred_content):>9,} bytes  ->  {deferred_filename}")
        print(f"  files: {', '.join(deferred_included)}")
        print(f"CSS bundle:         {len(css_content):>9,} bytes  ->  {css_filename}")
        print("manifest.json preview:")
        print(json.dumps(manifest, indent=2))
        return

    # --- Write ---
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    # Clean up previous bundles (avoid stale hashed files accumulating)
    for pattern in (
        "common.bundle.*.js",
        "common.bundle.*.css",
        "common-sync.*.js",
        "common-deferred.*.js",
        "common.*.css",
    ):
        for old in DIST_DIR.glob(pattern):
            old.unlink()

    def _display_path(p: Path) -> str:
        try:
            return str(p.relative_to(REPO_ROOT))
        except ValueError:
            return str(p)

    sync_out = DIST_DIR / sync_filename
    sync_out.write_text(sync_content, encoding="utf-8")
    print(f"Wrote sync JS:     {len(sync_content):,} bytes -> {_display_path(sync_out)}")

    deferred_out = DIST_DIR / deferred_filename
    deferred_out.write_text(deferred_content, encoding="utf-8")
    print(f"Wrote deferred JS: {len(deferred_content):,} bytes -> {_display_path(deferred_out)}")

    css_out = DIST_DIR / css_filename
    css_out.write_text(css_content, encoding="utf-8")
    print(f"Wrote CSS:         {len(css_content):,} bytes -> {_display_path(css_out)}")

    manifest_out = DIST_DIR / "manifest.json"
    manifest_out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote manifest -> {_display_path(manifest_out)}")
    print(f"Sync files ({len(sync_included)}): {', '.join(sync_included)}")
    print(f"Deferred files ({len(deferred_included)}): {', '.join(deferred_included)}")


if __name__ == "__main__":
    main()
