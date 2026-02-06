"""Rich-based dashboard for watch mode."""

from datetime import datetime

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from riparr.tui.status import StatusTracker, TaskState


class Dashboard:
    """Live-updating dashboard for riparr watch mode."""

    def __init__(self, tracker: StatusTracker) -> None:
        """Initialize dashboard.

        Args:
            tracker: Status tracker to display
        """
        self.tracker = tracker
        self.console = Console()
        self._live: Live | None = None

    def _make_header(self) -> Panel:
        """Create header panel."""
        header_text = Text()
        header_text.append("  RIPARR  ", style="bold white on blue")
        header_text.append("  Watch Mode  ", style="dim")
        header_text.append(
            f"  {datetime.now().strftime('%H:%M:%S')}  ",
            style="dim cyan",
        )
        return Panel(header_text, style="blue", height=3)

    def _make_rip_panel(self) -> Panel:
        """Create rip status panel."""
        rip = self.tracker.rip

        if rip.state == TaskState.IDLE:
            content = Text("Waiting for disc...", style="dim")
            title = "Rip Status"
            border_style = "dim"
        elif rip.state == TaskState.ACTIVE:
            # Create progress display
            progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold]{task.description}"),
                BarColumn(bar_width=30),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                expand=True,
            )
            progress.add_task(
                f"Title {rip.title_progress}",
                total=100,
                completed=rip.progress,
            )

            info_table = Table.grid(padding=(0, 2))
            info_table.add_column(style="cyan")
            info_table.add_column()
            info_table.add_row("Device:", rip.device)
            info_table.add_row("Disc:", rip.disc_name or "Unknown")
            info_table.add_row("Elapsed:", rip.elapsed)

            content = Group(info_table, "", progress)
            title = "[bold green]Ripping"
            border_style = "green"
        elif rip.state == TaskState.COMPLETE:
            content = Text(f"Completed: {rip.disc_name}", style="green")
            title = "Rip Complete"
            border_style = "green"
        else:  # FAILED
            content = Text(f"Error: {rip.error}", style="red")
            title = "Rip Failed"
            border_style = "red"

        return Panel(content, title=title, border_style=border_style, height=10)

    def _make_encode_panel(self) -> Panel:
        """Create encode status panel."""
        encode = self.tracker.encode

        if encode.state == TaskState.IDLE:
            content = Text("No active encoding", style="dim")
            title = "Encode Status"
            border_style = "dim"
        elif encode.state == TaskState.ACTIVE:
            progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold]{task.description}"),
                BarColumn(bar_width=30),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                expand=True,
            )
            progress.add_task(
                encode.filename[:40] + "..." if len(encode.filename) > 40 else encode.filename,
                total=100,
                completed=encode.progress,
            )

            info_table = Table.grid(padding=(0, 2))
            info_table.add_column(style="cyan")
            info_table.add_column()
            info_table.add_row("FPS:", f"{encode.fps:.1f}" if encode.fps else "—")
            info_table.add_row("ETA:", encode.eta or "—")
            info_table.add_row("Elapsed:", encode.elapsed)

            content = Group(info_table, "", progress)
            title = "[bold blue]Encoding"
            border_style = "blue"
        elif encode.state == TaskState.COMPLETE:
            content = Text(f"Completed: {encode.filename}", style="green")
            title = "Encode Complete"
            border_style = "green"
        else:  # FAILED
            content = Text(f"Error: {encode.error}", style="red")
            title = "Encode Failed"
            border_style = "red"

        return Panel(content, title=title, border_style=border_style, height=10)

    def _make_queue_panel(self) -> Panel:
        """Create queue panel."""
        queue = self.tracker.queue

        if not queue:
            content = Text("Queue is empty", style="dim")
        else:
            table = Table(show_header=True, header_style="bold", expand=True)
            table.add_column("Name", ratio=3)
            table.add_column("Size", width=10)
            table.add_column("Status", width=12)

            for item in queue[:8]:  # Show max 8 items
                status_style = {
                    "ready": "green",
                    "transcoding": "yellow",
                    "failed": "red",
                }.get(item.status, "dim")

                table.add_row(
                    item.name[:35] + "..." if len(item.name) > 35 else item.name,
                    item.size_str,
                    f"[{status_style}]{item.status}[/]",
                )

            if len(queue) > 8:
                table.add_row(
                    f"[dim]... and {len(queue) - 8} more[/]",
                    "",
                    "",
                )

            content = table

        return Panel(
            content,
            title=f"Encode Queue ({len(queue)})",
            border_style="cyan",
            height=12,
        )

    def _make_events_panel(self) -> Panel:
        """Create recent events panel."""
        events = self.tracker.recent_events

        if not events:
            content = Text("No recent events", style="dim")
        else:
            content = Text("\n".join(events[-6:]))  # Show last 6 events

        return Panel(
            content,
            title="Recent Events",
            border_style="dim",
            height=9,
        )

    def _make_layout(self) -> Layout:
        """Create the dashboard layout."""
        layout = Layout()

        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="footer", size=9),
        )

        layout["main"].split_row(
            Layout(name="left"),
            Layout(name="right", ratio=2),
        )

        layout["left"].split_column(
            Layout(name="rip"),
            Layout(name="encode"),
        )

        # Populate layout
        layout["header"].update(self._make_header())
        layout["rip"].update(self._make_rip_panel())
        layout["encode"].update(self._make_encode_panel())
        layout["right"].update(self._make_queue_panel())
        layout["footer"].update(self._make_events_panel())

        return layout

    def refresh(self) -> None:
        """Refresh the display."""
        if self._live:
            self._live.update(self._make_layout())

    def start(self) -> Live:
        """Start the live display.

        Returns:
            Live context manager
        """
        self._live = Live(
            self._make_layout(),
            console=self.console,
            refresh_per_second=2,
            screen=True,
        )

        # Set up automatic refresh on status updates
        self.tracker.set_update_callback(self.refresh)

        return self._live

    def stop(self) -> None:
        """Stop the live display."""
        if self._live:
            self._live.stop()
            self._live = None
