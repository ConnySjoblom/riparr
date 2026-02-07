"""Job queue orchestration.

Manages the rip and encode pipeline with support for concurrent operations
and automatic recovery of interrupted jobs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
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
        self.makemkv = MakeMKV(settings.makemkv_path, license_key=settings.makemkv_key)
        self.handbrake = HandBrake(settings.handbrake_path)
        self.selector = TitleSelector(settings)
        self.namer = OutputNamer(settings)

        self._rip_semaphore = asyncio.Semaphore(settings.max_concurrent_rips)
        self._encode_semaphore = asyncio.Semaphore(settings.max_concurrent_encodes)
        self._running = False

    async def process_disc(self, device: str, encode: bool = False) -> Job:
        """Process a disc from start to finish.

        Args:
            device: Device path
            encode: Whether to encode inline (default False - let queue processor handle it)

        Returns:
            Completed job
        """
        log.info("Processing disc", device=device)

        # Start with a temporary device-based directory (will be renamed after metadata lookup)
        temp_disc_dir = self.settings.raw_dir / f"disc_{device.replace('/', '_')}"
        temp_disc_dir.mkdir(parents=True, exist_ok=True)

        job = Job(
            disc=Disc(device=device),
            output_dir=temp_disc_dir,
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

            # Update output directory to use metadata-based naming
            disc_dir = self._get_disc_output_dir(disc, device)
            if disc_dir != temp_disc_dir:
                disc_dir.mkdir(parents=True, exist_ok=True)
                # Remove the empty temp directory if it was just created
                if temp_disc_dir.exists() and not any(temp_disc_dir.iterdir()):
                    temp_disc_dir.rmdir()
                job.output_dir = disc_dir
            else:
                disc_dir = temp_disc_dir

            # Check if already processed
            if self._is_already_processed(disc):
                disc_name = disc.metadata.title if disc.metadata else disc.name or "Unknown"
                log.info("Disc already processed, skipping", disc=disc_name)
                if self.tracker:
                    self.tracker.add_event(f"[yellow]Already processed:[/] {disc_name}")
                job.complete()
                return job

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
                    "imdb_id": disc.metadata.imdb_id if disc.metadata else None,
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

            # Encode (if enabled and not handled by queue processor)
            if encode and self.settings.encode_enabled:
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
        """Look up disc metadata using multiple methods.

        Tries in order:
        1. DVD ID → ARM database lookup
        2. TMDB search by disc name

        Args:
            disc: Disc object to update
            device: Device path
        """
        from riparr.metadata.tmdb import search as tmdb_search

        # Method 1: Try DVD ID → ARM database
        try:
            dvd_id = compute_dvd_id(device)
            disc.dvd_id = dvd_id
            log.info("Computed DVD ID", dvd_id=dvd_id)

            metadata = await lookup_disc(dvd_id)
            if metadata:
                disc.metadata = metadata
                log.info(
                    "Found metadata via ARM",
                    title=metadata.title,
                    year=metadata.year,
                )
                if self.tracker:
                    self.tracker.add_event(
                        f"Found: [cyan]{metadata.title}[/] ({metadata.year})"
                    )
                return  # Success, no need to try other methods
        except Exception as e:
            log.debug("DVD ID lookup failed", error=str(e))

        # Method 2: Try TMDB search by disc name
        if disc.name:
            try:
                # Clean up disc name for search (replace underscores, etc.)
                search_title = disc.name.replace("_", " ").strip()
                log.info("Searching TMDB by disc name", title=search_title)

                metadata = await tmdb_search(search_title)
                if metadata:
                    disc.metadata = metadata
                    log.info(
                        "Found metadata via TMDB",
                        title=metadata.title,
                        year=metadata.year,
                    )
                    if self.tracker:
                        self.tracker.add_event(
                            f"Found: [cyan]{metadata.title}[/] ({metadata.year})"
                        )
                    return  # Success
            except Exception as e:
                log.debug("TMDB search failed", error=str(e))

        # All methods failed
        log.warning("All metadata lookup methods failed", disc_name=disc.name)

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
                def make_progress_callback(title_idx: int) -> Callable[[float], None]:
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

                # Clean up raw file if enabled
                if self.settings.delete_raw_after_encode:
                    self._cleanup_raw_file(mkv_file)

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

    def _get_disc_output_dir(self, disc: Disc, device: str) -> Path:
        """Generate the output directory for a disc based on metadata.

        Uses the format: <title> (<year>) {imdb=<id>}

        Args:
            disc: Disc object with optional metadata
            device: Device path (fallback)

        Returns:
            Output directory path
        """
        import re

        if disc.metadata:
            # Use metadata to generate proper folder name
            title = disc.metadata.title
            year = disc.metadata.year
            imdb_id = disc.metadata.imdb_id

            # Sanitize title for filesystem
            title = re.sub(r'[<>:"/\\|?*]', "", title)
            title = re.sub(r"\s+", " ", title).strip()

            if year and imdb_id:
                folder_name = f"{title} ({year}) {{imdb-{imdb_id}}}"
            elif year:
                folder_name = f"{title} ({year})"
            elif imdb_id:
                folder_name = f"{title} {{imdb-{imdb_id}}}"
            else:
                folder_name = title

            return self.settings.raw_dir / folder_name

        # Fallback to disc name if available
        if disc.name:
            # Clean up disc name
            name = re.sub(r'[<>:"/\\|?*]', "", disc.name)
            name = re.sub(r"[\._]", " ", name)
            name = re.sub(r"\s+", " ", name).strip()
            return self.settings.raw_dir / name

        # Last resort: device-based name
        return self.settings.raw_dir / f"disc_{device.replace('/', '_')}"

    def _is_already_processed(self, disc: Disc) -> bool:
        """Check if a disc has already been processed.

        Checks both raw_dir and output_dir for existing folders matching the disc.

        Args:
            disc: Disc object with metadata

        Returns:
            True if disc appears to be already processed
        """
        import re

        if not disc.metadata:
            # Can't check without metadata
            return False

        title = disc.metadata.title
        year = disc.metadata.year
        imdb_id = disc.metadata.imdb_id

        # Sanitize title for matching
        title_clean = re.sub(r'[<>:"/\\|?*]', "", title).strip()

        # Build possible folder name patterns
        patterns = []
        if year and imdb_id:
            patterns.append(f"{title_clean} ({year}) {{imdb-{imdb_id}}}")
        if year:
            patterns.append(f"{title_clean} ({year})")
        patterns.append(title_clean)

        # Check raw_dir
        for pattern in patterns:
            raw_path = self.settings.raw_dir / pattern
            if raw_path.exists() and any(raw_path.glob("*.mkv")):
                log.debug("Found in raw_dir", path=str(raw_path))
                return True

        # Check output_dir (Movies subfolder)
        for pattern in patterns:
            output_path = self.settings.output_dir / "Movies" / pattern
            if output_path.exists() and any(output_path.glob("*.mkv")):
                log.debug("Found in output_dir", path=str(output_path))
                return True

        return False

    def _cleanup_raw_file(self, mkv_file: Path) -> None:
        """Clean up raw file and marker after successful encoding.

        Also removes the parent directory if empty.

        Args:
            mkv_file: Path to the raw MKV file
        """
        try:
            # Remove the raw MKV file
            if mkv_file.exists():
                mkv_file.unlink()
                log.info("Deleted raw file", file=mkv_file.name)

            # Remove marker files
            self.markers.remove_markers(mkv_file)

            # Remove parent directory if empty
            parent = mkv_file.parent
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
                log.info("Removed empty directory", dir=str(parent))

        except Exception as e:
            log.warning("Failed to clean up raw file", file=str(mkv_file), error=str(e))

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
                    dvd_id = job_info.metadata.get("dvd_id")
                    if isinstance(dvd_id, str):
                        disc.dvd_id = dvd_id

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

                # Clean up raw file if enabled
                if self.settings.delete_raw_after_encode:
                    self._cleanup_raw_file(job_info.path)

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
