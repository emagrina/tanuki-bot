from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tanuki_bot.core.plan import _workspace_paths, _load_tasks_payload, _save_tasks_payload, Task
from tanuki_bot.projects.registry import Registry


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:50] or "task"


@dataclass
class RunnerConfig:
    base_branch: str = "main"
    remote: str = "origin"
    pr_provider: str = "gh"
    pr_base: str = "main"
    pr_draft: bool = True
    checks: list[str] | None = None

    @staticmethod
    def load(path: Path) -> "RunnerConfig":
        if not path.exists():
            return RunnerConfig(checks=[])
        data = json.loads(path.read_text(encoding="utf-8"))
        return RunnerConfig(
            base_branch=data.get("base_branch", "main"),
            remote=(data.get("git", {}) or {}).get("remote", "origin"),
            pr_provider=(data.get("pr", {}) or {}).get("provider", "gh"),
            pr_base=(data.get("pr", {}) or {}).get("base", data.get("base_branch", "main")),
            pr_draft=bool((data.get("pr", {}) or {}).get("draft", True)),
            checks=[str(x) for x in ((data.get("checks", {}) or {}).get("commands", []) or [])],
        )


class CmdError(RuntimeError):
    pass


def _run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        capture_output=True,
    )
    if check and p.returncode != 0:
        raise CmdError(
            f"Command failed: {' '.join(cmd)}\n\nSTDOUT:\n{p.stdout}\n\nSTDERR:\n{p.stderr}"
        )
    return p


def _run_shell(command: str, cwd: Path) -> None:
    _run(["/bin/sh", "-lc", command], cwd=cwd, check=True)


def _select_next_task(tasks: list[Task]) -> Task | None:
    prio_rank = {"P1": 1, "P2": 2, "P3": 3}
    todo = [t for t in tasks if t.status == "todo"]
    todo.sort(key=lambda t: (prio_rank.get(t.priority, 9), t.id))
    return todo[0] if todo else None


def _update_task(tasks: list[Task], updated: Task) -> list[Task]:
    out: list[Task] = []
    for t in tasks:
        out.append(updated if t.id == updated.id else t)
    return out


def _write_run_log(run_dir: Path, name: str, content: str) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / name
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return path


def _ensure_clean_git(repo: Path) -> None:
    p = _run(["git", "status", "--porcelain"], cwd=repo, check=True)
    if p.stdout.strip():
        raise RuntimeError("Working tree is not clean. Commit/stash your changes before running tanuki run.")


def _detect_default_branch(repo: Path, remote: str) -> str:
    """
    Try to resolve origin/HEAD -> origin/main, origin/master, etc.
    Fallback: main
    """
    try:
        p = _run(["git", "symbolic-ref", f"refs/remotes/{remote}/HEAD"], cwd=repo, check=True)
        ref = p.stdout.strip()  # refs/remotes/origin/main
        parts = ref.split("/")
        if len(parts) >= 1:
            return parts[-1]
    except Exception:
        pass
    return "main"


def _checkout_base(repo: Path, base_branch: str, remote: str) -> None:
    _run(["git", "fetch", "--all", "--prune"], cwd=repo, check=False)
    _run(["git", "checkout", base_branch], cwd=repo, check=True)
    _run(["git", "pull", "--ff-only", remote, base_branch], cwd=repo, check=False)


def _create_branch(repo: Path, branch: str) -> None:
    _run(["git", "checkout", "-b", branch], cwd=repo, check=True)


def _commit_all(repo: Path, message: str) -> None:
    _run(["git", "add", "-A"], cwd=repo, check=True)
    diff = _run(["git", "diff", "--cached", "--name-only"], cwd=repo, check=True)
    if not diff.stdout.strip():
        raise RuntimeError("No changes to commit. Task produced no file changes.")
    _run(["git", "commit", "-m", message], cwd=repo, check=True)


def _push_branch(repo: Path, remote: str, branch: str) -> None:
    _run(["git", "push", "-u", remote, branch], cwd=repo, check=True)


def _create_pr_gh(repo: Path, base: str, title: str, body: str, draft: bool) -> str:
    cmd = ["gh", "pr", "create", "--base", base, "--title", title, "--body", body]
    if draft:
        cmd.append("--draft")
    p = _run(cmd, cwd=repo, check=True)
    return p.stdout.strip()


def _autodetect_checks(repo: Path) -> list[str]:
    """
    Very small heuristic set. You can expand later.
    """
    checks: list[str] = []

    pkg = repo / "package.json"
    pyproject = repo / "pyproject.toml"
    pytest_ini = repo / "pytest.ini"

    if pkg.exists():
        # Only run if test script exists
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            scripts = (data.get("scripts") or {})
            if isinstance(scripts, dict) and "test" in scripts:
                checks.append("npm test")
        except Exception:
            pass

    if pyproject.exists() or pytest_ini.exists():
        checks.append("python -m pytest")

    return checks


def _apply_task_stub(repo: Path, task: Task) -> None:
    """
    Placeholder.
    """
    raise RuntimeError(
        "Runner is wired, but apply_task is not implemented yet. Implement core/task_execute.py to generate code changes."
    )


