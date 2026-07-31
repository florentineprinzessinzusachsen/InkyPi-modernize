import logging
import os

from dotenv import dotenv_values

from flask import Blueprint

logger = logging.getLogger(__name__)
apikeys_bp = Blueprint("apikeys", __name__)

# Internal app secrets that must never appear in the user-facing API Keys UI (JTN-309).
# These are application-level secrets, not provider API credentials.
_INTERNAL_KEYS: frozenset[str] = frozenset(
    {
        "SECRET_KEY",
        "TEST_KEY",
        "WTF_CSRF_SECRET_KEY",
    }
)


# Path to .env file
def get_env_path() -> str:
    """Get path to .env file in the project root."""
    project_dir = os.environ.get("PROJECT_DIR")
    if project_dir:
        return os.path.join(project_dir, ".env")
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(base_dir, ".env")


def parse_env_file(filepath: str) -> list[tuple[str, str]]:
    """Parse .env file and return list of (key, value) tuples."""
    if not os.path.exists(filepath):
        return []

    try:
        env_dict = dotenv_values(filepath)
        return list(env_dict.items())
    except Exception as e:
        logger.error(f"Error parsing .env file: {e}")
        return []


def _has_invalid_control_chars(value: str) -> bool:
    """Return True if *value* contains control characters that are not safe in .env files."""
    return any(
        (ord(ch) < 32 and ch not in ("\t",)) or ch in ("\n", "\r") for ch in value
    )
