from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt

from tanuki_bot.projects.registry import Registry
from tanuki_bot.core.plan import _workspace_paths, _load_tasks_payload, Task
from tanuki_bot.core.task_add import add_tasks_from_brief

task_app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


def _require_active_paths():
    reg = Registry()
    pid = reg.get_active_id()
    if not pid:
        raise typer.BadParameter("No active project. Run: tanuki project up --path <repo>")
    return _workspace_paths(pid)


@task_app.command("list")
def list_tasks(
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status (todo/doing/blocked/done/skipped)"),
    priority: str | None = typer.Option(None, "--priority", "-p", help="Filter by priority (P1/P2/P3)"),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag"),
) -> None:
    p = _require_active_paths()
    payload = _load_tasks_payload(p["tasks"])
    tasks: list[Task] = payload["tasks"]

    if status:
        tasks = [t for t in tasks if t.status == status.strip().lower()]
    if priority:
        tasks = [t for t in tasks if t.priority == priority.strip().upper()]
    if tag:
        tg = tag.strip().lower()
        tasks = [t for t in tasks if any(x.lower() == tg for x in t.tags)]

    table = Table(title="Tasks", show_header=True, header_style="bold bright_white")
    table.add_column("ID", style="bright_cyan", no_wrap=True)
    table.add_column("Status", style="white", no_wrap=True)
    table.add_column("Prio", style="white", no_wrap=True)
    table.add_column("Title", style="white")
    table.add_column("Updated", style="dim", no_wrap=True)

    prio_rank = {"P1": 1, "P2": 2, "P3": 3}
    tasks_sorted = sorted(tasks, key=lambda t: (prio_rank.get(t.priority, 9), t.id))

    for t in tasks_sorted:
        table.add_row(str(t.id), t.status, t.priority, t.title, t.updated_at)

    console.print(table)


@task_app.command("show")
def show_task(
    task_id: int = typer.Argument(..., help="Task numeric ID"),
) -> None:
    p = _require_active_paths()
    payload = _load_tasks_payload(p["tasks"])
    tasks: list[Task] = payload["tasks"]

    t = next((x for x in tasks if x.id == task_id), None)
    if not t:
        console.print(f"[bold red]Error[/]: Task {task_id} not found.")
        raise typer.Exit(code=1)

    console.print(f"[bold bright_cyan]#{t.id}[/] {t.title}")
    console.print(f"[dim]status:[/] {t.status}   [dim]priority:[/] {t.priority}")
    console.print(f"[dim]tags:[/] {', '.join(t.tags) if t.tags else '-'}")
    console.print(f"[dim]created:[/] {t.created_at}")
    console.print(f"[dim]updated:[/] {t.updated_at}")
    if t.blocked_reason:
        console.print(f"[dim]blocked_reason:[/] {t.blocked_reason}")
    console.print()
    console.print(t.description or "[dim](no description)[/]")


@task_app.command("add")
def add_tasks(
    brief: str | None = typer.Option(None, "--brief", "-b", help="Describe what you want to add/change."),
    file: str | None = typer.Option(None, "--file", "-f", help="Load brief from a text/markdown file."),
) -> None:
    """
    Append NEW tasks to tasks.json based on an incremental request.
    Does NOT modify existing task statuses (Tanuki run will do that later).
    """
    if file:
        brief_text = Path(file).expanduser().read_text(encoding="utf-8")
    elif brief:
        brief_text = brief
    else:
        console.print("[bold bright_cyan]Tanuki task add[/]\n")
        brief_text = Prompt.ask("What do you want to add/change? (one paragraph)")

    try:
        tasks_path = add_tasks_from_brief(brief_text)
    except RuntimeError as e:
        raise typer.BadParameter(str(e)) from e

    console.print(f"[green]✓ Tasks appended[/] → {tasks_path}")