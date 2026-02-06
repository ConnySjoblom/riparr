"""Manual rip command."""

from pathlib import Path
from typing import Annotated

import anyio
import structlog
import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from riparr.config import get_settings

app = typer.Typer(help="Rip disc from optical drive.")
console = Console()
log = structlog.get_logger()


@app.callback(invoke_without_command=True)
def rip(
    device: Annotated[
        str | None,
        typer.Argument(help="Device path (e.g., /dev/sr0)"),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output directory override"),
    ] = None,
    no_encode: Annotated[
        bool,
        typer.Option("--no-encode", help="Skip encoding, only rip raw MKV"),
    ] = False,
    title: Annotated[
        int | None,
        typer.Option("--title", "-t", help="Rip specific title number only"),
    ] = None,
    min_duration: Annotated[
        int | None,
        typer.Option("--min-duration", "-m", help="Minimum title duration in seconds"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Show what would be done without ripping"),
    ] = False,
) -> None:
    """Rip disc from optical drive.

    If no device is specified, uses the default device from configuration.
    """
    settings = get_settings()
    device = device or settings.default_device
    output_dir = output or settings.raw_dir

    console.print(f"[bold blue]Riparr[/] - Ripping from [cyan]{device}[/]")

    if dry_run:
        console.print("[yellow]Dry run mode - no changes will be made[/]")

    anyio.run(
        _run_rip,
        device,
        output_dir,
        no_encode,
        title,
        min_duration,
        dry_run,
    )


async def _run_rip(
    device: str,
    output_dir: Path,
    no_encode: bool,
    title_num: int | None,
    min_duration: int | None,
    dry_run: bool,
) -> None:
    """Execute the rip operation."""
    from riparr.core.job import Job, JobStatus
    from riparr.metadata.dvdid import compute_dvd_id
    from riparr.metadata.arm_api import lookup_disc
    from riparr.ripper.makemkv import MakeMKV
    from riparr.ripper.selector import TitleSelector
    from riparr.encoder.handbrake import HandBrake
    from riparr.output.naming import OutputNamer

    settings = get_settings()
    makemkv = MakeMKV(settings.makemkv_path)
    selector = TitleSelector(settings)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        # Step 1: Scan disc
        task = progress.add_task("Scanning disc...", total=None)
        disc = await makemkv.scan_disc(device)
        progress.update(task, description=f"Found {len(disc.titles)} titles on disc")

        if not disc.titles:
            console.print("[red]No titles found on disc[/]")
            raise typer.Exit(1)

        # Step 2: Compute DVD ID and lookup metadata
        progress.update(task, description="Computing disc ID...")
        try:
            dvd_id = compute_dvd_id(device)
            disc.dvd_id = dvd_id
            log.info("Computed DVD ID", dvd_id=dvd_id)

            progress.update(task, description="Looking up metadata...")
            metadata = await lookup_disc(dvd_id)
            if metadata:
                disc.metadata = metadata
                console.print(f"[green]Found:[/] {metadata.title} ({metadata.year})")
        except Exception as e:
            log.warning("Metadata lookup failed", error=str(e))

        # Step 3: Select titles
        progress.update(task, description="Selecting titles...")
        if title_num is not None:
            selected = [t for t in disc.titles if t.index == title_num]
        else:
            selected = selector.select_titles(disc.titles, min_duration)

        console.print(f"[cyan]Selected {len(selected)} title(s) for ripping[/]")

        if dry_run:
            console.print("\n[bold]Titles to rip:[/]")
            for t in selected:
                console.print(f"  Title {t.index}: {t.duration_str} - {t.chapters} chapters")
            return

        # Step 4: Rip titles
        job = Job(disc=disc, selected_titles=selected, output_dir=output_dir)

        for idx, t in enumerate(selected, 1):
            progress.update(task, description=f"Ripping title {t.index} ({idx}/{len(selected)})...")
            await makemkv.rip_title(
                device,
                t.index,
                output_dir,
                progress_callback=lambda p: progress.update(task, description=f"Ripping: {p:.1f}%"),
            )

        job.status = JobStatus.RIPPED
        console.print(f"[green]Ripping complete![/] Files saved to {output_dir}")

        # Step 5: Encode (if enabled)
        if not no_encode and settings.encode_enabled:
            progress.update(task, description="Starting encoding...")
            handbrake = HandBrake(settings.handbrake_path)
            namer = OutputNamer(settings)

            for mkv_file in output_dir.glob("*.mkv"):
                output_path = namer.get_output_path(disc, mkv_file)
                output_path.parent.mkdir(parents=True, exist_ok=True)

                progress.update(task, description=f"Encoding {mkv_file.name}...")
                await handbrake.encode(
                    mkv_file,
                    output_path,
                    preset=settings.handbrake_preset,
                    video_codec=settings.video_codec,
                    quality=settings.video_quality,
                    progress_callback=lambda info: progress.update(
                        task, description=f"Encoding: {info.percent:.1f}%"
                    ),
                )

            job.status = JobStatus.COMPLETE
            console.print(f"[green]Encoding complete![/] Files saved to {settings.output_dir}")

        # Step 6: Eject disc
        if settings.eject_after_rip:
            progress.update(task, description="Ejecting disc...")
            await _eject_disc(device)
            console.print("[green]Disc ejected[/]")


async def _eject_disc(device: str) -> None:
    """Eject the disc from the drive."""
    import subprocess

    proc = await anyio.to_thread.run_sync(
        lambda: subprocess.run(["eject", device], capture_output=True)
    )
    if proc.returncode != 0:
        log.warning("Failed to eject disc", device=device)
