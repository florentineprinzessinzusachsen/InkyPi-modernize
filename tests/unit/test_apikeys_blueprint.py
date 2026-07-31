# pyright: reportMissingImports=false
"""Tests for blueprints/apikeys.py.

The module's ``/api-keys/save`` route (and its supporting
``_validate_api_key_entry``/``write_env_file``/``mask_value`` helpers) was
removed as dead UI surface, superseded by ``/settings/save_api_keys``
(see ``blueprints/settings/_config.py``) - that route has its own coverage.
What remains here are the still-used helpers (``get_env_path``,
``parse_env_file``) that ``blueprints/settings/_config.py`` imports.
"""

from unittest.mock import patch

# ---- Helper functions ----


def test_parse_env_file_nonexistent(tmp_path):
    from blueprints.apikeys import parse_env_file

    result = parse_env_file(str(tmp_path / "nonexistent.env"))
    assert result == []


def test_parse_env_file_valid(tmp_path):
    from blueprints.apikeys import parse_env_file

    env_file = tmp_path / ".env"
    env_file.write_text("KEY1=value1\nKEY2=value2\n")
    result = parse_env_file(str(env_file))
    assert ("KEY1", "value1") in result
    assert ("KEY2", "value2") in result


def test_parse_env_file_error(tmp_path):
    from blueprints.apikeys import parse_env_file

    with patch(
        "blueprints.apikeys.dotenv_values", side_effect=Exception("parse error")
    ):
        result = parse_env_file(str(tmp_path / ".env"))
    assert result == []


def test_get_env_path_with_project_dir(monkeypatch):
    from blueprints.apikeys import get_env_path

    monkeypatch.setenv("PROJECT_DIR", "/custom/project")
    assert get_env_path() == "/custom/project/.env"


def test_get_env_path_default(monkeypatch):
    from blueprints.apikeys import get_env_path

    monkeypatch.delenv("PROJECT_DIR", raising=False)
    result = get_env_path()
    assert result.endswith(".env")
