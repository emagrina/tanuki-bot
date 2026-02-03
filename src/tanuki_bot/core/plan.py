from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tanuki_bot.core.init import init_project
from tanuki_bot.core.llm import text
from tanuki_bot.projects.registry import Registry


def _workspace_paths(project_id: str) -> dict[str, Path]:
    base = Path.home() / ".tanuki" / "projects" / project_id
    return {
        "base": base,
        "architecture": base / "memory" / "ARCHITECTURE.md",
        "context": base / "memory" / "CONTEXT.md",
        "tasks": base / "tasks" / "tasks.json",
    }


def _write_tasks(path: Path, tasks: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"tasks": tasks}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def plan_from_brief(brief: str) -> tuple[Path, Path]:
    reg = Registry()
    pid = reg.get_active_id()
    if not pid:
        raise RuntimeError("No active project")

    # Ensure initialized
    init_project()
    p = _workspace_paths(pid)

    proj = reg.get(pid)
    assert proj is not None

    system = (
        "You are Tanuki, an expert staff-level software engineer and architect. "
        "Your job is to design an implementation plan and a task backlog for the given project brief. "
        "Return clear, actionable output."
    )

    prompt = f"""
Project name: {proj.name}
Repo path: {proj.repo_path}

User brief:
{brief}

Deliverables:
1) A concise ARCHITECTURE.md draft (sections: Overview, Tech stack, Modules, Data model, CLI commands, Quality gates).
2) A tasks.json list with 10-30 tasks.
   Each task must have: id, title, description, status (todo), priority (P1/P2/P3), tags (list).
Return the tasks as JSON only for (2). For (1) return markdown only.
Do NOT include code.
"""

    # Ask twice: once for architecture, once for tasks JSON
    arch_md = text(prompt + "\n\nNow write (1) ARCHITECTURE.md only.", system=system)

    tasks_json = text(prompt + "\n\nNow write (2) tasks JSON only.", system=system)

    # Parse tasks JSON (best-effort)
    try:
        parsed = json.loads(tasks_json)
        tasks = parsed["tasks"] if isinstance(parsed, dict) and "tasks" in parsed else parsed
        if not isinstance(tasks, list):
            raise ValueError("tasks not a list")
    except Exception:
        # fallback: minimal single task if parsing fails
        tasks = [
            {
                "id": "t1",
                "title": "Fix plan output",
                "description": "The model did not return valid JSON. Re-run tanuki plan and ensure tasks are valid JSON.",
                "status": "todo",
                "priority": "P1",
                "tags": ["planning"],
            }
        ]

    p["architecture"].write_text(arch_md.strip() + "\n", encoding="utf-8")
    _write_tasks(p["tasks"], tasks)

    return p["architecture"], p["tasks"]