"""Marker file operations for job state tracking.

Uses a file-based marker system for tracking job states:
- .ready - Ready for encoding
- .transcoding - Currently being encoded
- .failed - Encoding failed
- .complete - Successfully encoded

This allows recovery of interrupted jobs on restart.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import structlog

log = structlog.get_logger()


JobStatus = Literal["ready", "transcoding", "failed", "complete"]


@dataclass
class JobInfo:
    """Information about a queued job."""

    name: str
    path: Path
    status: JobStatus
    size_bytes: int
    created_at: datetime
    error: str | None = None
    metadata: dict[str, str | int | None] | None = None

    @property
    def size_str(self) -> str:
        """Human-readable size."""
        size = float(self.size_bytes)
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


class MarkerManager:
    """Manage marker files for job tracking."""

    MARKER_SUFFIXES: dict[JobStatus, str] = {
        "ready": ".ready",
        "transcoding": ".transcoding",
        "failed": ".failed",
        "complete": ".complete",
    }

    def __init__(self, base_dir: Path) -> None:
        """Initialize marker manager.

        Args:
            base_dir: Base directory for raw MKV files and markers
        """
        self.base_dir = Path(base_dir)

    def create_marker(
        self,
        mkv_path: Path,
        status: JobStatus,
        metadata: dict[str, str | int | None] | None = None,
        error: str | None = None,
    ) -> Path:
        """Create a marker file for an MKV.

        Args:
            mkv_path: Path to the MKV file
            status: Job status
            metadata: Optional metadata to store in marker
            error: Optional error message (for failed status)

        Returns:
            Path to the created marker file
        """
        # Remove any existing markers
        self.remove_markers(mkv_path)

        marker_path = mkv_path.with_suffix(
            mkv_path.suffix + self.MARKER_SUFFIXES[status]
        )

        marker_data: dict[str, str | dict[str, str | int | None]] = {
            "status": status,
            "created_at": datetime.now().isoformat(),
            "mkv_path": str(mkv_path),
        }

        if metadata:
            marker_data["metadata"] = metadata
        if error:
            marker_data["error"] = error

        marker_path.write_text(json.dumps(marker_data, indent=2))
        log.debug("Created marker", path=str(marker_path), status=status)

        return marker_path

    def get_status(self, mkv_path: Path) -> JobStatus | None:
        """Get the status of an MKV file.

        Args:
            mkv_path: Path to the MKV file

        Returns:
            Job status or None if no marker exists
        """
        for status, suffix in self.MARKER_SUFFIXES.items():
            marker_path = mkv_path.with_suffix(mkv_path.suffix + suffix)
            if marker_path.exists():
                return status
        return None

    def update_status(
        self,
        mkv_path: Path,
        new_status: JobStatus,
        error: str | None = None,
    ) -> Path | None:
        """Update the status of an MKV file.

        Args:
            mkv_path: Path to the MKV file
            new_status: New job status
            error: Optional error message

        Returns:
            Path to the new marker file, or None if no existing marker
        """
        current_status = self.get_status(mkv_path)

        if current_status is None:
            log.warning("No existing marker for file", path=str(mkv_path))
            return None

        # Read existing metadata
        current_marker = mkv_path.with_suffix(
            mkv_path.suffix + self.MARKER_SUFFIXES[current_status]
        )
        metadata = None
        try:
            data = json.loads(current_marker.read_text())
            metadata = data.get("metadata")
        except (json.JSONDecodeError, FileNotFoundError):
            pass

        return self.create_marker(mkv_path, new_status, metadata=metadata, error=error)

    def list_jobs(self, status_filter: str | None = None) -> list[JobInfo]:
        """List all jobs in the queue.

        Args:
            status_filter: Filter by status (ready, transcoding, failed, complete)

        Returns:
            List of JobInfo objects
        """
        jobs = []

        # Find all MKV files with markers
        for mkv_path in self.base_dir.rglob("*.mkv"):
            status = self.get_status(mkv_path)
            if status is None:
                continue

            if status_filter and status != status_filter:
                continue

            # Read marker data
            marker_path = mkv_path.with_suffix(
                mkv_path.suffix + self.MARKER_SUFFIXES[status]
            )

            created_at = datetime.now()
            error = None
            metadata = None

            try:
                data = json.loads(marker_path.read_text())
                created_at = datetime.fromisoformat(data.get("created_at", ""))
                error = data.get("error")
                metadata = data.get("metadata")
            except (json.JSONDecodeError, FileNotFoundError, ValueError):
                # Use file modification time as fallback
                created_at = datetime.fromtimestamp(marker_path.stat().st_mtime)

            try:
                size_bytes = mkv_path.stat().st_size
            except FileNotFoundError:
                size_bytes = 0

            jobs.append(
                JobInfo(
                    name=mkv_path.stem,
                    path=mkv_path,
                    status=status,
                    size_bytes=size_bytes,
                    created_at=created_at,
                    error=error,
                    metadata=metadata,
                )
            )

        # Sort by creation time
        jobs.sort(key=lambda j: j.created_at)

        return jobs

    def get_next_ready(self) -> JobInfo | None:
        """Get the next job ready for encoding.

        Returns:
            JobInfo for the next ready job, or None
        """
        ready_jobs = self.list_jobs(status_filter="ready")
        return ready_jobs[0] if ready_jobs else None

    def retry_job(self, name: str) -> bool:
        """Mark a failed job for retry.

        Args:
            name: Job name (MKV filename without extension)

        Returns:
            True if job was marked for retry
        """
        for job in self.list_jobs(status_filter="failed"):
            if job.name == name:
                self.update_status(job.path, "ready")
                log.info("Job marked for retry", name=name)
                return True
        return False

    def retry_all_failed(self) -> int:
        """Mark all failed jobs for retry.

        Returns:
            Number of jobs marked for retry
        """
        count = 0
        for job in self.list_jobs(status_filter="failed"):
            self.update_status(job.path, "ready")
            count += 1

        if count > 0:
            log.info("Jobs marked for retry", count=count)

        return count

    def clear_jobs(self, status_filter: str | None = None) -> int:
        """Clear markers from jobs.

        Args:
            status_filter: Only clear jobs with this status

        Returns:
            Number of jobs cleared
        """
        count = 0

        for job in self.list_jobs(status_filter=status_filter):
            self.remove_markers(job.path)
            count += 1

        log.info("Jobs cleared", count=count, status_filter=status_filter)
        return count

    def remove_markers(self, mkv_path: Path) -> None:
        """Remove all markers for an MKV file.

        Args:
            mkv_path: Path to the MKV file
        """
        for suffix in self.MARKER_SUFFIXES.values():
            marker_path = mkv_path.with_suffix(mkv_path.suffix + suffix)
            if marker_path.exists():
                marker_path.unlink()
                log.debug("Removed marker", path=str(marker_path))
