"""Watch daemon command for automatic disc ripping."""

from typing import Annotated

import anyio
import structlog
import typer
from rich.console import Console

from riparr.config import get_settings

app = typer.Typer(help="Watch for disc insertions and auto-rip.")
console = Console()
log = structlog.get_logger()


@app.callback(invoke_without_command=True)
def watch(
    devices: Annotated[
        list[str] | None,
        typer.Argument(help="Device paths to watch (default: auto-detect)"),
    ] = None,
    once: Annotated[
        bool,
        typer.Option("--once", help="Process one disc and exit"),
    ] = False,
    gui: Annotated[
        bool,
        typer.Option("--gui", "-g", help="Show live dashboard UI"),
    ] = False,
) -> None:
    """Start daemon to watch for disc insertions.

    Monitors optical drives for disc insertion events and automatically
    initiates ripping when a disc is detected.

    Use --gui for a live dashboard showing rip progress, encoding status,
    and the encode queue.
    """
    settings = get_settings()

    device_list = devices or [settings.default_device]

    if not gui:
        console.print("[bold blue]Riparr Watch Mode[/]")
        console.print(f"Monitoring devices: {', '.join(device_list)}")
        console.print(f"Detection method: {settings.detection_method}")
        console.print("\n[dim]Press Ctrl+C to stop[/]\n")

    try:
        anyio.run(_run_watch, device_list, once, gui)
    except KeyboardInterrupt:
        if not gui:
            console.print("\n[yellow]Shutting down...[/]")


async def _run_watch(devices: list[str], once: bool, gui: bool) -> None:
    """Run the watch daemon."""
    from riparr.detection.watcher import DiscWatcher
    from riparr.queue.manager import QueueManager
    from riparr.tui.status import get_tracker

    settings = get_settings()
    watcher = DiscWatcher(devices, method=settings.detection_method)
    tracker = get_tracker()
    queue_manager = QueueManager(settings, tracker=tracker if gui else None)

    async def on_disc_inserted(device: str) -> None:
        """Handle disc insertion event."""
        log.info("Disc inserted", device=device)
        if gui:
            tracker.add_event(f"[green]Disc detected:[/] {device}")

        try:
            await queue_manager.process_disc(device)
        except Exception as e:
            log.error("Failed to process disc", device=device, error=str(e))

    async def on_disc_removed(device: str) -> None:
        """Handle disc removal event."""
        log.info("Disc removed", device=device)
        if gui:
            tracker.add_event(f"[yellow]Disc removed:[/] {device}")

    if gui:
        await _run_with_dashboard(
            watcher, queue_manager, tracker, devices, once,
            on_disc_inserted, on_disc_removed,
        )
    else:
        # Start queue processor and disc watcher in parallel
        async with anyio.create_task_group() as tg:
            # Start encode queue processor in background
            tg.start_soon(queue_manager.process_queue)

            # Start disc watcher
            await watcher.start(
                on_insert=on_disc_inserted,
                on_remove=on_disc_removed,
                once=once,
            )

            # Stop queue processor when watcher exits
            queue_manager.stop()
            tg.cancel_scope.cancel()


async def _run_with_dashboard(
    watcher,
    queue_manager,
    tracker,
    devices: list[str],
    once: bool,
    on_insert,
    on_remove,
) -> None:
    """Run watch mode with live dashboard."""
    from riparr.tui.dashboard import Dashboard

    dashboard = Dashboard(tracker)

    # Add initial event
    tracker.add_event(f"Watching devices: {', '.join(devices)}")

    async def update_queue_display() -> None:
        """Periodically update queue display."""
        from riparr.tui.status import QueuedItem

        while True:
            await anyio.sleep(2)
            jobs = queue_manager.markers.list_jobs()
            # Filter out completed items - they're not really "queued"
            items = [
                QueuedItem(
                    name=j.name,
                    size_str=j.size_str,
                    status=j.status,
                    created_at=j.created_at,
                )
                for j in jobs
                if j.status != "complete"
            ]
            tracker.update_queue(items)

    with dashboard.start():
        async with anyio.create_task_group() as tg:
            # Start encode queue processor in background
            tg.start_soon(queue_manager.process_queue)

            # Start queue display updater
            tg.start_soon(update_queue_display)

            # Start disc watcher
            await watcher.start(
                on_insert=on_insert,
                on_remove=on_remove,
                once=once,
            )

            # Stop queue processor and cancel background tasks when watcher stops
            queue_manager.stop()
            tg.cancel_scope.cancel()
