from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tanuki_bot.core.plan import _workspace_paths
from tanuki_bot.projects.registry import Registry


class TaskExecuteError(RuntimeError):
    pass


@dataclass
class ApplyResult:
    ok: bool
    applied: bool
    notes: str


_DIFF_ONLY_SYSTEM = (
    "You are a code-change engine.\n"
    "You MUST return ONLY a unified diff that can be applied with `git apply`.\n"
    "No explanations. No prose.\n"
    "Prefer `diff --git a/... b/...` format.\n"
    "If you include a code fence, it must be a single ```diff or ```patch block.\n"
    "The diff must be valid unified diff.\n"
)


def _read_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _write_debug(workspace_base: Path, name: str, content: str) -> None:
    dbg = workspace_base / "debug"
    dbg.mkdir(parents=True, exist_ok=True)
    (dbg / name).write_text(content, encoding="utf-8")


def _strip_code_fences(text: str) -> str:
    m = re.search(r"```(?:diff|patch)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text.strip()


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    return text


def _trim_trailing_noise(diff_text: str) -> str:
    lines = diff_text.splitlines(True)
    out: list[str] = []
    started = False

    for line in lines:
        if not started:
            if line.startswith("diff --git") or line.startswith("--- "):
                started = True
                out.append(line)
            continue

        if line.strip() in {"```", "```diff", "```patch"}:
            break
        if line.strip() == "---":
            break

        out.append(line)

    return "".join(out).strip() + "\n"


def _extract_unified_diff(text: str) -> str:
    raw = _normalize(_strip_code_fences(text))

    idx = raw.find("diff --git")
    if idx != -1:
        return _trim_trailing_noise(raw[idx:])

    m = re.search(r"^---\s+.+\n\+\+\+\s+.+\n", raw, re.MULTILINE)
    if m:
        return _trim_trailing_noise(raw[m.start() :])

    preview = raw[:500].replace("\n", "\\n")
    raise TaskExecuteError(
        "Model did not return a recognizable unified diff. "
        f"First 500 chars: {preview}"
    )


def _git_apply_check(repo: Path, diff_text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn", "-"],
        cwd=str(repo),
        input=diff_text,
        text=True,
        capture_output=True,
    )


def _git_apply(repo: Path, diff_text: str) -> None:
    check_p = _git_apply_check(repo, diff_text)
    if check_p.returncode != 0:
        raise TaskExecuteError(
            f"git apply --check failed.\n\nSTDOUT:\n{check_p.stdout}\n\nSTDERR:\n{check_p.stderr}"
        )

    apply_p = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=str(repo),
        input=diff_text,
        text=True,
        capture_output=True,
    )
    if apply_p.returncode != 0:
        raise TaskExecuteError(
            f"git apply failed.\n\nSTDOUT:\n{apply_p.stdout}\n\nSTDERR:\n{apply_p.stderr}"
        )


def _auto_context(repo: Path, workspace_base: Path) -> dict[str, Any]:
    # ARCHITECTURE.md (si existe)
    arch = _read_if_exists(workspace_base / "ARCHITECTURE.md")

    # IMPORTANT: meter “estado real” de archivos clave para que el patch aplique
    index_html = _read_if_exists(repo / "index.html")
    readme = _read_if_exists(repo / "README.md")

    # recorta para no petar el prompt
    def clip(s: str, limit: int) -> str:
        s = s or ""
        if len(s) > limit:
            return s[:limit] + "\n\n(TRUNCATED)\n"
        return s

    pkg = (repo / "package.json").exists()
    pyproject = (repo / "pyproject.toml").exists()

    return {
        "architecture_md": clip(arch, 20_000),
        "repo_hints": {
            "has_package_json": pkg,
            "has_pyproject_toml": pyproject,
        },
        "files": {
            "index.html": clip(index_html, 18_000),
            "README.md": clip(readme, 12_000),
        },
    }


