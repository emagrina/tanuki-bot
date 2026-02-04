from __future__ import annotations

import json
import re
import subprocess
import threading
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from tanuki_bot.core.plan import _workspace_paths, _load_tasks_payload, _save_tasks_payload, Task
from tanuki_bot.projects.registry import Registry
from tanuki_bot.core.task_execute import apply_task


# -------------------------
# Console helpers (ANSI)
# -------------------------

_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_YELLOW = "\033[33m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_BLUE = "\033[34m"
_CYAN = "\033[36m"


def _print(msg: str) -> None:
    print(msg, flush=True)


def _step(msg: str) -> None:
    _print(f"{_BLUE}→{_RESET} {msg}")


def _task_start(task_id: int, title: str) -> None:
    _print(f"{_YELLOW}▶{_RESET} {_BOLD}Task {task_id}{_RESET}: {title}")


def _task_ok(task_id: int, extra: str = "") -> None:
    suffix = f" {_DIM}{extra}{_RESET}" if extra else ""
    _print(f"{_GREEN}✔{_RESET} {_BOLD}OK Task {task_id}{_RESET}{suffix}")


def _task_warn(task_id: int, msg: str) -> None:
    _print(f"{_CYAN}⚠{_RESET} {_BOLD}Task {task_id}{_RESET}: {msg}")


def _task_fail(task_id: int, msg: str) -> None:
    _print(f"{_RED}✖{_RESET} {_BOLD}FAIL Task {task_id}{_RESET}: {msg}")


def _warn(msg: str) -> None:
    _print(f"{_CYAN}⚠{_RESET} {msg}")


class _Heartbeat:
    """
    Prints a dot every `interval` seconds while running.
    Useful for long LLM calls so it never looks frozen.
    """

    def __init__(self, interval: float = 2.0) -> None:
        self.interval = interval
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def __enter__(self) -> "_Heartbeat":
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            _print(f"{_DIM}.{_RESET}")

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        if self._t:
            self._t.join(timeout=1.0)


# -------------------------
# Subprocess helpers
# -------------------------

class CmdError(RuntimeError):
    pass


def _iter_lines(pipe) -> Iterator[str]:
    # text mode line iterator
    for line in iter(pipe.readline, ""):
        if not line:
            break
        yield line


def _run_live(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """
    Run a command streaming stdout/stderr live, but still capture full output.
    """
    p = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )
    assert p.stdout is not None
    assert p.stderr is not None

    out_parts: list[str] = []
    err_parts: list[str] = []

    # Read both streams without blocking forever using threads
    def _read_stdout() -> None:
        for line in _iter_lines(p.stdout):
            out_parts.append(line)
            _print(f"{_DIM}{line.rstrip()}{_RESET}")

    def _read_stderr() -> None:
        for line in _iter_lines(p.stderr):
            err_parts.append(line)
            _print(f"{_DIM}{line.rstrip()}{_RESET}")

    t1 = threading.Thread(target=_read_stdout, daemon=True)
    t2 = threading.Thread(target=_read_stderr, daemon=True)
    t1.start()
    t2.start()

    rc = p.wait()
    t1.join(timeout=1.0)
    t2.join(timeout=1.0)

    stdout = "".join(out_parts)
    stderr = "".join(err_parts)

    cp = subprocess.CompletedProcess(args=cmd, returncode=rc, stdout=stdout, stderr=stderr)
    if rc != 0:
        raise CmdError(
            f"Command failed: {' '.join(cmd)}\n\nSTDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
        )
    return cp


def _run(cmd: list[str], cwd: Path, check: bool = True, live: bool = False) -> subprocess.CompletedProcess[str]:
    if live:
        # live runner always checks, but we can simulate check=False by catching
        if not check:
            try:
                return _run_live(cmd, cwd)
            except CmdError as e:
                # emulate CompletedProcess for check=False
                msg = str(e)
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr=msg)
        return _run_live(cmd, cwd)

    p = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True)
    if check and p.returncode != 0:
        raise CmdError(
            f"Command failed: {' '.join(cmd)}\n\nSTDOUT:\n{p.stdout}\n\nSTDERR:\n{p.stderr}"
        )
    return p


def _run_shell(command: str, cwd: Path, live: bool = True) -> None:
    # default live=True so you SEE what happens
    _run(["/bin/sh", "-lc", command], cwd=cwd, check=True, live=live)


# -------------------------
# Core logic
# -------------------------

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
    remote: str | None = "origin"
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


def _is_git_repo(repo: Path) -> bool:
    return (repo / ".git").exists()


