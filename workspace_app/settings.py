"""Small preferences kept on this computer (single operator), such as the color theme."""
import json
from pathlib import Path

THEMES = ("system", "light", "dark")
DEFAULTS = {"tema": "system"}


def _file(directory):
    return Path(directory) / "settings.json"


def load(directory):
    try:
        data = json.loads(_file(directory).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    merged = {**DEFAULTS, **{key: data[key] for key in DEFAULTS if key in data}}
    if merged["tema"] not in THEMES:
        merged["tema"] = DEFAULTS["tema"]
    return merged


def save(directory, **values):
    """Merge `values` into the stored preferences; unknown keys and values are ignored."""
    current = load(directory)
    for key, value in values.items():
        if key in DEFAULTS and (key != "tema" or value in THEMES):
            current[key] = value
    Path(directory).mkdir(parents=True, exist_ok=True)
    target = _file(directory)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
    return current
