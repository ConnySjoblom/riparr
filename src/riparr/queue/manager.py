"""Job queue orchestration.

Manages the rip and encode pipeline with support for concurrent operations
and automatic recovery of interrupted jobs.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import structlog

from riparr.config.settings import Settings
from riparr.core.disc import Disc
from riparr.core.job import Job, JobStatus
from riparr.encoder.handbrake import HandBrake
from riparr.metadata.arm_api import lookup_disc
from riparr.metadata.dvdid import compute_dvd_id
from riparr.output.naming import OutputNamer
from riparr.queue.markers import MarkerManager
from riparr.ripper.makemkv import MakeMKV
from riparr.ripper.selector import TitleSelector

if TYPE_CHECKING:
    from riparr.tui.status import StatusTracker

log = structlog.get_logger()


class QueueManager:
    """Orchestrate rip and encode operations."""

    def __init__(
        self,
        settings: Settings,
        tracker: StatusTracker | None = None,
    ) -> None:
        """Initialize queue manager.

        Args:
            settings: Application settings
            tracker: Optional status tracker for TUI updates
        """
        self.settings = settings
        self.tracker = tracker
        self.markers = MarkerManager(settings.raw_dir)
        self.makemkv = MakeMKV(settings.makemkv_path)
        self.handbrake = HandBrake(settings.handbrake_path)
        self.selector = TitleSelector(settings)
        self.namer = OutputNamer(settings)

        self._rip_semaphore = asyncio.Semaphore(settings.max_concurrent_rips)
        self._encode_semaphore = asyncio.Semaphore(settings.max_concurrent_encodes)
        self._running = False

    async def process_disc(self, device: str) -> Job:
        """Process a disc from start to finish.

        Args:
            device: Device path

        Returns:
            Completed job
        """
        log.info("Processing disc", device=device)

        # Create output directory for this disc
        disc_dir = self.settings.raw_dir / f"disc_{device.replace('/', '_')}"
        disc_dir.mkdir(parents=True, exist_ok=True)

        job = Job(
            disc=Disc(device=device),
            output_dir=disc_dir,
        )

        try:
            # Scan disc
            job.start()
            if self.tracker:
                self.tracker.add_event("Scanning disc...")

            disc = await self.makemkv.scan_disc(device)
            job.disc = disc

            # Lookup metadata
            await self._lookup_metadata(disc, device)

            # Select titles
            job.selected_titles = self.selector.select_titles(disc.titles)

            if not job.selected_titles:
                log.warning("No titles selected for ripping", device=device)
                job.fail("No titles selected", "selection")
                if self.tracker:
                    self.tracker.fail_rip("No titles selected")
                return job

            # Start rip tracking
            if self.tracker:
                disc_name = disc.metadata.title if disc.metadata else disc.name or "Unknown"
                self.tracker.start_rip(device, disc_name, len(job.selected_titles))

            # Rip titles
            job.status = JobStatus.RIPPING
            ripped_files = await self._rip_titles(job)

            if not ripped_files:
                job.fail("No files ripped", "ripping")
                if self.tracker:
                    self.tracker.fail_rip("No files ripped")
                return job

            job.status = JobStatus.RIPPED
            job.output_files = ripped_files

            if self.tracker:
                self.tracker.complete_rip()

            # Mark files ready for encoding
            for mkv_file in ripped_files:
                metadata = {
                    "disc_name": disc.name,
                    "dvd_id": disc.dvd_id,
                    "title": disc.metadata.title if disc.metadata else None,
                    "year": disc.metadata.year if disc.metadata else None,
                }
                self.markers.create_marker(mkv_file, "ready", metadata=metadata)

            # Eject disc
            if self.settings.eject_after_rip:
                await self._eject_disc(device)
                if self.tracker:
                    self.tracker.add_event("Disc ejected")

            # Clear rip status after eject
            if self.tracker:
                self.tracker.clear_rip()

            # Encode (if enabled)
            if self.settings.encode_enabled:
                job.status = JobStatus.ENCODING
                await self._encode_files(job)

            job.complete()
            log.info(
                "Disc processing complete",
                device=device,
                files=len(job.output_files),
            )

        except Exception as e:
            log.error("Disc processing failed", device=device, error=str(e))
            job.fail(str(e), "processing")
            if self.tracker:
                self.tracker.fail_rip(str(e))

        return job

    async def _lookup_metadata(self, disc: Disc, device: str) -> None:
        """Look up disc metadata.

        Args:
            disc: Disc object to update
            device: Device path
        """
        try:
            dvd_id = compute_dvd_id(device)
            disc.dvd_id = dvd_id
            log.info("Computed DVD ID", dvd_id=dvd_id)

            metadata = await lookup_disc(dvd_id)
            if metadata:
                disc.metadata = metadata
                log.info(
                    "Found metadata",
                    title=metadata.title,
                    year=metadata.year,
                )
                if self.tracker:
                    self.tracker.add_event(
                        f"Found: [cyan]{metadata.title}[/] ({metadata.year})"
                    )
        except Exception as e:
            log.warning("Metadata lookup failed", error=str(e))

    async def _rip_titles(self, job: Job) -> list[Path]:
        """Rip selected titles from disc.

        Args:
            job: Job containing disc and selected titles

        Returns:
            List of ripped file paths
        """
        ripped_files = []

        async with self._rip_semaphore:
            for idx, title in enumerate(job.selected_titles, 1):
                job.current_title = title.index
                job.progress = ((idx - 1) / len(job.selected_titles)) * 100

                log.info(
                    "Ripping title",
                    index=title.index,
                    progress=f"{idx}/{len(job.selected_titles)}",
                )

                # Progress callback for tracker
                def make_progress_callback(title_idx: int) -> callable:
                    def callback(progress: float) -> None:
                        if self.tracker:
                            # Calculate overall progress
                            base = ((title_idx - 1) / len(job.selected_titles)) * 100
                            title_contrib = (progress / len(job.selected_titles))
                            overall = base + title_contrib
                            self.tracker.update_rip(title_idx, overall)
                    return callback

                try:
                    output_file = await self.makemkv.rip_title(
                        job.disc.device,
                        title.index,
                        job.output_dir,
                        progress_callback=make_progress_callback(idx),
                    )
                    ripped_files.append(output_file)
                except Exception as e:
                    log.error(
                        "Failed to rip title",
                        index=title.index,
                        error=str(e),
                    )

        return ripped_files

    async def _encode_files(self, job: Job) -> None:
        """Encode ripped files.

        Args:
            job: Job containing files to encode
        """
        for mkv_file in job.output_files:
            if not mkv_file.exists():
                continue

            self.markers.update_status(mkv_file, "transcoding")

            if self.tracker:
                self.tracker.start_encode(mkv_file.name)

            try:
                output_path = self.namer.get_output_path(job.disc, mkv_file)
                output_path.parent.mkdir(parents=True, exist_ok=True)

                # Progress callback for tracker
                def encode_progress_callback(info) -> None:
                    if self.tracker:
                        self.tracker.update_encode(
                            info.percent,
                            fps=info.fps,
                            eta=info.eta,
                        )

                async with self._encode_semaphore:
                    await self.handbrake.encode(
                        mkv_file,
                        output_path,
                        preset=self.settings.handbrake_preset,
                        video_codec=self.settings.video_codec,
                        quality=self.settings.video_quality,
                        encoder_preset=self.settings.encoder_preset,
                        deinterlace=self.settings.deinterlace,
                        subtitle_scan=self.settings.subtitle_scan,
                        progress_callback=encode_progress_callback,
                    )

                self.markers.update_status(mkv_file, "complete")
                log.info("Encoded file", input=mkv_file.name, output=str(output_path))

                if self.tracker:
                    self.tracker.complete_encode()

            except Exception as e:
                log.error("Encoding failed", file=mkv_file.name, error=str(e))
                self.markers.update_status(mkv_file, "failed", error=str(e))

                if self.tracker:
                    self.tracker.fail_encode(str(e))

        # Clear encode status when done with all files
        if self.tracker:
            self.tracker.clear_encode()

    async def _eject_disc(self, device: str) -> None:
        """Eject disc from drive.

        Args:
            device: Device path
        """
        import subprocess

        try:
            await anyio.to_thread.run_sync(
                lambda: subprocess.run(["eject", device], capture_output=True)
            )
            log.info("Disc ejected", device=device)
        except Exception as e:
            log.warning("Failed to eject disc", device=device, error=str(e))

    async def process_queue(self) -> None:
        """Process pending items in the encoding queue."""
        self._running = True
        log.info("Starting queue processor")

        while self._running:
            job_info = self.markers.get_next_ready()

            if job_info is None:
                # No pending jobs, wait and check again
                await anyio.sleep(5)
                continue

            log.info("Processing queued file", name=job_info.name)

            self.markers.update_status(job_info.path, "transcoding")

            if self.tracker:
                self.tracker.start_encode(job_info.name)

            try:
                # Reconstruct disc info from metadata
                disc = Disc(name=job_info.name)
                if job_info.metadata:
                    disc.dvd_id = job_info.metadata.get("dvd_id")

                output_path = self.namer.get_output_path(disc, job_info.path)
                output_path.parent.mkdir(parents=True, exist_ok=True)

                def encode_progress_callback(info) -> None:
                    if self.tracker:
                        self.tracker.update_encode(
                            info.percent,
                            fps=info.fps,
                            eta=info.eta,
                        )

                async with self._encode_semaphore:
                    await self.handbrake.encode(
                        job_info.path,
                        output_path,
                        preset=self.settings.handbrake_preset,
                        video_codec=self.settings.video_codec,
                        quality=self.settings.video_quality,
                        encoder_preset=self.settings.encoder_preset,
                        deinterlace=self.settings.deinterlace,
                        subtitle_scan=self.settings.subtitle_scan,
                        progress_callback=encode_progress_callback,
                    )

                self.markers.update_status(job_info.path, "complete")
                log.info("Encoding complete", name=job_info.name, output=str(output_path))

                if self.tracker:
                    self.tracker.complete_encode()

            except Exception as e:
                log.error("Encoding failed", name=job_info.name, error=str(e))
                self.markers.update_status(job_info.path, "failed", error=str(e))

                if self.tracker:
                    self.tracker.fail_encode(str(e))

    def stop(self) -> None:
        """Stop the queue processor."""
        self._running = False

    async def recover_interrupted(self) -> int:
        """Recover any interrupted encoding jobs.

        Resets jobs that were in "transcoding" state back to "ready".

        Returns:
            Number of jobs recovered
        """
        interrupted = self.markers.list_jobs(status_filter="transcoding")
        count = 0

        for job in interrupted:
            log.info("Recovering interrupted job", name=job.name)
            self.markers.update_status(job.path, "ready")
            count += 1

        if count > 0:
            log.info("Recovered interrupted jobs", count=count)
            if self.tracker:
                self.tracker.add_event(f"Recovered {count} interrupted job(s)")

        return count
