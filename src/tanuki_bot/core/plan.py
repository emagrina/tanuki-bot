from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tanuki_bot.core.init import init_project
from tanuki_bot.core.llm import text
from tanuki_bot.core.repo_scan import snapshot_repo
from tanuki_bot.projects.registry import Registry


# -------------------------
# Helpers
# -------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _workspace_paths(project_id: str) -> dict[str, Path]:
    base = Path.home() / ".tanuki" / "projects" / project_id
    return {
        "base": base,
        "architecture": base / "memory" / "ARCHITECTURE.md",
        "context": base / "memory" / "CONTEXT.md",
        "tasks": base / "tasks" / "tasks.json",
    }


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _norm(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _safe_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    return []


def _clamp_priority(p: str) -> str:
    # Accept: P1/P2/P3 or p0/p1/p2/p3. Normalize to P1/P2/P3 (simple).
    s = _norm(p)
    if s in {"p0", "p1"}:
        return "P1"
    if s in {"p2"}:
        return "P2"
    if s in {"p3"}:
        return "P3"
    if s in {"p1", "p2", "p3"}:
        return s.upper()
    if s in {"p0", "p1", "p2", "p3"}:
        return s.upper()
    if s in {"p1", "p2", "p3"}:
        return s.upper()
    if s in {"p1", "p2", "p3"}:
        return s.upper()
    # If the model returns something else, default:
    return "P2"


def _clamp_status(s: str) -> str:
    # Keep it simple & automatable
    s = _norm(s)
    if s in {"todo", "doing", "blocked", "done", "skipped"}:
        return s
    if s in {"in progress", "in-progress"}:
        return "doing"
    if s in {"complete", "completed"}:
        return "done"
    if s in {"pending"}:
        return "todo"
    return "todo"


@dataclass
class Task:
    id: int
    title: str
    description: str
    status: str
    priority: str
    tags: list[str]

    created_at: str
    updated_at: str
    started_at: str | None
    done_at: str | None
    blocked_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "tags": self.tags,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "done_at": self.done_at,
            "blocked_reason": self.blocked_reason,
        }


def _task_from_any(d: dict[str, Any]) -> Task:
    # id can come as int or str; normalize to int
    raw_id = d.get("id")
    if isinstance(raw_id, int):
        tid = raw_id
    else:
        # Try parse strings like "t1" or "001" => numeric fallback
        m = re.search(r"(\d+)", str(raw_id or "0"))
        tid = int(m.group(1)) if m else 0

    title = str(d.get("title", "")).strip()
    description = str(d.get("description", "")).strip()
    status = _clamp_status(str(d.get("status", "todo")))
    priority = _clamp_priority(str(d.get("priority", "P2")))
    tags = _safe_list(d.get("tags"))

    created_at = str(d.get("created_at") or _now_iso())
    updated_at = str(d.get("updated_at") or created_at)
    started_at = d.get("started_at")
    done_at = d.get("done_at")
    blocked_reason = d.get("blocked_reason")

    return Task(
        id=tid,
        title=title,
        description=description,
        status=status,
        priority=priority,
        tags=tags,
        created_at=created_at,
        updated_at=updated_at,
        started_at=started_at if started_at else None,
        done_at=done_at if done_at else None,
        blocked_reason=blocked_reason if blocked_reason else None,
    )


def _load_tasks_payload(path: Path) -> dict[str, Any]:
    """
    New format:
    {
      "version": 1,
      "next_id": 12,
      "tasks": [ ... ]
    }
    Backwards compatible with: { "tasks": [...] }
    """
    payload = _load_json(path, {"tasks": []})
    tasks_raw = payload.get("tasks", [])
    if not isinstance(tasks_raw, list):
        tasks_raw = []

    tasks = [_task_from_any(x) for x in tasks_raw if isinstance(x, dict)]
    max_id = max((t.id for t in tasks), default=0)

    version = payload.get("version", 1)
    next_id = payload.get("next_id", max_id + 1)
    if not isinstance(next_id, int):
        next_id = max_id + 1
    if next_id <= max_id:
        next_id = max_id + 1

    return {
        "version": int(version) if isinstance(version, int) else 1,
        "next_id": next_id,
        "tasks": tasks,
    }


