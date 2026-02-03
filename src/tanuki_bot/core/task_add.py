from __future__ import annotations

import json
import re
from typing import Any

from tanuki_bot.core.llm import text
from tanuki_bot.core.repo_scan import snapshot_repo
from tanuki_bot.projects.registry import Registry
from tanuki_bot.core.plan import (
    _workspace_paths,
    _read_text,
    _load_tasks_payload,
    _save_tasks_payload,
    _now_iso,
    _norm,
    _safe_list,
    _clamp_priority,
    Task,
)


def _extract_tasks_json(s: str) -> str:
    pat = r"===TASKS_JSON===\s*(.*)\s*$"
    m = re.search(pat, s, flags=re.DOTALL)
    return (m.group(1).strip() if m else "").strip()


def add_tasks_from_brief(brief: str):
    reg = Registry()
    pid = reg.get_active_id()
    if not pid:
        raise RuntimeError("No active project")

    p = _workspace_paths(pid)

    proj = reg.get(pid)
    if not proj:
        raise RuntimeError("Active project not found in registry")

    snap = snapshot_repo(proj.repo_path)

    existing_ctx = _read_text(p["context"])
    existing_arch = _read_text(p["architecture"])

    payload = _load_tasks_payload(p["tasks"])
    existing_tasks: list[Task] = payload["tasks"]
    next_id: int = payload["next_id"]
    version: int = payload["version"]

    existing_compact = [
        {"id": t.id, "title": t.title, "status": t.status, "priority": t.priority, "tags": t.tags}
        for t in existing_tasks
    ]

    system = (
        "You are Tanuki, an expert staff-level engineer. "
        "You propose incremental tasks for a backlog. "
        "Be concise and actionable."
    )

    important_block = "\n\n".join(
        f"--- {k} ---\n{v}" for k, v in snap.important_files.items() if v.strip()
    )
    tree_block = "\n".join(snap.tree[:400])

    prompt = f"""
Project name: {proj.name}

Incremental request from the user:
{brief}

Existing CONTEXT.md (for goals):
{existing_ctx}

Existing ARCHITECTURE.md (for structure):
{existing_arch}

Existing tasks (avoid duplicates):
{json.dumps({"tasks": existing_compact}, ensure_ascii=False, indent=2)}

Repo tree (truncated):
{tree_block}

Important files (truncated):
{important_block}

Return EXACTLY one block:

===TASKS_JSON===
A JSON object: {{ "tasks": [ ... ] }}

Rules:
- Generate ONLY new tasks needed for the incremental request above.
- Do NOT repeat any existing task titles.
- Do NOT include IDs (omit "id" or set it to null).
- Each task must include: title, description, status ("todo"), priority ("P1"/"P2"/"P3"), tags (list).
- 1 to 8 tasks max.
- Do NOT write code.
"""

    combined = text(prompt, system=system)
    tasks_json = _extract_tasks_json(combined)

    generated: list[dict[str, Any]] = []
    try:
        parsed = json.loads(tasks_json)
        tlist = parsed.get("tasks", [])
        if isinstance(tlist, list):
            generated = [x for x in tlist if isinstance(x, dict)]
    except Exception:
        generated = []

    if not generated:
        raise RuntimeError("Model did not return valid tasks JSON.")

    existing_titles = {_norm(t.title) for t in existing_tasks}
    now = _now_iso()

    new_tasks: list[Task] = []
    for cand in generated:
        title = str(cand.get("title", "")).strip() or "Untitled task"
        nt = _norm(title)
        if nt in existing_titles:
            continue

        t = Task(
            id=next_id,
            title=title,
            description=str(cand.get("description", "")).strip(),
            status="todo",  # forced
            priority=_clamp_priority(str(cand.get("priority", "P2"))),
            tags=_safe_list(cand.get("tags")),
            created_at=now,
            updated_at=now,
            started_at=None,
            done_at=None,
            blocked_reason=None,
        )
        new_tasks.append(t)
        existing_titles.add(nt)
        next_id += 1

    if not new_tasks:
        # everything was duplicate → no-op
        return p["tasks"]

    merged = existing_tasks + new_tasks
    _save_tasks_payload(p["tasks"], version=version, next_id=next_id, tasks=merged)
    return p["tasks"]