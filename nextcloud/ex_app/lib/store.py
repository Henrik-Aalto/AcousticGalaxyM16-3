"""Persistenz pro Nextcloud-Benutzer im ExApp-Volume."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def data_root() -> Path:
    base = (os.environ.get("APP_PERSISTENT_STORAGE")
            or os.environ.get("ACOUSTIC_DATA_DIR")
            or "/tmp/acousticgalaxy")
    return Path(base)


def user_dir(user: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.@-]", "_", user or "anonymous")
    path = data_root() / "users" / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load_settings(user: str) -> dict[str, Any]:
    return read_json(user_dir(user) / "settings.json", {})


def save_settings(user: str, settings: dict[str, Any]) -> None:
    write_json(user_dir(user) / "settings.json", settings)


def load_features(user: str) -> dict[str, Any]:
    return read_json(user_dir(user) / "features.json", {})


def save_features(user: str, features: dict[str, Any]) -> None:
    write_json(user_dir(user) / "features.json", features)


def load_library(user: str) -> dict[str, Any]:
    return read_json(user_dir(user) / "library.json", {})


def save_library(user: str, library: dict[str, Any]) -> None:
    write_json(user_dir(user) / "library.json", library)


def load_landscape(user: str) -> dict[str, Any]:
    return read_json(user_dir(user) / "landscape.json", {})


def save_landscape(user: str, landscape: dict[str, Any]) -> None:
    write_json(user_dir(user) / "landscape.json", landscape)
