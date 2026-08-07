# Dependency Management

Two independent pipelines, both producing hash-pinned lockfiles that `install/install.sh` installs with `pip install --require-hashes` — a tampered wheel is rejected even if the version number looks correct.

| Layer | Source of truth | Lockfile | Tool |
|---|---|---|---|
| Runtime deps | `pyproject.toml` (`[project.dependencies]`) | `uv.lock` → exported to `install/requirements.txt` | [uv](https://docs.astral.sh/uv/) |
| Dev/CI deps | `install/requirements-dev.in` | `install/requirements-dev.txt` | `pip-compile` |

`install/requirements.in` still exists as a legacy reference but is **not** the source of truth for runtime deps — edit `pyproject.toml` instead.

## Runtime dependencies (uv)

`uv lock` produces one universal, hash-pinned resolution covering every platform InkyPi supports (Linux x86_64/aarch64/armv7l/armv6l and macOS arm64/x86_64) from a single run — including `sys_platform` guards (e.g. `inky`, `cysystemd` are Linux-only) and multi-arch wheel hashes, with no manual per-platform patching required. This replaced an earlier pip-compile-based flow that had to be run per-Python-version and needed a hand-maintained block of manually-fetched hashes appended to `requirements.txt` for Linux-only packages, because pip-compile run on macOS can't resolve `sys_platform == "linux"` dependencies at all.

After changing a dependency in `pyproject.toml`:

```bash
uv lock

uv export --format requirements.txt --no-dev --no-emit-project \
    --output-file install/requirements.txt
```

Commit both `uv.lock` and `install/requirements.txt`.

To upgrade a package within an already-satisfied range (e.g. after a CVE) without touching everything else:

```bash
uv lock --upgrade-package requests
uv export --format requirements.txt --no-dev --no-emit-project --output-file install/requirements.txt
```

A bare `uv lock` does **not** float an already-satisfied range to the latest version — it keeps resolutions stable. `--upgrade-package` is required to pick up a newer version inside an existing bound.

Verify everything is in sync (same check CI runs):

```bash
bash scripts/check_requirements_drift.sh
```

`install/install.sh` still installs from `install/requirements.txt` with `--require-hashes` — no behavior change on the Pi, only the tool that regenerates the file changed.

## Dev/CI dependencies (pip-compile)

This pipeline is separate and not part of the uv migration.

1. Edit `install/requirements-dev.in`.
2. Regenerate:
   ```bash
   pip-compile --generate-hashes --no-strip-extras --allow-unsafe \
       install/requirements-dev.in -o install/requirements-dev.txt
   ```
3. Commit both files.

`install/requirements-dev.txt` is manually maintained against `requirements-dev.in` — nothing checks they stay in sync automatically, so regenerate deliberately rather than hand-editing the `.txt`. Regenerating via `pip-compile` on macOS **drops every `sys_platform == "linux"`-gated entry** (e.g. `memray`) — verify by grepping the output for `sys_platform` and diff the count against `requirements-dev.in`; restore any dropped entry's hash block from git history if needed.

Before changing a dev tool's config schema (e.g. `[tool.mutmut]`), check the pinned version in `requirements-dev.txt` — an in-flight dependency bump PR isn't proof the bump has landed.
