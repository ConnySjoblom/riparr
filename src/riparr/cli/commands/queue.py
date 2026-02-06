"""Queue management commands."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from riparr.config import get_settings

app = typer.Typer(help="Manage encoding queue.")
console = Console()


@app.command("list")
def list_queue(
    status: Annotated[
        str | None,
        typer.Option("--status", "-s", help="Filter by status (ready, transcoding, failed)"),
    ] = None,
) -> None:
    """List items in the encoding queue."""
    from riparr.queue.markers import MarkerManager

    settings = get_settings()
    markers = MarkerManager(settings.raw_dir)

    jobs = markers.list_jobs(status_filter=status)

    if not jobs:
        console.print("[dim]No items in queue[/]")
        return

    table = Table(title="Encoding Queue", show_header=True, header_style="bold cyan")
    table.add_column("Name", style="cyan")
    table.add_column("Status", width=12)
    table.add_column("Size", width=10)
    table.add_column("Created", width=20)

    for job in jobs:
        status_style = {
            "ready": "green",
            "transcoding": "yellow",
            "failed": "red",
            "complete": "blue",
        }.get(job.status, "dim")

        table.add_row(
            job.name,
            f"[{status_style}]{job.status}[/]",
            job.size_str,
            job.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


@app.command("retry")
def retry_failed(
    name: Annotated[
        str | None,
        typer.Argument(help="Job name to retry (or 'all' for all failed)"),
    ] = None,
) -> None:
    """Retry failed encoding jobs."""
    from riparr.queue.markers import MarkerManager

    settings = get_settings()
    markers = MarkerManager(settings.raw_dir)

    if name == "all":
        count = markers.retry_all_failed()
        console.print(f"[green]Marked {count} job(s) for retry[/]")
    elif name:
        if markers.retry_job(name):
            console.print(f"[green]Job '{name}' marked for retry[/]")
        else:
            console.print(f"[red]Job '{name}' not found or not in failed state[/]")
    else:
        console.print("[yellow]Specify a job name or 'all' to retry all failed[/]")


@app.command("clear")
def clear_queue(
    status: Annotated[
        str | None,
        typer.Option("--status", "-s", help="Clear only jobs with this status"),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Skip confirmation"),
    ] = False,
) -> None:
    """Clear items from the queue."""
    from riparr.queue.markers import MarkerManager

    settings = get_settings()
    markers = MarkerManager(settings.raw_dir)

    jobs = markers.list_jobs(status_filter=status)

    if not jobs:
        console.print("[dim]No items to clear[/]")
        return

    if not force:
        confirmed = typer.confirm(f"Clear {len(jobs)} item(s) from queue?")
        if not confirmed:
            raise typer.Abort()

    count = markers.clear_jobs(status_filter=status)
    console.print(f"[green]Cleared {count} item(s) from queue[/]")


@app.command("add")
def add_to_queue(
    path: Annotated[
        Path,
        typer.Argument(help="Path to MKV file or directory to add"),
    ],
) -> None:
    """Add existing MKV files to encoding queue."""
    from riparr.queue.markers import MarkerManager

    settings = get_settings()
    markers = MarkerManager(settings.raw_dir)

    if path.is_file():
        if path.suffix.lower() != ".mkv":
            console.print("[red]File must be an MKV file[/]")
            raise typer.Exit(1)
        markers.create_marker(path, "ready")
        console.print(f"[green]Added to queue:[/] {path.name}")
    elif path.is_dir():
        count = 0
        for mkv in path.glob("*.mkv"):
            markers.create_marker(mkv, "ready")
            count += 1
        console.print(f"[green]Added {count} file(s) to queue[/]")
    else:
        console.print(f"[red]Path not found:[/] {path}")
        raise typer.Exit(1)
