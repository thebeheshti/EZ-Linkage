"""Where the AI API key comes from, chosen in Settings:

- "typed": the key typed into Settings, saved in .env next to the app;
- "variable": an environment variable the user names (for example MY_API_KEY),
  read from the system each time the key is needed.

The browser never stores the key or gets it back; the server adds it to model calls.
"""
import os
import re
from pathlib import Path

from app_paths import APP_DIR

ENV_FILE = APP_DIR / ".env"
SAVED_KEY_LINE = "EZ_LINKAGE_API_KEY"  # the .env line holding a typed key; only this app reads it
KEY_SOURCES = ("typed", "variable")
_VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def load_dotenv(path: Path | None = None) -> None:
    """Read KEY=VALUE lines from .env without overriding variables already set.

    The typed key is skipped: it stays in the file and never becomes an environment variable.
    """
    for key, value in _env_items(path or ENV_FILE):
        if key != SAVED_KEY_LINE:
            os.environ.setdefault(key, value)


def _env_items(path: Path) -> list[tuple[str, str]]:
    if not path.is_file():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        items.append((key.strip(), value.strip().strip('"').strip("'")))
    return items


def get_saved_key() -> str:
    return next((value for key, value in _env_items(ENV_FILE) if key == SAVED_KEY_LINE), "")


def set_api_key(key: str) -> None:
    """Save the typed key to .env; an empty key removes it."""
    key = (key or "").strip()
    if "\n" in key or "\r" in key:
        raise ValueError("The API key must be a single line.")
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.is_file() else []
    lines = [line for line in lines if line.split("=", 1)[0].strip() != SAVED_KEY_LINE]
    if key:
        lines.append(f"{SAVED_KEY_LINE}={key}")
    ENV_FILE.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    try:
        os.chmod(ENV_FILE, 0o600)  # owner-only where the OS supports it
    except OSError:
        pass


def is_valid_variable_name(name: str) -> bool:
    return bool(_VARIABLE_NAME.fullmatch(name or ""))


def variable_is_set(name: str) -> bool:
    return is_valid_variable_name(name) and bool(os.environ.get(name, "").strip())


def key_source(settings: dict | None) -> tuple[str, str]:
    """(source, variable name) from saved settings."""
    settings = settings or {}
    source = settings.get("apiKeySource")
    return (source if source in KEY_SOURCES else "typed"), str(settings.get("apiKeyVariable") or "").strip()


def get_api_key(settings: dict | None = None) -> str:
    source, variable = key_source(settings)
    if source == "variable":
        return os.environ.get(variable, "").strip() if is_valid_variable_name(variable) else ""
    return get_saved_key()


def missing_variable_message(settings: dict | None) -> str:
    """Why the chosen variable cannot supply a key, or "" when it can (or a typed key is used)."""
    source, variable = key_source(settings)
    if source != "variable" or variable_is_set(variable):
        return ""
    if not variable:
        return "Settings say to read the API key from a system variable, but no variable name is entered."
    return (f"The API key variable {variable} is not set on this computer. Set it and restart EZ Linkage, "
            "or type the key in Settings instead.")
