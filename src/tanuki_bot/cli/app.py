from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt

from tanuki_bot.core.version import __version__
from tanuki_bot.projects.commands import project_app
from tanuki_bot.tasks.commands import task_app
from tanuki_bot.ui.branding import show_banner
from tanuki_bot.ui_web.server import serve

from tanuki_bot.core.init import init_project
from tanuki_bot.core.doctor import run_doctor

from tanuki_bot.config.config import get_model, set_model, set_openai_key
from tanuki_bot.core.plan import plan_from_brief

from tanuki_bot.core.runner import run_autonomous

app = typer.Typer(add_completion=False, no_args_is_help=False)
app.add_typer(project_app, name="project")
app.add_typer(task_app, name="task")

console = Console()

ACCENT = "dark_orange"
LINK = "dark_orange"
ERR = "red"


def _print_nice_openai_error(message: str) -> None:
    msg_lower = message.lower()

    if "quota" in msg_lower or "billing" in msg_lower or "insufficient_quota" in msg_lower or "429" in msg_lower:
        title = "[bold red]OpenAI API quota / billing issue[/]"
        body = (
            "[bold]Tanuki can't call the OpenAI API because your API project has no available credits "
            "or billing isn't enabled.[/]\n\n"
            "[bold]Fix it here:[/]\n"
            f"• Billing: [{LINK}]https://platform.openai.com/settings/billing[/]\n"
            f"• Usage:  [{LINK}]https://platform.openai.com/usage[/]\n\n"
            "[bold]Then retry:[/]\n"
            "• [green]tanuki plan[/]\n\n"
            "[dim]Note: ChatGPT Plus is separate from API billing. The API uses OpenAI Platform credits.[/]"
        )
    elif "authentication" in msg_lower or "invalid" in msg_lower or "api key" in msg_lower:
        title = "[bold red]OpenAI authentication failed[/]"
        body = (
            "[bold]Your API key looks invalid/revoked or not configured.[/]\n\n"
            "[bold]Fix it:[/]\n"
            f"• Create / manage keys: [{LINK}]https://platform.openai.com/api-keys[/]\n"
            "• Re-run: [green]tanuki setup[/]\n\n"
            "[bold]Then retry:[/]\n"
            "• [green]tanuki doctor[/]\n"
            "• [green]tanuki plan[/]"
        )
    elif "model" in msg_lower and ("invalid" in msg_lower or "not found" in msg_lower or "400" in msg_lower):
        title = "[bold red]Invalid model configured[/]"
        body = (
            "[bold]The configured model name is not accepted by the API.[/]\n\n"
            "[bold]Fix it:[/]\n"
            "• Show current: [green]tanuki model[/]\n"
            "• Set one:      [green]tanuki model gpt-5-mini[/]\n\n"
            "[bold]Then retry:[/]\n"
            "• [green]tanuki plan[/]\n\n"
            "[dim]Models available depend on your OpenAI project and permissions.[/]"
        )
    else:
        title = "[bold red]Tanuki failed[/]"
        body = (
            f"[bold]Error:[/]\n{message}\n\n"
            "[bold]Useful links:[/]\n"
            f"• API keys: [{LINK}]https://platform.openai.com/api-keys[/]\n"
            f"• Usage:    [{LINK}]https://platform.openai.com/usage[/]\n"
            f"• Billing:  [{LINK}]https://platform.openai.com/settings/billing[/]\n\n"
            "[dim]If this persists, run `tanuki doctor` and share the output.[/]"
        )

    console.print(
        Panel(
            body,
            title=title,
            border_style=ERR,
            padding=(1, 2),
        )
    )


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="Show version and exit"),
) -> None:
    if version:
        console.print(f"[bold {ACCENT}]tanuki[/] v{__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        show_banner(console)

        table = Table(title="Commands", show_header=True, header_style="bold bright_white")
        table.add_column("Command", style=ACCENT, no_wrap=True)
        table.add_column("What it does", style="white")

        def section(title: str) -> None:
            table.add_row(f"[bold]{title}[/]", "", style="dim")

        section("Getting started")
        table.add_row("tanuki project up --path <repo>", "Attach a repo folder and set it active")
        table.add_row("tanuki init", "Initialize Tanuki memory & backlog for the active project")
        table.add_row("tanuki setup", "Interactive setup (OpenAI API key + default model)")
        table.add_row("tanuki doctor", "Check Tanuki setup and detect missing steps")
        table.add_row("tanuki model [name]", "Show or set the model (e.g. gpt-5-mini)")
        table.add_row("tanuki plan", "Generate ARCHITECTURE.md + tasks.json from a brief")

        section("Tasks")
        table.add_row("tanuki task list", "View current tasks (filters: --status/--priority/--tag)")
        table.add_row("tanuki task show <id>", "Show full details for one task")
        table.add_row("tanuki task add", "Append new tasks from an incremental request")

        section("Projects")
        table.add_row("tanuki project list", "List registered projects")
        table.add_row("tanuki project show [id]", "Show project info (defaults to active)")
        table.add_row("tanuki project use <id>", "Switch the active project")
        table.add_row("tanuki project rename <id> --name <name>", "Rename a project (registry only)")
        table.add_row("tanuki project set-path <id> --path <folder>", "Update a project's repo path (registry only)")
        table.add_row("tanuki project remove <id>", "Remove a project from Tanuki registry (repo not deleted)")

        section("UI / automation")
        table.add_row("tanuki ui", "Open the local dashboard (projects + backlog)")
        table.add_row("tanuki run", "Run the autonomous loop (task -> changes -> checks -> PR -> status)")

        console.print(table)
        console.print('\n[dim]Tip:[/] register current folder with [bold]tanuki project up --path .[/]\n')


@app.command()
def init() -> None:
    """Initialize Tanuki workspace for the active project."""
    try:
        path = init_project()
        console.print(f"[green]Tanuki initialized[/] at [dim]{path}[/]")
    except RuntimeError:
        console.print("[bold red]Error[/]: No active project. Run [bold]tanuki project up --path <repo>[/]")


@app.command()
def setup() -> None:
    """Interactive setup: API key + default model."""
    console.print(f"[bold {ACCENT}]Tanuki setup[/]\n")

    key = Prompt.ask("OpenAI API key", password=True)
    if key.strip():
        set_openai_key(key.strip())
        console.print("[green]API key saved[/]")
    else:
        console.print("[yellow]Skipped API key[/]")

    current = get_model()
    console.print(f"\nCurrent model: [bold]{current}[/]")
    model_name = Prompt.ask("Model to use", default=current)
    set_model(model_name.strip())
    console.print(f"[green]Model set[/] -> {get_model()}")


@app.command()
def config() -> None:
    """Backwards-compatible alias for setup (so users can run tanuki config)."""
    setup()


@app.command()
def model(
    name: str | None = typer.Argument(None, help="Optional model name to set, e.g. gpt-5-mini"),
) -> None:
    """Show or set the current model."""
    if name:
        set_model(name)
        console.print(f"[green]Model set[/] -> {get_model()}")
    else:
        console.print(f"[bold {ACCENT}]Model[/] -> {get_model()}")


@app.command()
def plan(
    brief: str | None = typer.Option(None, "--brief", "-b", help="Project brief. If omitted, Tanuki will ask interactively."),
    file: str | None = typer.Option(None, "--file", "-f", help="Load brief from a text/markdown file."),
) -> None:
    """Generate ARCHITECTURE.md and tasks.json for the active project."""
    if file:
        brief_text = Path(file).expanduser().read_text(encoding="utf-8")
    elif brief:
        brief_text = brief
    else:
        console.print(f"[bold {ACCENT}]Tanuki plan[/]\n")
        brief_text = Prompt.ask("Paste a short brief (one paragraph is enough)")

    try:
        arch_path, tasks_path = plan_from_brief(brief_text)
    except RuntimeError as e:
        console.print()
        _print_nice_openai_error(str(e))
        console.print()
        console.print("[dim]Suggested flow:[/] [bold]tanuki project up[/] -> [bold]tanuki init[/] -> [bold]tanuki setup[/] -> [bold]tanuki plan[/]")
        raise typer.Exit(code=1)

    console.print("Plan generated")
    console.print(f"[dim]Architecture:[/] {arch_path}")
    console.print(f"[dim]Tasks:[/] {tasks_path}")


@app.command()
def doctor() -> None:
    """Run diagnostics to check Tanuki setup."""
    console.print(f"[bold {ACCENT}]Tanuki doctor[/]\n")

    results = run_doctor()
    has_error = False

    for ok, msg in results:
        color = "green" if ok else "red"
        prefix = "OK" if ok else "FAIL"
        console.print(f"[{color}]{prefix} {msg}[/]")
        if not ok:
            has_error = True

    if has_error:
        console.print("\n[yellow]Some checks failed.[/]")
        console.print("[dim]Follow the suggestions above to fix them.[/]")
    else:
        console.print("\n[green]All checks passed. Tanuki is ready.[/]")


@app.command()
def run(
    max_tasks: int = typer.Option(1, "--max-tasks", "-n", help="Max tasks to process in this run"),
    no_pr: bool = typer.Option(False, "--no-pr", help="Do not create PRs automatically"),
    keep_going: bool = typer.Option(False, "--keep-going", help="Keep processing tasks even if one fails"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Do not write/commit/push; just simulate decisions"),
) -> None:
    """
    Autonomous loop: picks next tasks, creates branches, applies changes, runs checks, pushes, opens PR, updates status.
    """
    console.print(f"[bold {ACCENT}]Tanuki run[/]\n")

    try:
        results = run_autonomous(
            max_tasks=max_tasks,
            create_pr=not no_pr,
            keep_going=keep_going,
            dry_run=dry_run,
        )
    except Exception as e:
        console.print(f"[bold red]Error[/]: {e}")
        raise typer.Exit(code=1)

    if not results:
        console.print("No runnable tasks found.")
        return

    for r in results:
        if r.get("ok"):
            task_id = r.get("task_id", "?")
            status = "ok"
            if r.get("message"):
                console.print(r["message"])
                continue
            console.print(f"Task {task_id}: {status}")
            if r.get("branch"):
                console.print(f"[dim]branch:[/] {r['branch']}")
            if r.get("pr"):
                console.print(f"[dim]pr:[/] {r['pr']}")
            if r.get("dry_run"):
                console.print("[dim]dry-run: true[/]")
        else:
            console.print(f"[bold red]Run failed[/]: {r.get('error','unknown error')}")


@app.command()
def ui(
    port: int = typer.Option(3847, "--port", "-p", help="Local UI port"),
) -> None:
    serve(port=port)