def _save_tasks_payload(path: Path, version: int, next_id: int, tasks: list[Task]) -> None:
    _save_json(
        path,
        {
            "version": version,
            "next_id": next_id,
            "tasks": [t.to_dict() for t in tasks],
        },
    )


def _find_match(existing: Iterable[Task], cand_title: str) -> Task | None:
    """
    Cheap matching: title normalization exact match.
    (You can improve later with fuzzy matching, but this already avoids 90% duplicates.)
    """
    ct = _norm(cand_title)
    for t in existing:
        if _norm(t.title) == ct:
            return t
    return None


# -------------------------
# LLM output parsing
# -------------------------

def _extract_blocks(s: str) -> tuple[str, str, str]:
    """
    Expect output format:
    ===CONTEXT===
    ...markdown...
    ===ARCHITECTURE===
    ...markdown...
    ===TASKS_JSON===
    {...json...}

    Backwards compatible:
    - if CONTEXT missing, return "" for context.
    """
    ctx_pat = r"===CONTEXT===\s*(.*?)\s*===ARCHITECTURE==="
    arch_pat = r"===ARCHITECTURE===\s*(.*?)\s*===TASKS_JSON==="
    tasks_pat = r"===TASKS_JSON===\s*(.*)\s*$"

    mctx = re.search(ctx_pat, s, flags=re.DOTALL)
    march = re.search(arch_pat, s, flags=re.DOTALL)
    mtasks = re.search(tasks_pat, s, flags=re.DOTALL)

    ctx = (mctx.group(1).strip() if mctx else "").strip()
    arch = (march.group(1).strip() if march else "").strip()
    tasks = (mtasks.group(1).strip() if mtasks else "").strip()

    return ctx, arch, tasks


# -------------------------
# Main entry
# -------------------------

