"""Main CLI application."""

import structlog
import typer
from rich.console import Console

from riparr import __version__
from riparr.cli.commands import info, queue, rip, watch
from riparr.config import get_settings

app = typer.Typer(
    name="riparr",
    help="Modern DVD/Blu-ray ripper with automated disc detection and encoding.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()


def configure_logging() -> None:
    """Configure structured logging."""
    settings = get_settings()

    processors = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.log_json:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def version_callback(value: bool) -> None:
    """Show version and exit."""
    if value:
        console.print(f"[bold blue]riparr[/] version [green]{__version__}[/]")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: bool = typer.Option(
        None,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Riparr - Modern DVD/Blu-ray ripper."""
    configure_logging()


# Register subcommands
app.add_typer(rip.app, name="rip")
app.add_typer(watch.app, name="watch")
app.add_typer(info.app, name="info")
app.add_typer(queue.app, name="queue")


@app.command()
def config() -> None:
    """Display current configuration."""
    from rich.panel import Panel
    from rich.table import Table

    settings = get_settings()

    table = Table(title="Riparr Configuration", show_header=True)
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    for field_name, field_info in settings.model_fields.items():
        value = getattr(settings, field_name)
        # Mask sensitive values
        if "key" in field_name.lower() and value:
            display_value = "***" + str(value)[-4:] if len(str(value)) > 4 else "****"
        else:
            display_value = str(value)
        table.add_row(field_name, display_value)

    console.print(Panel(table))


def main() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
