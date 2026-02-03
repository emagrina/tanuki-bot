from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tanuki_bot.config.paths import current_project_path, registry_path
from tanuki_bot.projects.models import Project, now_iso


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "project"


class Registry:
    def __init__(self) -> None:
        self.path = registry_path()
        self.data: dict[str, Any] = _load_json(self.path, {"projects": {}})

    def save(self) -> None:
        _save_json(self.path, self.data)

    def list_projects(self) -> list[Project]:
        projects: list[Project] = []
        for p in self.data["projects"].values():
            projects.append(Project(**p))
        projects.sort(key=lambda x: (x.last_used_at or "", x.created_at), reverse=True)
        return projects

    def get(self, project_id: str) -> Project | None:
        p = self.data["projects"].get(project_id)
        return Project(**p) if p else None

    def exists_repo_path(self, repo_path: str) -> Project | None:
        """Return the project that matches repo_path, if any."""
        wanted = str(Path(repo_path).expanduser().resolve())
        for p in self.list_projects():
            if str(Path(p.repo_path).expanduser().resolve()) == wanted:
                return p
        return None

    def add(self, name: str, repo_path: str) -> Project:
        base_id = _slugify(name)
        project_id = base_id
        i = 2
        while project_id in self.data["projects"]:
            project_id = f"{base_id}-{i}"
            i += 1

        proj = Project(
            id=project_id,
            name=name,
            repo_path=repo_path,
            created_at=now_iso(),
            last_used_at=None,
        )
        self.data["projects"][project_id] = proj.to_dict()
        self.save()
        return proj

    def set_active(self, project_id: str) -> None:
        current_project_path().write_text(project_id, encoding="utf-8")

    def clear_active(self) -> None:
        p = current_project_path()
        if p.exists():
            p.unlink(missing_ok=True)

    def get_active_id(self) -> str | None:
        p = current_project_path()
        if not p.exists():
            return None
        value = p.read_text(encoding="utf-8").strip()
        return value or None

    def touch_last_used(self, project_id: str) -> None:
        p = self.data["projects"].get(project_id)
        if not p:
            return
        p["last_used_at"] = now_iso()
        self.data["projects"][project_id] = p
        self.save()

    # -----------------------------
    # New: update / rename / remove
    # -----------------------------

    def update_path(self, project_id: str, repo_path: str) -> bool:
        p = self.data["projects"].get(project_id)
        if not p:
            return False
        p["repo_path"] = repo_path
        self.data["projects"][project_id] = p
        self.save()
        return True

    def rename(self, project_id: str, name: str) -> bool:
        p = self.data["projects"].get(project_id)
        if not p:
            return False
        p["name"] = name
        self.data["projects"][project_id] = p
        self.save()
        return True

    def remove(self, project_id: str) -> bool:
        if project_id not in self.data["projects"]:
            return False

        # If removing active project, clear pointer
        if self.get_active_id() == project_id:
            self.clear_active()

        del self.data["projects"][project_id]
        self.save()
        return True

    def remove_by_path(self, repo_path: str) -> bool:
        """Remove a project by repo path match. Useful to clean up wrong registrations."""
        proj = self.exists_repo_path(repo_path)
        if not proj:
            return False
        return self.remove(proj.id)