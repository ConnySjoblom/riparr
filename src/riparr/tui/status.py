"""Shared status tracker for TUI updates."""

from dataclasses import dataclass, field
from datetime import datetime
from collections.abc import Callable
from enum import StrEnum
from threading import Lock


class TaskState(StrEnum):
    """State of a tracked task."""

    IDLE = "idle"
    ACTIVE = "active"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class RipStatus:
    """Current rip operation status."""

    state: TaskState = TaskState.IDLE
    device: str = ""
    disc_name: str = ""
    current_title: int = 0
    total_titles: int = 0
    progress: float = 0.0
    started_at: datetime | None = None
    error: str | None = None

    @property
    def title_progress(self) -> str:
        """Format title progress."""
        if self.total_titles == 0:
            return "—"
        return f"{self.current_title}/{self.total_titles}"

    @property
    def elapsed(self) -> str:
        """Format elapsed time."""
        if self.started_at is None:
            return "—"
        delta = datetime.now() - self.started_at
        minutes, seconds = divmod(int(delta.total_seconds()), 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m {seconds}s"


@dataclass
class EncodeStatus:
    """Current encode operation status."""

    state: TaskState = TaskState.IDLE
    filename: str = ""
    progress: float = 0.0
    fps: float = 0.0
    eta: str = ""
    started_at: datetime | None = None
    error: str | None = None

    @property
    def elapsed(self) -> str:
        """Format elapsed time."""
        if self.started_at is None:
            return "—"
        delta = datetime.now() - self.started_at
        minutes, seconds = divmod(int(delta.total_seconds()), 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m {seconds}s"


@dataclass
class QueuedItem:
    """Item waiting in the encode queue."""

    name: str
    size_str: str
    status: str
    created_at: datetime


@dataclass
class StatusTracker:
    """Thread-safe status tracker for TUI updates."""

    rip: RipStatus = field(default_factory=RipStatus)
    encode: EncodeStatus = field(default_factory=EncodeStatus)
    queue: list[QueuedItem] = field(default_factory=list)
    recent_events: list[str] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock, repr=False)
    _on_update: Callable[[], None] | None = field(default=None, repr=False)

    def set_update_callback(self, callback: Callable[[], None]) -> None:
        """Set callback to trigger on status updates."""
        self._on_update = callback

    def _notify(self) -> None:
        """Notify listeners of update."""
        if self._on_update:
            self._on_update()

    def add_event(self, message: str) -> None:
        """Add an event to the recent events list."""
        with self._lock:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.recent_events.append(f"[dim]{timestamp}[/] {message}")
            # Keep last 10 events
            if len(self.recent_events) > 10:
                self.recent_events.pop(0)
        self._notify()

    # Rip status updates
    def start_rip(self, device: str, disc_name: str, total_titles: int) -> None:
        """Mark rip as started."""
        with self._lock:
            self.rip = RipStatus(
                state=TaskState.ACTIVE,
                device=device,
                disc_name=disc_name,
                current_title=0,
                total_titles=total_titles,
                progress=0.0,
                started_at=datetime.now(),
            )
        self.add_event(f"[green]Started ripping[/] {disc_name}")

    def update_rip(self, title: int, progress: float) -> None:
        """Update rip progress."""
        with self._lock:
            self.rip.current_title = title
            self.rip.progress = progress
        self._notify()

    def complete_rip(self) -> None:
        """Mark rip as complete."""
        with self._lock:
            disc_name = self.rip.disc_name
            self.rip.state = TaskState.COMPLETE
            self.rip.progress = 100.0
        self.add_event(f"[green]Completed ripping[/] {disc_name}")

    def fail_rip(self, error: str) -> None:
        """Mark rip as failed."""
        with self._lock:
            self.rip.state = TaskState.FAILED
            self.rip.error = error
        self.add_event(f"[red]Rip failed:[/] {error}")

    def clear_rip(self) -> None:
        """Clear rip status."""
        with self._lock:
            self.rip = RipStatus()
        self._notify()

    # Encode status updates
    def start_encode(self, filename: str) -> None:
        """Mark encode as started."""
        with self._lock:
            self.encode = EncodeStatus(
                state=TaskState.ACTIVE,
                filename=filename,
                progress=0.0,
                started_at=datetime.now(),
            )
        self.add_event(f"[blue]Started encoding[/] {filename}")

    def update_encode(self, progress: float, fps: float = 0.0, eta: str = "") -> None:
        """Update encode progress."""
        with self._lock:
            self.encode.progress = progress
            self.encode.fps = fps
            self.encode.eta = eta
        self._notify()

    def complete_encode(self) -> None:
        """Mark encode as complete."""
        with self._lock:
            filename = self.encode.filename
            self.encode.state = TaskState.COMPLETE
            self.encode.progress = 100.0
        self.add_event(f"[green]Completed encoding[/] {filename}")

    def fail_encode(self, error: str) -> None:
        """Mark encode as failed."""
        with self._lock:
            self.encode.state = TaskState.FAILED
            self.encode.error = error
        self.add_event(f"[red]Encode failed:[/] {error}")

    def clear_encode(self) -> None:
        """Clear encode status."""
        with self._lock:
            self.encode = EncodeStatus()
        self._notify()

    # Queue updates
    def update_queue(self, items: list[QueuedItem]) -> None:
        """Update the queue list."""
        with self._lock:
            self.queue = items
        self._notify()


# Global status tracker instance
_tracker: StatusTracker | None = None


def get_tracker() -> StatusTracker:
    """Get the global status tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = StatusTracker()
    return _tracker
