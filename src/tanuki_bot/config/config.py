from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_PATH = Path.home() / ".tanuki" / "config.json"

DEFAULT_MODEL = "gpt-5-mini"


def _load() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _save(data: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def ensure_defaults() -> dict[str, Any]:
    data = _load()
    changed = False

    if "openai" not in data:
        data["openai"] = {}
        changed = True

    if "model" not in data["openai"]:
        data["openai"]["model"] = DEFAULT_MODEL
        changed = True

    if changed:
        _save(data)

    return data


def set_openai_key(key: str) -> None:
    data = ensure_defaults()
    data["openai"]["api_key"] = key
    _save(data)


def has_openai_key() -> bool:
    data = _load()
    return bool(data.get("openai", {}).get("api_key"))


def get_openai_key() -> str | None:
    data = _load()
    return data.get("openai", {}).get("api_key")


def get_model() -> str:
    data = ensure_defaults()
    return str(data["openai"].get("model") or DEFAULT_MODEL)


def set_model(model: str) -> None:
    data = ensure_defaults()
    data["openai"]["model"] = model
    _save(data)


def masked_config() -> dict[str, Any]:
    data = ensure_defaults()
    if "openai" in data and "api_key" in data["openai"]:
        data["openai"]["api_key"] = "********"
    return data