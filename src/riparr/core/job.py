"""Job state machine for rip/encode operations."""

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from riparr.core.disc import Disc, Title


class JobStatus(str, Enum):
    """Job status states."""

    PENDING = "pending"
    SCANNING = "scanning"
    RIPPING = "ripping"
    RIPPED = "ripped"
    ENCODING = "encoding"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobError(BaseModel):
    """Error information for failed jobs."""

    message: str
    stage: str
    timestamp: datetime = Field(default_factory=datetime.now)
    details: str | None = None


class Job(BaseModel):
    """Represents a rip/encode job."""

    id: str = Field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S"))
    disc: Disc
    selected_titles: list[Title] = Field(default_factory=list)
    output_dir: Path
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    current_title: int | None = None
    progress: float = 0.0
    errors: list[JobError] = Field(default_factory=list)
    output_files: list[Path] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    def start(self) -> None:
        """Mark job as started."""
        self.started_at = datetime.now()
        self.status = JobStatus.SCANNING

    def complete(self) -> None:
        """Mark job as complete."""
        self.completed_at = datetime.now()
        self.status = JobStatus.COMPLETE
        self.progress = 100.0

    def fail(self, message: str, stage: str, details: str | None = None) -> None:
        """Mark job as failed with error info."""
        self.status = JobStatus.FAILED
        self.errors.append(
            JobError(message=message, stage=stage, details=details)
        )

    def cancel(self) -> None:
        """Mark job as cancelled."""
        self.status = JobStatus.CANCELLED

    @property
    def is_terminal(self) -> bool:
        """Check if job is in a terminal state."""
        return self.status in (JobStatus.COMPLETE, JobStatus.FAILED, JobStatus.CANCELLED)

    @property
    def can_retry(self) -> bool:
        """Check if job can be retried."""
        return self.status == JobStatus.FAILED

    @property
    def duration(self) -> float | None:
        """Get job duration in seconds."""
        if self.started_at is None:
            return None
        end = self.completed_at or datetime.now()
        return (end - self.started_at).total_seconds()


class JobQueue(BaseModel):
    """Queue of pending and active jobs."""

    jobs: list[Job] = Field(default_factory=list)
    max_concurrent: int = 1

    def add(self, job: Job) -> None:
        """Add a job to the queue."""
        self.jobs.append(job)

    def get_pending(self) -> list[Job]:
        """Get all pending jobs."""
        return [j for j in self.jobs if j.status == JobStatus.PENDING]

    def get_active(self) -> list[Job]:
        """Get all active (non-terminal) jobs."""
        return [j for j in self.jobs if not j.is_terminal]

    def get_next(self) -> Job | None:
        """Get next job to process, respecting concurrency limit."""
        active_count = len([j for j in self.jobs if j.status in (
            JobStatus.SCANNING, JobStatus.RIPPING, JobStatus.ENCODING
        )])

        if active_count >= self.max_concurrent:
            return None

        pending = self.get_pending()
        return pending[0] if pending else None

    def get_by_id(self, job_id: str) -> Job | None:
        """Get job by ID."""
        for job in self.jobs:
            if job.id == job_id:
                return job
        return None

    def cleanup_completed(self, keep_count: int = 10) -> int:
        """Remove old completed jobs, keeping most recent."""
        completed = [j for j in self.jobs if j.status == JobStatus.COMPLETE]
        completed.sort(key=lambda j: j.completed_at or datetime.min, reverse=True)

        to_remove = completed[keep_count:]
        for job in to_remove:
            self.jobs.remove(job)

        return len(to_remove)
