"""Disc information command."""

from typing import Annotated

import anyio
import structlog
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from riparr.config import get_settings

app = typer.Typer(help="Display disc information.")
console = Console()
log = structlog.get_logger()


@app.callback(invoke_without_command=True)
def info(
    device: Annotated[
        str | None,
        typer.Argument(help="Device path (e.g., /dev/sr0)"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Output as JSON"),
    ] = False,
) -> None:
    """Display information about a disc.

    Shows disc metadata, titles, audio tracks, and subtitles.
    """
    settings = get_settings()
    device = device or settings.default_device

    console.print(f"[bold blue]Scanning disc in[/] [cyan]{device}[/]...\n")

    anyio.run(_show_info, device, json_output)


async def _show_info(device: str, json_output: bool) -> None:
    """Scan and display disc information."""
    from riparr.metadata.arm_api import lookup_disc
    from riparr.metadata.dvdid import compute_dvd_id
    from riparr.ripper.makemkv import MakeMKV

    settings = get_settings()
    makemkv = MakeMKV(settings.makemkv_path)

    # Scan disc
    disc = await makemkv.scan_disc(device)

    if not disc.titles:
        console.print("[red]No titles found on disc. Is there a disc in the drive?[/]")
        raise typer.Exit(1)

    # Compute DVD ID
    try:
        dvd_id = compute_dvd_id(device)
        disc.dvd_id = dvd_id
    except Exception as e:
        log.warning("Failed to compute DVD ID", error=str(e))
        dvd_id = None

    # Lookup metadata
    metadata = None
    if dvd_id:
        try:
            metadata = await lookup_disc(dvd_id)
            disc.metadata = metadata
        except Exception as e:
            log.warning("Metadata lookup failed", error=str(e))

    if json_output:
        import json

        output = disc.model_dump(mode="json")
        console.print(json.dumps(output, indent=2))
        return

    # Display disc info panel
    disc_info = f"""[bold]Name:[/] {disc.name or 'Unknown'}
[bold]Type:[/] {disc.disc_type.value}
[bold]DVD ID:[/] {dvd_id or 'N/A'}
[bold]Titles:[/] {len(disc.titles)}"""

    if metadata:
        disc_info += f"""

[bold green]Metadata Found:[/]
  Title: {metadata.title}
  Year: {metadata.year}
  Type: {metadata.media_type}
  IMDB: {metadata.imdb_id or 'N/A'}"""

    console.print(Panel(disc_info, title="Disc Information", border_style="blue"))

    # Display titles table
    table = Table(title="Titles", show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=4)
    table.add_column("Duration", width=10)
    table.add_column("Chapters", width=10)
    table.add_column("Size", width=10)
    table.add_column("Video", width=20)
    table.add_column("Audio", width=30)

    for title in disc.titles:
        audio_info = ", ".join(
            f"{a.language} ({a.codec})" for a in title.audio_tracks[:2]
        )
        if len(title.audio_tracks) > 2:
            audio_info += f" +{len(title.audio_tracks) - 2}"

        table.add_row(
            str(title.index),
            title.duration_str,
            str(title.chapters),
            title.size_str,
            f"{title.video_codec} {title.resolution}",
            audio_info or "N/A",
        )

    console.print()
    console.print(table)

    # Show title selection suggestion
    from riparr.ripper.selector import TitleSelector

    selector = TitleSelector(settings)
    selected = selector.select_titles(disc.titles)

    if selected:
        console.print(
            f"\n[green]Suggested titles to rip:[/] "
            f"{', '.join(str(t.index) for t in selected)}"
        )
        classification = selector.classify_disc(disc.titles)
        console.print(f"[dim]Disc classified as:[/] {classification}")