def _ensure_git_identity(repo: Path) -> None:
    name = _run(["git", "config", "--get", "user.name"], cwd=repo, check=False).stdout.strip()
    email = _run(["git", "config", "--get", "user.email"], cwd=repo, check=False).stdout.strip()
    if not name:
        _run(["git", "config", "user.name", "tanuki-bot"], cwd=repo, check=True)
    if not email:
        _run(["git", "config", "user.email", "tanuki@local"], cwd=repo, check=True)


def _ensure_git_initialized(repo: Path, base_branch: str = "main") -> None:
    if _is_git_repo(repo):
        _ensure_git_identity(repo)
        return

    init_res = _run(["git", "init", "-b", base_branch], cwd=repo, check=False)
    if init_res.returncode != 0:
        _run(["git", "init"], cwd=repo, check=True)
        _run(["git", "checkout", "-b", base_branch], cwd=repo, check=False)

    _ensure_git_identity(repo)

    head_ok = _run(["git", "rev-parse", "--verify", "HEAD"], cwd=repo, check=False)
    if head_ok.returncode != 0:
        _run(["git", "add", "-A"], cwd=repo, check=True)
        diff = _run(["git", "diff", "--cached", "--name-only"], cwd=repo, check=False).stdout.strip()
        if diff:
            _run(["git", "commit", "-m", "chore: initial commit"], cwd=repo, check=True)
        else:
            _run(["git", "commit", "--allow-empty", "-m", "chore: initial commit"], cwd=repo, check=True)


def _list_remotes(repo: Path) -> list[str]:
    p = _run(["git", "remote"], cwd=repo, check=False)
    return [x.strip() for x in p.stdout.splitlines() if x.strip()]


def _has_remote(repo: Path, remote: str) -> bool:
    return remote in _list_remotes(repo)


def _git_branch_exists(repo: Path, branch: str) -> bool:
    p = _run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo, check=False)
    return p.returncode == 0


def _git_has_head(repo: Path) -> bool:
    p = _run(["git", "rev-parse", "--verify", "HEAD"], cwd=repo, check=False)
    return p.returncode == 0


def _git_current_branch(repo: Path) -> str | None:
    p = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, check=False)
    b = p.stdout.strip()
    if not b or b == "HEAD":
        return None
    return b


def _select_next_task(tasks: list[Task]) -> Task | None:
    prio_rank = {"P1": 1, "P2": 2, "P3": 3}
    todo = [t for t in tasks if t.status == "todo"]
    todo.sort(key=lambda t: (prio_rank.get(t.priority, 9), t.id))
    return todo[0] if todo else None


def _update_task(tasks: list[Task], updated: Task) -> list[Task]:
    return [updated if t.id == updated.id else t for t in tasks]


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
    try:
        p = _run(["git", "symbolic-ref", f"refs/remotes/{remote}/HEAD"], cwd=repo, check=True)
        ref = p.stdout.strip()
        parts = ref.split("/")
        if parts:
            return parts[-1]
    except Exception:
        pass
    return "main"


def _checkout_base(repo: Path, base_branch: str, remote: str | None) -> str:
    if remote and _has_remote(repo, remote):
        _step("git fetch --all --prune")
        _run(["git", "fetch", "--all", "--prune"], cwd=repo, check=False, live=True)

    effective = base_branch
    if not _git_branch_exists(repo, effective):
        if _git_branch_exists(repo, "master"):
            effective = "master"
        else:
            cur = _git_current_branch(repo) if _git_has_head(repo) else None
            if cur:
                effective = cur
            else:
                _run(["git", "checkout", "-B", effective], cwd=repo, check=True, live=True)
                return effective

    _run(["git", "checkout", effective], cwd=repo, check=False, live=True)
    current = _git_current_branch(repo)
    if current != effective:
        _run(["git", "checkout", "-B", effective], cwd=repo, check=True, live=True)

    if remote and _has_remote(repo, remote):
        _step(f"git pull --ff-only {remote} {effective}")
        _run(["git", "pull", "--ff-only", remote, effective], cwd=repo, check=False, live=True)

    return effective


def _create_branch(repo: Path, branch: str) -> None:
    if _git_branch_exists(repo, branch):
        _run(["git", "checkout", branch], cwd=repo, check=True, live=True)
        return
    _run(["git", "checkout", "-b", branch], cwd=repo, check=True, live=True)


def _commit_all(repo: Path, message: str) -> bool:
    _run(["git", "add", "-A"], cwd=repo, check=True, live=True)
    diff = _run(["git", "diff", "--cached", "--name-only"], cwd=repo, check=True).stdout.strip()
    if not diff:
        return False
    _run(["git", "commit", "-m", message], cwd=repo, check=True, live=True)
    return True


