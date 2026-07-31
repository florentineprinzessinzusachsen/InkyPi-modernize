"""Tests that mutmut configuration is present and well-formed.

This ensures future PRs cannot accidentally remove or corrupt the mutation
testing config without a test failure drawing attention to the change.

Schema is mutmut 3.x (bumped from 2.5.1 via dependabot): paths_to_mutate/
tests_dir/runner/dict_synonyms are gone, replaced by source_paths (a real
TOML array, not a comma-joined string) and pytest_add_cli_args_test_selection/
pytest_add_cli_args. mutmut 3.x always invokes pytest itself internally, so
there is no longer a "runner" key to assert invokes pytest.
"""

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

EXPECTED_FILES = [
    "src/app_setup/",
    "src/blueprints/",
    "src/utils/",
    "src/refresh_task/",
]


def _load_mutmut_config() -> dict:
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    return data.get("tool", {}).get("mutmut", {})


class TestMutmutConfig:
    def test_section_exists(self):
        cfg = _load_mutmut_config()
        assert cfg, "[tool.mutmut] section is missing from pyproject.toml"

    def test_source_paths_present(self):
        cfg = _load_mutmut_config()
        assert "source_paths" in cfg, "source_paths key missing from [tool.mutmut]"

    def test_source_paths_not_empty(self):
        cfg = _load_mutmut_config()
        paths = cfg.get("source_paths", [])
        assert paths, "source_paths must not be empty"

    def test_expected_files_in_scope(self):
        cfg = _load_mutmut_config()
        configured = set(cfg.get("source_paths", []))
        for expected in EXPECTED_FILES:
            assert expected in configured, (
                f"{expected} is not in source_paths — "
                "do not remove files from mutation scope without a deliberate decision"
            )

    def test_pytest_test_selection_configured(self):
        cfg = _load_mutmut_config()
        assert cfg.get("pytest_add_cli_args_test_selection") == [
            "tests/"
        ], "pytest_add_cli_args_test_selection should be ['tests/'] in [tool.mutmut]"

    def test_scoped_files_exist_on_disk(self):
        root = PYPROJECT.parent
        cfg = _load_mutmut_config()
        for rel_path in cfg.get("source_paths", []):
            full = root / rel_path
            assert full.exists(), (
                f"Mutation scope references {rel_path} but file does not exist. "
                "Either create the file or remove it from source_paths."
            )
            if rel_path.endswith("/"):
                assert full.is_dir(), f"{rel_path} should be a directory path"
            else:
                assert full.is_file(), f"{rel_path} should be a file path"