def plan_from_brief(brief: str) -> tuple[Path, Path]:
    reg = Registry()
    pid = reg.get_active_id()
    if not pid:
        raise RuntimeError("No active project")

    # Ensure initialized workspace exists
    init_project()
    p = _workspace_paths(pid)

    proj = reg.get(pid)
    if not proj:
        raise RuntimeError("Active project not found in registry")

    snap = snapshot_repo(proj.repo_path)

    existing_arch = _read_text(p["architecture"])
    existing_ctx = _read_text(p["context"])

    tasks_payload = _load_tasks_payload(p["tasks"])
    existing_tasks: list[Task] = tasks_payload["tasks"]
    next_id: int = tasks_payload["next_id"]
    version: int = tasks_payload["version"]

    # Minimized task context to keep prompt small
    existing_tasks_compact = [
        {
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "priority": t.priority,
            "tags": t.tags,
        }
        for t in existing_tasks
    ]

    important_block = "\n\n".join(
        f"--- {k} ---\n{v}" for k, v in snap.important_files.items() if v.strip()
    )
    tree_block = "\n".join(snap.tree[:600])

    system = (
        "You are Tanuki, an expert staff-level software engineer and architect. "
        "You generate pragmatic project planning artifacts. "
        "Be concise, actionable, and avoid fluff. "
        "Never output code unless asked (we are planning now)."
    )

    prompt = f"""
Project name: {proj.name}
Repo path: {proj.repo_path}

User brief (latest):
{brief}

Existing CONTEXT.md (current state):
{existing_ctx}

Existing ARCHITECTURE.md (current state):
{existing_arch}

Existing tasks (compact, keep IDs stable):
{json.dumps({"tasks": existing_tasks_compact}, ensure_ascii=False, indent=2)}

Repo tree (truncated):
{tree_block}

Important files (truncated):
{important_block}

Return EXACTLY three blocks:

===CONTEXT===
Markdown for CONTEXT.md with sections:
- Goals (what we are building)
- Scope / non-goals
- Constraints & assumptions
- Decisions log (short bullets)
- Open questions

Rules for CONTEXT.md:
- Keep it user/product oriented, not folder listing.
- Capture the user brief and key decisions.
- Do NOT duplicate architecture.

===ARCHITECTURE===
Markdown for ARCHITECTURE.md with sections:
- Overview (architecture overview, not product brief)
- Modules / folder responsibilities (describe where things live)
- Data model (high level, if needed)
- CLI commands (current + planned)
- Quality gates (lint/tests/build)

Rules for ARCHITECTURE.md:
- Focus on repo structure and responsibilities.
- Do NOT repeat user brief / goals (that belongs in CONTEXT.md).
- Do NOT write code.

===TASKS_JSON===
A JSON object: {{ "tasks": [ ... ] }}

Rules for TASKS_JSON:
- Do NOT write code.
- Tasks must be actionable and ordered logically.
- 10-30 tasks.
- IMPORTANT: Preserve existing tasks by referencing their existing "id" when appropriate.
- If a task already exists but needs improvement, keep its same "id" and update title/description/priority/tags.
- NEVER reset statuses: keep status fields as-is unless a task truly must change.
- New tasks: you may omit "id" or set it to null; Tanuki will assign IDs.
- Each task must have: title, description, status (todo/doing/blocked/done/skipped), priority (P1/P2/P3), tags (list).
"""

    try:
        combined = text(prompt, system=system)
    except Exception as e:
        # Friendly error (billing/quota/network etc). Keep it simple:
        raise RuntimeError(
            "Plan generation failed while calling OpenAI. "
            "Check your OpenAI billing/credits and try again. "
            "If you see 'insufficient_quota', add credits in OpenAI Platform Billing."
        ) from e

    ctx_md, arch_md, tasks_json = _extract_blocks(combined)

    # Fallbacks if model output is incomplete
    if not ctx_md:
        ctx_md = existing_ctx or (
            "# Project Context\n\n"
            "## Goals\n\n"
            "- (not defined yet)\n\n"
            "## Scope / non-goals\n\n"
            "- \n\n"
            "## Constraints & assumptions\n\n"
            "- \n\n"
            "## Decisions log\n\n"
            "- \n\n"
            "## Open questions\n\n"
            "- \n"
        )

    if not arch_md:
        arch_md = existing_arch or "# Architecture\n\n(Architecture not defined yet.)\n"

    # Parse tasks JSON
    generated_tasks: list[dict[str, Any]] = []
    try:
        parsed = json.loads(tasks_json)
        tlist = parsed.get("tasks", [])
        if isinstance(tlist, list):
            generated_tasks = [x for x in tlist if isinstance(x, dict)]
    except Exception:
        generated_tasks = []

    # If parsing failed, keep existing and add a single blocking task (numeric id)
    if not generated_tasks:
        now = _now_iso()
        # Only add if not already present
        if not _find_match(existing_tasks, "Fix plan output"):
            new_task = Task(
                id=next_id,
                title="Fix plan output",
                description=(
                    "The model did not return valid tasks JSON. "
                    "Check billing/quota, then re-run `tanuki plan`."
                ),
                status="blocked",
                priority="P1",
                tags=["planning"],
                created_at=now,
                updated_at=now,
                started_at=None,
                done_at=None,
                blocked_reason="Invalid tasks JSON from model",
            )
            existing_tasks.append(new_task)
            next_id += 1

        # Write outputs (still update context/architecture)
        _write_text(p["context"], ctx_md)
        _write_text(p["architecture"], arch_md)
        _save_tasks_payload(p["tasks"], version=version, next_id=next_id, tasks=existing_tasks)
        return p["architecture"], p["tasks"]

    # Merge strategy:
    # - Keep existing tasks by id where provided
    # - If id missing/null => try title match => else new id
    # - Preserve status & timestamps of existing tasks unless explicitly changed
    by_id: dict[int, Task] = {t.id: t for t in existing_tasks if t.id > 0}
    merged: list[Task] = []

    now = _now_iso()

    def _apply_update(old: Task, upd: dict[str, Any]) -> Task:
        title = str(upd.get("title", old.title)).strip() or old.title
        description = str(upd.get("description", old.description)).strip() or old.description
        priority = _clamp_priority(str(upd.get("priority", old.priority)))
        tags = _safe_list(upd.get("tags")) or old.tags

        # Status: keep old unless model explicitly gives a valid status.
        status_in = upd.get("status", old.status)
        status_new = _clamp_status(str(status_in))

        status_final = old.status
        if status_new != old.status:
            # Only allow change if it's a meaningful transition
            # (We keep it permissive but consistent)
            status_final = status_new

        started_at = old.started_at
        done_at = old.done_at
        blocked_reason = old.blocked_reason

        # Maintain timestamps on status transitions (basic)
        if old.status != status_final:
            if status_final == "doing" and not started_at:
                started_at = now
            if status_final == "done":
                if not started_at:
                    started_at = now
                done_at = now
                blocked_reason = None
            if status_final == "blocked":
                blocked_reason = str(upd.get("blocked_reason") or blocked_reason or "").strip() or "Blocked"
            if status_final == "todo":
                # Don’t wipe history, just clear block reason
                blocked_reason = None

        updated = Task(
            id=old.id,
            title=title,
            description=description,
            status=status_final,
            priority=priority,
            tags=tags,
            created_at=old.created_at,
            updated_at=now,  # updated because plan ran and re-evaluated content
            started_at=started_at,
            done_at=done_at,
            blocked_reason=blocked_reason,
        )
        return updated

    seen_ids: set[int] = set()
    seen_titles: set[str] = set()

    for cand in generated_tasks:
        cand_id = cand.get("id", None)
        tid: int | None = None
        if isinstance(cand_id, int):
            tid = cand_id
        elif cand_id is None:
            tid = None
        else:
            # e.g. "t12"
            m = re.search(r"(\d+)", str(cand_id))
            tid = int(m.group(1)) if m else None

        title = str(cand.get("title", "")).strip()

        # Try id match
        if tid is not None and tid in by_id:
            updated = _apply_update(by_id[tid], cand)
            merged.append(updated)
            seen_ids.add(tid)
            seen_titles.add(_norm(updated.title))
            continue

        # Try title match (avoid duplicates)
        matched = _find_match(existing_tasks, title) if title else None
        if matched and matched.id in by_id:
            updated = _apply_update(matched, cand)
            merged.append(updated)
            seen_ids.add(matched.id)
            seen_titles.add(_norm(updated.title))
            continue

        # Otherwise it's a new task: assign numeric ID
        title = title or "Untitled task"
        nt = Task(
            id=next_id,
            title=title,
            description=str(cand.get("description", "")).strip(),
            status=_clamp_status(str(cand.get("status", "todo"))),
            priority=_clamp_priority(str(cand.get("priority", "P2"))),
            tags=_safe_list(cand.get("tags")),
            created_at=now,
            updated_at=now,
            started_at=None,
            done_at=None,
            blocked_reason=str(cand.get("blocked_reason") or "").strip() or None,
        )
        merged.append(nt)
        seen_ids.add(nt.id)
        seen_titles.add(_norm(nt.title))
        next_id += 1

    # Keep existing tasks not mentioned by the model (don’t lose work/status)
    # But only if their titles weren't reintroduced in merged list
    for t in existing_tasks:
        if t.id in seen_ids:
            continue
        if _norm(t.title) in seen_titles:
            continue
        merged.append(t)

    # Sort: keep model ordering first, then leftover existing. That’s already the case.

    # Write files
    _write_text(p["context"], ctx_md)
    _write_text(p["architecture"], arch_md)
    _save_tasks_payload(p["tasks"], version=version, next_id=next_id, tasks=merged)

    return p["architecture"], p["tasks"]