def _push_branch(repo: Path, remote: str, branch: str) -> None:
    _run(["git", "push", "-u", remote, branch], cwd=repo, check=True, live=True)


def _create_pr_gh(repo: Path, base: str, title: str, body: str, draft: bool) -> str:
    cmd = ["gh", "pr", "create", "--base", base, "--title", title, "--body", body]
    if draft:
        cmd.append("--draft")
    p = _run(cmd, cwd=repo, check=True, live=True)
    return p.stdout.strip()


def _autodetect_checks(repo: Path) -> list[str]:
    checks: list[str] = []
    pkg = repo / "package.json"
    pyproject = repo / "pyproject.toml"
    pytest_ini = repo / "pytest.ini"

    if pkg.exists():
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


def _llm_is_available() -> bool:
    try:
        from tanuki_bot.core import llm  # noqa: F401
        return hasattr(llm, "text")
    except Exception:
        return False


def _should_unblock(reason: str | None) -> bool:
    if not reason:
        return False

    r = reason.lower()
    transient_markers = [
        "not a git repository",
        "no such file or directory: .git",
        "runner is wired",
        "apply_task is not implemented",
        "cannot import tanuki_bot.core.llm.chat",
        "cannot import tanuki_bot.core.llm",
        "missing openai api key",
        "openai authentication failed",
        "quota",
        "billing",
        "insufficient_quota",
        "429",
        "did not return a recognizable unified diff",
        "corrupt patch",
        "git apply --check failed",
        "git apply failed",
        "pathspec",
        "no changes to commit",
        "task produced no file changes",
    ]
    return any(m in r for m in transient_markers)


def _auto_unblock_tasks(tasks: list[Task]) -> tuple[list[Task], int]:
    can_llm = _llm_is_available()
    now = _now_iso()

    updated: list[Task] = []
    changed = 0

    for t in tasks:
        if t.status == "blocked" and _should_unblock(t.blocked_reason):
            if t.blocked_reason and "llm.chat" in t.blocked_reason.lower():
                if not can_llm:
                    updated.append(t)
                    continue

            updated.append(
                Task(
                    id=t.id,
                    title=t.title,
                    description=t.description,
                    status="todo",
                    priority=t.priority,
                    tags=t.tags,
                    created_at=t.created_at,
                    updated_at=now,
                    started_at=t.started_at,
                    done_at=t.done_at,
                    blocked_reason=None,
                )
            )
            changed += 1
        else:
            updated.append(t)

    return updated, changed


def _has_blocked(tasks: list[Task]) -> bool:
    return any(t.status == "blocked" for t in tasks)


def _blocked_summary(tasks: list[Task]) -> str:
    blocked = [t for t in tasks if t.status == "blocked"]
    blocked.sort(key=lambda t: (t.priority, t.id))
    lines: list[str] = []
    for t in blocked[:10]:
        reason = (t.blocked_reason or "").strip().replace("\n", " ")
        if len(reason) > 160:
            reason = reason[:160] + "..."
        lines.append(f"- {t.id} ({t.priority}) {t.title}: {reason}")
    extra = len(blocked) - len(lines)
    if extra > 0:
        lines.append(f"- (+{extra} más)")
    return "\n".join(lines) if lines else ""


# -------------------------
# Local task execution (no LLM)
# -------------------------

def _local_init_repo(repo: Path, cfg: RunnerConfig) -> None:
    _ensure_git_initialized(repo, base_branch=cfg.base_branch)
    _checkout_base(repo, cfg.base_branch, remote=None)


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _local_create_index_html(repo: Path) -> None:
    index = repo / "index.html"
    if index.exists():
        return

    _write_file(
        index,
        """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Tanuki Demo</title>
    <script src="https://cdn.tailwindcss.com"></script>
  </head>
  <body class="min-h-screen bg-neutral-950 text-neutral-100">
    <main class="mx-auto max-w-3xl px-6 py-12">
      <h1 class="text-4xl font-semibold tracking-tight text-emerald-400">Hello</h1>
      <p class="mt-3 text-neutral-300">Base page with Tailwind CDN.</p>
    </main>
  </body>
</html>
""",
    )