def _build_prompt(*, task_title: str, task_description: str, context: dict[str, Any]) -> str:
    arch = context.get("architecture_md") or ""
    hints = context.get("repo_hints") or {}
    files = context.get("files") or {}

    index_html = files.get("index.html", "")
    readme = files.get("README.md", "")

    return (
        "Return a unified diff suitable for `git apply`.\n"
        "Rules:\n"
        "- Output ONLY the diff.\n"
        "- Prefer `diff --git a/... b/...` format.\n"
        "- No explanations.\n"
        "- The diff MUST apply to the CURRENT repo state shown below.\n\n"
        f"Task title:\n{task_title}\n\n"
        f"Task description:\n{task_description}\n\n"
        "Repo hints:\n"
        f"- has_package_json: {hints.get('has_package_json')}\n"
        f"- has_pyproject_toml: {hints.get('has_pyproject_toml')}\n\n"
        "CURRENT index.html:\n"
        "-----\n"
        f"{index_html}\n"
        "-----\n\n"
        "CURRENT README.md:\n"
        "-----\n"
        f"{readme}\n"
        "-----\n\n"
        "Project architecture (may be truncated):\n"
        f"{arch}\n"
    )


def _build_repair_prompt(*, prev_diff: str, error: str) -> str:
    prev = prev_diff if len(prev_diff) <= 12_000 else prev_diff[:12_000] + "\n\n(TRUNCATED)\n"
    err = error if len(error) <= 2_000 else error[:2_000] + "\n(TRUNCATED)\n"

    return (
        "The previous diff was invalid and could not be applied with `git apply`.\n"
        "Return ONLY a corrected unified diff that applies cleanly.\n"
        "No explanations.\n\n"
        f"git apply error:\n{err}\n\n"
        "Previous diff to repair:\n"
        f"{prev}\n"
    )


def _call_llm(prompt: str, *, system: str) -> str:
    try:
        from tanuki_bot.core.llm import text as llm_text  # type: ignore
    except Exception as e:
        raise TaskExecuteError(
            "Cannot import tanuki_bot.core.llm.text. Check src/tanuki_bot/core/llm.py."
        ) from e

    resp = llm_text(prompt, system=system)
    if not isinstance(resp, str) or not resp.strip():
        raise TaskExecuteError("LLM returned empty response.")
    return resp


def _is_corrupt_patch_error(msg: str) -> bool:
    m = msg.lower()
    return (
        ("corrupt patch" in m)
        or ("patch failed" in m)
        or ("git apply --check failed" in m)
        or ("git apply failed" in m)
    )


def apply_task(repo: Path, task: Any) -> ApplyResult:
    reg = Registry()
    pid = reg.get_active_id()
    if not pid:
        raise TaskExecuteError("No active project. Run: tanuki project up --path <repo>")

    paths = _workspace_paths(pid)
    workspace_base = paths["base"]

    title = getattr(task, "title", "") or ""
    desc = getattr(task, "description", "") or ""
    if not title:
        raise TaskExecuteError("Task has no title.")
    if not desc:
        desc = "(no description)"

    ctx = _auto_context(repo, workspace_base)
    prompt = _build_prompt(task_title=title, task_description=desc, context=ctx)

    raw1 = _call_llm(prompt, system=_DIFF_ONLY_SYSTEM)
    _write_debug(workspace_base, "last_llm_raw_1.txt", raw1)
    diff1 = _extract_unified_diff(raw1)
    _write_debug(workspace_base, "last_diff_1.patch", diff1)

    try:
        _git_apply(repo, diff1)
        return ApplyResult(ok=True, applied=True, notes="Diff applied successfully (attempt 1).")
    except TaskExecuteError as e:
        _write_debug(workspace_base, "last_apply_error_1.txt", str(e))
        if not _is_corrupt_patch_error(str(e)):
            raise

        repair_prompt = _build_repair_prompt(prev_diff=diff1, error=str(e))
        raw2 = _call_llm(repair_prompt, system=_DIFF_ONLY_SYSTEM)
        _write_debug(workspace_base, "last_llm_raw_2.txt", raw2)
        diff2 = _extract_unified_diff(raw2)
        _write_debug(workspace_base, "last_diff_2.patch", diff2)

        _git_apply(repo, diff2)
        return ApplyResult(ok=True, applied=True, notes="Diff applied successfully (attempt 2).")