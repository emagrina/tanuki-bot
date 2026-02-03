from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt

from tanuki_bot.core.version import __version__
from tanuki_bot.projects.commands import project_app
from tanuki_bot.ui.branding import show_banner
from tanuki_bot.ui_web.server import serve

from tanuki_bot.core.init import init_project
from tanuki_bot.core.doctor import run_doctor

from tanuki_bot.config.config import get_model, set_model, set_openai_key
from tanuki_bot.core.plan import plan_from_brief

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

        # --------------------------------
        # Getting started / setup (ordered)
        # --------------------------------
        table.add_row("tanuki project up --path <repo>", "Attach a repo folder and set it active")
        table.add_row("tanuki init", "Initialize Tanuki memory & backlog for the active project")
        table.add_row("tanuki setup", "Interactive setup (OpenAI API key + default model)")
        table.add_row("tanuki doctor", "Check Tanuki setup and detect missing steps")
        table.add_row("tanuki model [name]", "Show or set the model (e.g. gpt-5-mini)")
        table.add_row("tanuki plan", "Generate ARCHITECTURE.md + tasks.json from a brief")

        # ---------------------------
        # Project management
        # ---------------------------
        table.add_row("tanuki project list", "List registered projects")
        table.add_row("tanuki project show [id]", "Show project info (defaults to active)")
        table.add_row("tanuki project use <id>", "Switch the active project")
        table.add_row("tanuki project rename <id> --name <name>", "Rename a project (registry only)")
        table.add_row("tanuki project set-path <id> --path <folder>", "Update a project's repo path (registry only)")
        table.add_row("tanuki project remove <id>", "Remove a project from Tanuki registry (repo not deleted)")

        # ---------------------------
        # UI / automation
        # ---------------------------
        table.add_row("tanuki ui", "Open the local dashboard (projects + backlog)")
        table.add_row("tanuki run", "Run the autonomous loop (next step)")

        console.print(table)
        console.print('\n[dim]Tip:[/] register current folder with [bold]tanuki project up --path .[/]\n')


# -------------------------
# Core Tanuki commands
# -------------------------

@app.command()
def init() -> None:
    """Initialize Tanuki workspace for the active project."""
    try:
        path = init_project()
        console.print(f"[green]✓ Tanuki initialized[/] at [dim]{path}[/]")
    except RuntimeError:
        console.print("[bold red]Error[/]: No active project. Run [bold]tanuki project up --path <repo>[/]")


@app.command()
def setup() -> None:
    """Interactive setup: API key + default model."""
    console.print("[bold bright_cyan]Tanuki setup[/]\n")

    key = Prompt.ask("OpenAI API key", password=True)
    if key.strip():
        set_openai_key(key.strip())
        console.print("[green]✓ API key saved[/]")
    else:
        console.print("[yellow]Skipped API key[/]")

    current = get_model()
    console.print(f"\nCurrent model: [bold]{current}[/]")
    model_name = Prompt.ask("Model to use", default=current)
    set_model(model_name.strip())
    console.print(f"[green]✓ Model set[/] → {get_model()}")


@app.command()
def config() -> None:
    """
    Backwards-compatible alias for setup (so users can run tanuki config).
    """
    setup()


@app.command()
def model(
    name: str | None = typer.Argument(None, help="Optional model name to set, e.g. gpt-5-mini"),
) -> None:
    """Show or set the current model."""
    if name:
        set_model(name)
        console.print(f"[green]✓ Model set[/] → {get_model()}")
    else:
        console.print(f"[bold bright_cyan]Model[/] → {get_model()}")


@app.command()
def plan(
    brief: str | None = typer.Option(
        None,
        "--brief",
        "-b",
        help="Project brief. If omitted, Tanuki will ask interactively.",
    ),
    file: str | None = typer.Option(
        None,
        "--file",
        "-f",
        help="Load brief from a text/markdown file.",
    ),
) -> None:
    """Generate ARCHITECTURE.md and tasks.json for the active project."""
    if file:
        brief_text = Path(file).expanduser().read_text(encoding="utf-8")
    elif brief:
        brief_text = brief
    else:
        console.print("[bold bright_cyan]Tanuki plan[/]\n")
        brief_text = Prompt.ask("Paste a short brief (one paragraph is enough)")

    try:
        arch_path, tasks_path = plan_from_brief(brief_text)
    except RuntimeError as e:
        console.print(f"[bold red]Error[/]: {e}")
        console.print("[dim]Tip: run `tanuki project up` then `tanuki init` then `tanuki setup`.[/]")
        raise typer.Exit(code=1)

    console.print("[green]✓ Plan generated[/]")
    console.print(f"[dim]Architecture:[/] {arch_path}")
    console.print(f"[dim]Tasks:[/] {tasks_path}")


@app.command()
def doctor() -> None:
    """Run diagnostics to check Tanuki setup."""
    console.print("[bold bright_cyan]Tanuki doctor[/]\n")

    results = run_doctor()
    has_error = False

    for ok, msg in results:
        icon = "✓" if ok else "✗"
        color = "green" if ok else "red"
        console.print(f"[{color}]{icon} {msg}[/]")
        if not ok:
            has_error = True

    if has_error:
        console.print("\n[yellow]Some checks failed.[/]")
        console.print("[dim]Follow the suggestions above to fix them.[/]")
    else:
        console.print("\n[green]All checks passed. Tanuki is ready.[/]")


@app.command()
def run() -> None:
    console.print("[bold bright_cyan]Run[/] → (pending) Autonomous loop: task → changes → tests → review.")


@app.command()
def ui(
    port: int = typer.Option(3847, "--port", "-p", help="Local UI port"),
) -> None:
    serve(port=port)