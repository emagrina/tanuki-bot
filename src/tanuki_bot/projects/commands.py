from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from tanuki_bot.projects.registry import Registry

project_app = typer.Typer(help="Manage projects (register, activate, update, remove).")
console = Console()


def _resolve_repo_path(repo_path: str) -> str:
    return str(Path(repo_path).expanduser().resolve())


@project_app.command("up")
def project_up(
    repo_path: str = typer.Option(".", "--path", "-p", help="Path to the repo folder"),
    name: str | None = typer.Option(None, "--name", "-n", help="Optional friendly name (defaults to folder name)"),
) -> None:
    """
    Register/attach a repo to Tanuki and set it as active.
    """
    repo = _resolve_repo_path(repo_path)
    default_name = Path(repo).name
    project_name = name or default_name

    reg = Registry()

    # Reuse if already registered by repo_path
    existing = None
    for p in reg.list_projects():
        if str(Path(p.repo_path).resolve()) == repo:
            existing = p
            break

    if existing:
        reg.set_active(existing.id)
        reg.touch_last_used(existing.id)
        console.print(
            f"[bold dark_orange]Active[/] {existing.name} → id=[bold]{existing.id}[/] path=[dim]{existing.repo_path}[/]"
        )
        return

    proj = reg.add(name=project_name, repo_path=repo)
    reg.set_active(proj.id)
    reg.touch_last_used(proj.id)

    console.print(
        f"[bold dark_orange]Registered[/] {proj.name} → id=[bold]{proj.id}[/] path=[dim]{proj.repo_path}[/]"
    )


@project_app.command("list")
def list_projects() -> None:
    reg = Registry()
    active = reg.get_active_id()
    rows = reg.list_projects()

    if not rows:
        console.print("[dim]No projects yet.[/]")
        console.print('[dim]Create one with:[/] [bold]tanuki project up --path .[/]')
        return

    table = Table(title="Tanuki Projects", show_header=True, header_style="bold bright_white")
    table.add_column("Active", style="yellow", no_wrap=True)
    table.add_column("ID", style="dark_orange", no_wrap=True)
    table.add_column("Name", style="white")
    table.add_column("Repo path", style="dim")

    for p in rows:
        mark = "★" if p.id == active else ""
        table.add_row(mark, p.id, p.name, p.repo_path)

    console.print(table)


@project_app.command("use")
def use_project(
    project_id: str = typer.Argument(..., help="Project ID to set as active"),
) -> None:
    reg = Registry()
    proj = reg.get(project_id)

    if not proj:
        console.print(f"[bold red]Error[/]: Project '{project_id}' not found. Use [bold]tanuki project list[/].")
        raise typer.Exit(code=1)

    reg.set_active(project_id)
    reg.touch_last_used(project_id)
    console.print(f"[bold dark_orange]Active[/] {proj.name} ([bold]{proj.id}[/])")


@project_app.command("show")
def show_project(
    project_id: str = typer.Argument(None, help="Project ID (defaults to active)"),
) -> None:
    reg = Registry()
    pid = project_id or reg.get_active_id()

    if not pid:
        console.print("[bold red]Error[/]: No active project. Run [bold]tanuki project up --path <repo>[/].")
        raise typer.Exit(code=1)

    proj = reg.get(pid)
    if not proj:
        console.print(f"[bold red]Error[/]: Project '{pid}' not found in registry.")
        raise typer.Exit(code=1)

    console.print(f"[bold dark_orange]{proj.name}[/]  id=[bold]{proj.id}[/]")
    console.print(f"[dim]repo:[/] {proj.repo_path}")
    console.print(f"[dim]created:[/] {proj.created_at}")
    console.print(f"[dim]last used:[/] {proj.last_used_at or '-'}")


@project_app.command("set-path")
def set_path(
    project_id: str = typer.Argument(..., help="Project ID"),
    repo_path: str = typer.Option(..., "--path", "-p", help="New repo path (folder)"),
) -> None:
    """
    Update the repo path for a project (does NOT touch the filesystem).
    """
    reg = Registry()
    proj = reg.get(project_id)
    if not proj:
        console.print(f"[bold red]Error[/]: Project '{project_id}' not found.")
        raise typer.Exit(code=1)

    new_path = _resolve_repo_path(repo_path)
    reg.update_path(project_id, new_path)
    reg.touch_last_used(project_id)

    console.print(
        f"[bold dark_orange]Updated path[/] {proj.name} → id=[bold]{project_id}[/] path=[dim]{new_path}[/]"
    )


@project_app.command("rename")
def rename_project(
    project_id: str = typer.Argument(..., help="Project ID"),
    name: str = typer.Option(..., "--name", "-n", help="New project name"),
) -> None:
    """
    Rename a project (registry only).
    """
    reg = Registry()
    proj = reg.get(project_id)
    if not proj:
        console.print(f"[bold red]Error[/]: Project '{project_id}' not found.")
        raise typer.Exit(code=1)

    reg.rename(project_id, name)
    reg.touch_last_used(project_id)
    console.print(f"[bold dark_orange]Renamed[/] id=[bold]{project_id}[/] → {name}")


@project_app.command("remove")
def remove_project(
    project_id: str = typer.Argument(..., help="Project ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """
    Remove a project from Tanuki registry (does NOT delete any repo files).
    """
    reg = Registry()
    proj = reg.get(project_id)
    if not proj:
        console.print(f"[bold red]Error[/]: Project '{project_id}' not found.")
        raise typer.Exit(code=1)

    if not yes:
        confirm = typer.confirm(
            f"Remove '{proj.name}' from Tanuki registry? (Repo will NOT be deleted)"
        )
        if not confirm:
            console.print("[dim]Cancelled.[/]")
            raise typer.Exit(code=0)

    reg.remove(project_id)
    console.print(f"[bold dark_orange]Removed[/] {proj.name} ([bold]{project_id}[/])")