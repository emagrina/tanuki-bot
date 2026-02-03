from __future__ import annotations

from pathlib import Path

from tanuki_bot.config.config import get_model, has_openai_key
from tanuki_bot.projects.registry import Registry


def run_doctor() -> list[tuple[bool, str]]:
    checks: list[tuple[bool, str]] = []

    reg = Registry()
    pid = reg.get_active_id()

    if not pid:
        checks.append((False, "No active project"))
        return checks

    checks.append((True, f"Active project: {pid}"))

    workspace = Path.home() / ".tanuki" / "projects" / pid
    if not workspace.exists():
        checks.append((False, "Project not initialized (run: tanuki init)"))
    else:
        checks.append((True, "Project initialized"))

    if has_openai_key():
        checks.append((True, "OpenAI API key configured"))
        checks.append((True, f"Model: {get_model()}"))
    else:
        checks.append((False, "OpenAI API key missing (run: tanuki setup)"))

    return checks