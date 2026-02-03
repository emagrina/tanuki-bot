from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from tanuki_bot.core.version import __version__
from tanuki_bot.projects.commands import project_app
from tanuki_bot.ui.branding import show_banner
from tanuki_bot.ui_web.server import serve

app = typer.Typer(add_completion=False, no_args_is_help=False)
app.add_typer(project_app, name="project")

console = Console()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="Show version and exit"),
) -> None:
    if version:
        console.print(f"[bold bright_cyan]tanuki[/] v{__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        show_banner(console)

        table = Table(title="Commands", show_header=True, header_style="bold bright_white")
        table.add_column("Command", style="bright_cyan", no_wrap=True)
        table.add_column("What it does", style="white")

        table.add_row("tanuki project up", "Register/attach a repo folder and set it active")
        table.add_row("tanuki project list", "List registered projects")
        table.add_row("tanuki project use <id>", "Switch the active project")
        table.add_row("tanuki project show [id]", "Show project info (defaults to active)")
        table.add_row("tanuki project set-path <id> --path <folder>", "Update a project's repo path (registry only)")
        table.add_row("tanuki project rename <id> --name <name>", "Rename a project (registry only)")
        table.add_row("tanuki project remove <id>", "Remove a project from Tanuki registry (repo not deleted)")
        table.add_row("tanuki init", "Initialize memory/backlog for the active project (next step)")
        table.add_row("tanuki run", "Run the autonomous loop (next step)")
        table.add_row("tanuki ui", "Open the local dashboard (projects + backlog)")

        console.print(table)
        console.print('\n[dim]Tip:[/] register current folder with [bold]tanuki project up --path .[/]\n')


@app.command()
def init() -> None:
    console.print("[bold bright_cyan]Init[/] → (pending) Initialize memory/backlog for the active project.")


@app.command()
def run() -> None:
    console.print("[bold bright_cyan]Run[/] → (pending) Autonomous loop: task → changes → tests → review.")


@app.command()
def ui(port: int = typer.Option(3847, "--port", "-p", help="Local UI port")) -> None:
    serve(port=port)