_LOCAL_TASKS: list[tuple[re.Pattern[str], Callable[[Path, RunnerConfig], None]]] = [
    (re.compile(r"^\s*initialize repository and create main branch\s*$", re.I), _local_init_repo),
    (re.compile(r"^\s*initialize repository\s*$", re.I), _local_init_repo),
    (re.compile(r"create main branch", re.I), _local_init_repo),
    (re.compile(r"create index\.html with tailwind cdn", re.I), lambda repo, cfg: _local_create_index_html(repo)),
]


def _try_run_local_task(repo: Path, cfg: RunnerConfig, task: Task) -> bool:
    title = (task.title or "").strip()
    for pattern, fn in _LOCAL_TASKS:
        if pattern.search(title):
            fn(repo, cfg)
            return True
    return False


def _finalize_task_success(
    *,
    t: Task,
    tasks: list[Task],
    tasks_path: Path,
    version: int,
    next_id: int,
    effective_create_pr: bool,
    now_iso: str,
) -> list[Task]:
    final_status = "review" if effective_create_pr else "done"
    done_at = now_iso if final_status == "done" else None

    t_final = Task(
        id=t.id,
        title=t.title,
        description=t.description,
        status=final_status,
        priority=t.priority,
        tags=t.tags,
        created_at=t.created_at,
        updated_at=now_iso,
        started_at=t.started_at,
        done_at=done_at,
        blocked_reason=None,
    )
    tasks = _update_task(tasks, t_final)
    _save_tasks_payload(tasks_path, version=version, next_id=next_id, tasks=tasks)
    return tasks


def run_once(*, create_pr: bool = True, dry_run: bool = False, ignore_blocked: bool = False) -> dict[str, Any]:
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
        cfg.base_branch = "main"
        cfg.pr_base = "main"
        cfg.pr_provider = "gh"
        cfg.pr_draft = True
        cfg.checks = _autodetect_checks(repo)

    run_dir = p["base"] / "runs" / _now_iso().replace(":", "").replace("-", "")
    log_parts: list[str] = []

    _ensure_git_initialized(repo, base_branch=cfg.base_branch)

    tasks, unblocked = _auto_unblock_tasks(tasks)
    if unblocked > 0:
        _save_tasks_payload(p["tasks"], version=version, next_id=next_id, tasks=tasks)

    # If there are blocked tasks, do NOT halt if there is still work to do.
    # Only halt if there are blocked tasks AND no todo tasks remain (unless ignore_blocked=True).
    if _has_blocked(tasks):
        summary = _blocked_summary(tasks)
        if summary:
            _warn("Hay tareas bloqueadas (las salto si hay TODO):\n" + summary)

        has_todo = any(t.status == "todo" for t in tasks)
        if (not has_todo) and (not ignore_blocked):
            msg = "Hay tareas bloqueadas y no quedan TODO. No avanzo."
            log_parts.append("status: halted_due_to_blocked")
            log_parts.append(msg)
            _write_run_log(run_dir, "run.md", "\n".join(log_parts))
            return {"ok": False, "message": msg, "log_dir": str(run_dir)}

    t = _select_next_task(tasks)
    if not t:
        return {"ok": True, "message": "No todo tasks found."}

    _task_start(t.id, t.title)

    branch = f"task/{t.id}-{_slug(t.title)}"
    pr_title = f"task {t.id}: {t.title}"
    pr_body = f"Automated change for task {t.id}.\n\nStatus: review required."

    remote_ok = bool(cfg.remote and _has_remote(repo, cfg.remote))
    effective_create_pr = bool(create_pr and remote_ok)

    if dry_run:
        _task_ok(t.id, extra="dry-run")
        return {"ok": True, "task_id": t.id, "branch": branch, "pr": "", "log_dir": str(run_dir), "dry_run": True}

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
        _step("checking clean git")
        _ensure_clean_git(repo)

        if cfg.remote and remote_ok:
            _step("detecting default branch from remote")
            cfg.base_branch = _detect_default_branch(repo, cfg.remote)
            cfg.pr_base = cfg.base_branch

        _step(f"checkout base: {cfg.base_branch}")
        checked_out = _checkout_base(repo, cfg.base_branch, cfg.remote if remote_ok else None)
        log_parts.append(f"base_branch(checked_out): {checked_out}")

        _step(f"create/checkout branch: {branch}")
        _create_branch(repo, branch)

        _step("try local task matcher")
        ran_local = _try_run_local_task(repo, cfg, t_doing)
        log_parts.append(f"local_task: {ran_local}")

        if not ran_local:
            _step("apply_task (LLM) running…")
            with _Heartbeat(interval=2.0):
                apply_task(repo, t_doing)
            _step("apply_task done")

        for c in (cfg.checks or []):
            _step(f"check: {c}")
            log_parts.append(f"check: {c}")
            _run_shell(c, cwd=repo, live=True)

        _step("commit changes")
        committed = _commit_all(repo, pr_title)
        log_parts.append(f"committed: {committed}")

        pr_url = ""

        if not committed:
            now2 = _now_iso()
            tasks = _finalize_task_success(
                t=t_doing,
                tasks=tasks,
                tasks_path=p["tasks"],
                version=version,
                next_id=next_id,
                effective_create_pr=effective_create_pr,
                now_iso=now2,
            )
            log_parts.append("note: no changes to commit; skipping push/pr")
            _write_run_log(run_dir, "run.md", "\n".join(log_parts))
            _task_warn(t.id, "no changes to commit (marked done/review)")
            return {
                "ok": True,
                "task_id": t.id,
                "branch": branch,
                "pr": "",
                "log_dir": str(run_dir),
                "no_changes": True,
            }

        if remote_ok and cfg.remote:
            _step(f"push branch to {cfg.remote}")
            _push_branch(repo, cfg.remote, branch)
        else:
            log_parts.append("push: skipped (no remote)")
            _step("push skipped (no remote)")

        if effective_create_pr:
            _step("create PR")
            if cfg.pr_provider == "gh":
                pr_url = _create_pr_gh(repo, base=cfg.pr_base, title=pr_title, body=pr_body, draft=cfg.pr_draft)
            else:
                raise RuntimeError(f"Unsupported PR provider: {cfg.pr_provider}")
        else:
            log_parts.append("pr: skipped (no remote or disabled)")
            _step("PR skipped (no remote or disabled)")

        now2 = _now_iso()
        tasks = _finalize_task_success(
            t=t_doing,
            tasks=tasks,
            tasks_path=p["tasks"],
            version=version,
            next_id=next_id,
            effective_create_pr=effective_create_pr,
            now_iso=now2,
        )

        if pr_url:
            log_parts.append(f"pr: {pr_url}")

        _write_run_log(run_dir, "run.md", "\n".join(log_parts))
        _task_ok(t.id, extra=(pr_url or branch))
        return {"ok": True, "task_id": t.id, "branch": branch, "pr": pr_url, "log_dir": str(run_dir)}

    except Exception as e:
        err = str(e) if str(e) else "unknown error"
        tb = traceback.format_exc()

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
        log_parts.append(f"error: {err}")
        _write_run_log(run_dir, "run.md", "\n".join(log_parts))
        _write_run_log(run_dir, "error.txt", tb)

        _task_fail(t.id, err.splitlines()[0][:220])
        raise


