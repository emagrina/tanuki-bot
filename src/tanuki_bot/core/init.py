from __future__ import annotations

import json
from pathlib import Path

from tanuki_bot.projects.registry import Registry


def init_project() -> Path:
    reg = Registry()
    pid = reg.get_active_id()

    if not pid:
        raise RuntimeError("No active project")

    base = Path.home() / ".tanuki" / "projects" / pid
    memory = base / "memory"
    tasks = base / "tasks"
    runs = base / "runs"

    memory.mkdir(parents=True, exist_ok=True)
    tasks.mkdir(parents=True, exist_ok=True)
    runs.mkdir(parents=True, exist_ok=True)

    # project.json
    pj = base / "project.json"
    if not pj.exists():
        proj = reg.get(pid)
        pj.write_text(
            json.dumps(
                {
                    "id": proj.id,
                    "name": proj.name,
                    "repo_path": proj.repo_path,
                    "created_at": proj.created_at,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # memory files
    arch = memory / "ARCHITECTURE.md"
    if not arch.exists():
        arch.write_text(
            "# Architecture\n\nInitial architecture not defined yet.\n",
            encoding="utf-8",
        )

    ctx = memory / "CONTEXT.md"
    if not ctx.exists():
        ctx.write_text(
            "# Project Context\n\nDescribe goals, constraints and scope here.\n",
            encoding="utf-8",
        )

    # tasks.json
    tj = tasks / "tasks.json"
    if not tj.exists():
        tj.write_text(
            json.dumps({"tasks": []}, indent=2),
            encoding="utf-8",
        )

    return base