def run_once(*, create_pr: bool = True, dry_run: bool = False) -> dict[str, Any]:
    """
    Runs exactly one task:
    todo -> doing -> review (PR created) or blocked
    """
    reg = Registry()
    pid = reg.get_active_id()
    if not pid:
        raise RuntimeError("No active project. Run: tanuki project up --path <repo>")

    proj = reg.get(pid)
    if not proj:
        raise RuntimeError("Active project not found in registry")

    p = _workspace_paths(pid)
    tasks_payload = _load_tasks_payload(p["tasks"])
    tasks: list[Task] = tasks_payload["tasks"]
    version: int = tasks_payload["version"]
    next_id: int = tasks_payload["next_id"]

    repo = Path(proj.repo_path)

    cfg_path = p["base"] / "project.json"
    cfg = RunnerConfig.load(cfg_path)

    if not cfg_path.exists():
        cfg.remote = "origin"
        cfg.base_branch = _detect_default_branch(repo, cfg.remote)
        cfg.pr_base = cfg.base_branch
        cfg.pr_provider = "gh"
        cfg.pr_draft = True
        cfg.checks = _autodetect_checks(repo)

    run_dir = p["base"] / "runs" / _now_iso().replace(":", "").replace("-", "")
    log_parts: list[str] = []

    t = _select_next_task(tasks)
    if not t:
        return {"ok": True, "message": "No todo tasks found."}

    log_parts.append(f"task_id: {t.id}")
    log_parts.append(f"title: {t.title}")
    log_parts.append(f"repo: {repo}")
    log_parts.append(f"base_branch: {cfg.base_branch}")
    log_parts.append(f"remote: {cfg.remote}")
    log_parts.append(f"checks: {cfg.checks or []}")
    log_parts.append(f"create_pr: {create_pr}")
    log_parts.append(f"dry_run: {dry_run}")

    branch = f"task/{t.id}-{_slug(t.title)}"
    pr_title = f"task {t.id}: {t.title}"
    pr_body = f"Automated change for task {t.id}.\n\nStatus: review required."

    if dry_run:
        _write_run_log(run_dir, "run.md", "\n".join(log_parts))
        return {
            "ok": True,
            "task_id": t.id,
            "branch": branch,
            "pr": "",
            "log_dir": str(run_dir),
            "dry_run": True,
        }

    # Mark doing
    now = _now_iso()
    t_doing = Task(
        id=t.id,
        title=t.title,
        description=t.description,
        status="doing",
        priority=t.priority,
        tags=t.tags,
        created_at=t.created_at,
        updated_at=now,
        started_at=t.started_at or now,
        done_at=t.done_at,
        blocked_reason=None,
    )
    tasks = _update_task(tasks, t_doing)
    _save_tasks_payload(p["tasks"], version=version, next_id=next_id, tasks=tasks)

    try:
        _ensure_clean_git(repo)
        _checkout_base(repo, cfg.base_branch, cfg.remote)
        _create_branch(repo, branch)

        _apply_task_stub(repo, t_doing)

        for c in (cfg.checks or []):
            log_parts.append(f"check: {c}")
            _run_shell(c, cwd=repo)

        _commit_all(repo, pr_title)
        _push_branch(repo, cfg.remote, branch)

        pr_url = ""
        if create_pr:
            if cfg.pr_provider == "gh":
                pr_url = _create_pr_gh(repo, base=cfg.pr_base, title=pr_title, body=pr_body, draft=cfg.pr_draft)
            else:
                raise RuntimeError(f"Unsupported PR provider: {cfg.pr_provider}")
        else:
            log_parts.append("pr: skipped")

        now2 = _now_iso()
        t_review = Task(
            id=t.id,
            title=t.title,
            description=t.description,
            status="review",
            priority=t.priority,
            tags=t.tags,
            created_at=t.created_at,
            updated_at=now2,
            started_at=t_doing.started_at,
            done_at=None,
            blocked_reason=None,
        )
        tasks = _update_task(tasks, t_review)
        _save_tasks_payload(p["tasks"], version=version, next_id=next_id, tasks=tasks)

        log_parts.append(f"branch: {branch}")
        if pr_url:
            log_parts.append(f"pr: {pr_url}")

        _write_run_log(run_dir, "run.md", "\n".join(log_parts))

        return {"ok": True, "task_id": t.id, "branch": branch, "pr": pr_url, "log_dir": str(run_dir)}

    except Exception as e:
        err = str(e)
        now3 = _now_iso()
        t_blocked = Task(
            id=t.id,
            title=t.title,
            description=t.description,
            status="blocked",
            priority=t.priority,
            tags=t.tags,
            created_at=t.created_at,
            updated_at=now3,
            started_at=t_doing.started_at,
            done_at=None,
            blocked_reason=err[:5000],
        )
        tasks = _update_task(tasks, t_blocked)
        _save_tasks_payload(p["tasks"], version=version, next_id=next_id, tasks=tasks)

        log_parts.append("status: blocked")
        log_parts.append(err)
        _write_run_log(run_dir, "run.md", "\n".join(log_parts))

        raise


def run_autonomous(
    *,
    max_tasks: int = 1,
    create_pr: bool = True,
    keep_going: bool = False,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """
    Loop runner. Returns a list of results.
    """
    results: list[dict[str, Any]] = []
    for _ in range(max_tasks):
        try:
            r = run_once(create_pr=create_pr, dry_run=dry_run)
            results.append(r)
            # Stop if no work
            if r.get("message") == "No todo tasks found.":
                break
        except Exception as e:
            results.append({"ok": False, "error": str(e)})
            if not keep_going:
                break
    return results