def run_forever(
    *,
    create_pr: bool = True,
    dry_run: bool = False,
    poll_seconds: float | None = None,
    max_tasks: int | None = None,
) -> list[dict[str, Any]]:
    """
    Runs tasks continuously until:
      - no TODO tasks remain (success), or
      - only BLOCKED tasks remain (halts with summary), or
      - max_tasks reached (if provided).

    If poll_seconds is provided:
      - when there are no TODO tasks, it will sleep and re-check (daemon mode).
      - BUT it still halts if there are blocked tasks AND no TODO tasks (cannot proceed).
    """
    results: list[dict[str, Any]] = []
    ran = 0

    while True:
        if max_tasks is not None and ran >= max_tasks:
            _warn(f"max_tasks reached ({max_tasks}). Stopping.")
            break

        try:
            r = run_once(
                create_pr=create_pr,
                dry_run=dry_run,
                ignore_blocked=False,  # IMPORTANT: we want to stop if only blocked remain
            )
            results.append(r)
            ran += 1

            # Finished
            if r.get("message") == "No todo tasks found.":
                _print(f"{_GREEN}✔{_RESET} All TODO tasks completed.")
                if poll_seconds is None:
                    break
                # daemon mode: sleep and continue checking for new tasks
                _step(f"no TODO tasks; sleeping {poll_seconds}s")
                threading.Event().wait(poll_seconds)
                continue

            # Halt due to blocked
            if r.get("ok") is False and "bloquead" in str(r.get("message", "")).lower():
                _task_fail(-1, "cannot continue: blocked tasks remain and no TODO available")
                break

            # otherwise: continue loop to next TODO

        except Exception as e:
            # run_once already marks the task as blocked before re-raising
            results.append({"ok": False, "error": str(e)})
            _warn(f"Task execution raised exception (task marked blocked). Continuing. Error: {e}")

            # Continue to next TODO automatically.
            # However, if now everything is blocked and no TODO remain, run_once will return halt next iteration.
            continue

    return results