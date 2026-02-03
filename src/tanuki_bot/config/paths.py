from __future__ import annotations

import os
from pathlib import Path

def tanuki_home() -> Path:
    base = Path(os.environ.get("TANUKI_HOME", Path.home() / ".tanuki"))
    base.mkdir(parents=True, exist_ok=True)
    (base / "projects").mkdir(parents=True, exist_ok=True)
    return base

def registry_path() -> Path:
    return tanuki_home() / "registry.json"

def current_project_path() -> Path:
    return tanuki_home() / "current_project"

def project_dir(project_id: str) -> Path:
    p = tanuki_home() / "projects" / project_id
    p.mkdir(parents=True, exist_ok=True)
    return p