"""MakeMKV async wrapper for disc ripping."""

import asyncio
from collections.abc import Callable
from pathlib import Path

import anyio
import structlog

from riparr.core.disc import Disc
from riparr.ripper.parser import ParseState, finalize_state, parse_line

log = structlog.get_logger()


class MakeMKVError(Exception):
    """MakeMKV operation error."""

    pass


class MakeMKV:
    """Async wrapper for MakeMKV command-line tool."""

    def __init__(self, executable: str = "makemkvcon", license_key: str | None = None) -> None:
        """Initialize MakeMKV wrapper.

        Args:
            executable: Path to makemkvcon binary
            license_key: Optional MakeMKV license key to configure
        """
        self.executable = executable
        if license_key:
            self._configure_license(license_key)

    def _configure_license(self, key: str) -> None:
        """Configure MakeMKV license key.

        Uses makemkvcon reg command to register the key.
        """
        import subprocess

        try:
            result = subprocess.run(
                [self.executable, "reg", key],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                log.debug("Configured MakeMKV license key")
            else:
                log.warning(
                    "Failed to register MakeMKV key",
                    returncode=result.returncode,
                    stderr=result.stderr.strip() if result.stderr else None,
                )
        except FileNotFoundError:
            log.warning("MakeMKV not found, cannot register key")
        except Exception as e:
            log.warning("Failed to configure MakeMKV license", error=str(e))

    async def scan_disc(self, device: str) -> Disc:
        """Scan a disc and return its structure.

        Args:
            device: Device path (e.g., /dev/sr0) or disc index (e.g., disc:0)

        Returns:
            Disc object with all titles and tracks

        Raises:
            MakeMKVError: If scanning fails
        """
        # Convert device path to MakeMKV format
        if device.startswith("/dev/"):
            source = f"dev:{device}"
        elif device.startswith("disc:"):
            source = device
        else:
            source = f"dev:{device}"

        cmd = [self.executable, "-r", "info", source]
        log.info("Scanning disc", device=device, command=" ".join(cmd))

        state = ParseState()
        state.disc.device = device

        try:
            process = await anyio.run_process(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                check=False,
            )

            # Parse stdout line by line
            for line in process.stdout.decode("utf-8", errors="replace").splitlines():
                parse_line(line, state)

            if process.returncode != 0:
                stderr = process.stderr.decode("utf-8", errors="replace")
                # Log errors from MSG lines (more useful than stderr)
                error_msgs = state.errors if state.errors else [stderr] if stderr else ["Unknown error"]

                # Add helpful context for common return codes
                hint = ""
                if process.returncode == 253:
                    hint = " (likely: beta key expired or license issue - set RIPARR_MAKEMKV_KEY)"
                elif process.returncode == 2:
                    hint = " (no disc in drive)"

                log.error(
                    "MakeMKV scan failed",
                    returncode=process.returncode,
                    hint=hint.strip() if hint else None,
                    errors=error_msgs,
                )
                # Don't raise on non-zero return code, as MakeMKV may still have output
                # useful information before failing

            disc = finalize_state(state)
            log.info(
                "Disc scan complete",
                name=disc.name,
                titles=len(disc.titles),
                disc_type=disc.disc_type.value,
            )
            return disc

        except FileNotFoundError as e:
            raise MakeMKVError(
                f"MakeMKV not found at '{self.executable}'. "
                "Please install MakeMKV and ensure makemkvcon is in PATH."
            ) from e
        except Exception as e:
            raise MakeMKVError(f"Failed to scan disc: {e}") from e

    async def rip_title(
        self,
        device: str,
        title_index: int,
        output_dir: Path,
        progress_callback: Callable[[float], None] | None = None,
    ) -> Path:
        """Rip a single title from disc.

        Args:
            device: Device path (e.g., /dev/sr0)
            title_index: Title index to rip
            output_dir: Directory to save MKV file
            progress_callback: Optional callback for progress updates (0-100)

        Returns:
            Path to the ripped MKV file

        Raises:
            MakeMKVError: If ripping fails
        """
        # Ensure output directory exists
        output_dir.mkdir(parents=True, exist_ok=True)

        # Convert device path to MakeMKV format
        if device.startswith("/dev/"):
            source = f"dev:{device}"
        else:
            source = device

        cmd = [
            self.executable,
            "-r",
            "mkv",
            source,
            str(title_index),
            str(output_dir),
        ]

        log.info(
            "Ripping title",
            device=device,
            title=title_index,
            output_dir=str(output_dir),
        )

        state = ParseState()
        output_file: Path | None = None

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            assert process.stdout is not None

            async for line_bytes in process.stdout:
                line = line_bytes.decode("utf-8", errors="replace").strip()
                parse_line(line, state)

                # Report progress
                if progress_callback and state.progress_max > 0:
                    progress = (state.progress_current / state.progress_max) * 100
                    progress_callback(progress)

                # Capture output filename
                if "TINFO" in line and f",{title_index}," in line:
                    # Parse for output filename
                    for title in state.titles.values():
                        if title.output_file:
                            output_file = output_dir / title.output_file

            returncode = await process.wait()

            if returncode != 0:
                stderr = (
                    await process.stderr.read() if process.stderr else b""
                ).decode("utf-8", errors="replace")
                raise MakeMKVError(f"MakeMKV rip failed (code {returncode}): {stderr}")

            # Find output file if not captured
            if output_file is None:
                mkv_files = list(output_dir.glob(f"*_t{title_index:02d}.mkv"))
                if mkv_files:
                    output_file = mkv_files[0]
                else:
                    # Fallback: find most recent MKV
                    mkv_files = sorted(output_dir.glob("*.mkv"), key=lambda p: p.stat().st_mtime)
                    if mkv_files:
                        output_file = mkv_files[-1]

            if output_file is None or not output_file.exists():
                raise MakeMKVError(f"No output file found after ripping title {title_index}")

            log.info("Title ripped successfully", title=title_index, output=str(output_file))
            return output_file

        except FileNotFoundError as e:
            raise MakeMKVError(
                f"MakeMKV not found at '{self.executable}'. "
                "Please install MakeMKV and ensure makemkvcon is in PATH."
            ) from e
        except MakeMKVError:
            raise
        except Exception as e:
            raise MakeMKVError(f"Failed to rip title {title_index}: {e}") from e

    async def rip_all(
        self,
        device: str,
        output_dir: Path,
        progress_callback: Callable[[float], None] | None = None,
    ) -> list[Path]:
        """Rip all titles from disc.

        Args:
            device: Device path (e.g., /dev/sr0)
            output_dir: Directory to save MKV files
            progress_callback: Optional callback for progress updates (0-100)

        Returns:
            List of paths to ripped MKV files
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Convert device path to MakeMKV format
        if device.startswith("/dev/"):
            source = f"dev:{device}"
        else:
            source = device

        cmd = [
            self.executable,
            "-r",
            "mkv",
            source,
            "all",
            str(output_dir),
        ]

        log.info("Ripping all titles", device=device, output_dir=str(output_dir))

        state = ParseState()

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            assert process.stdout is not None

            async for line_bytes in process.stdout:
                line = line_bytes.decode("utf-8", errors="replace").strip()
                parse_line(line, state)

                if progress_callback and state.progress_max > 0:
                    progress = (state.progress_current / state.progress_max) * 100
                    progress_callback(progress)

            returncode = await process.wait()

            if returncode != 0:
                stderr = (
                    await process.stderr.read() if process.stderr else b""
                ).decode("utf-8", errors="replace")
                raise MakeMKVError(f"MakeMKV rip failed (code {returncode}): {stderr}")

            output_files = list(output_dir.glob("*.mkv"))
            log.info("All titles ripped", count=len(output_files))
            return output_files

        except MakeMKVError:
            raise
        except Exception as e:
            raise MakeMKVError(f"Failed to rip all titles: {e}") from e

    async def get_drives(self) -> list[dict[str, str]]:
        """List available optical drives.

        Returns:
            List of drive info dicts with 'device' and 'name' keys
        """
        cmd = [self.executable, "-r", "info", "disc:9999"]  # Invalid disc triggers drive list

        try:
            process = await anyio.run_process(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                check=False,
            )

            drives = []
            for line in process.stdout.decode("utf-8", errors="replace").splitlines():
                if line.startswith("DRV:"):
                    parts = line[4:].split(",")
                    if len(parts) >= 6:
                        drive_idx = parts[0]
                        visible = parts[1]
                        enabled = parts[2]
                        drive_name = parts[4].strip('"')
                        disc_name = parts[5].strip('"')

                        if enabled == "1":
                            drives.append({
                                "index": drive_idx,
                                "device": f"/dev/sr{drive_idx}",
                                "drive_name": drive_name,
                                "disc_name": disc_name,
                                "has_disc": visible == "1",
                            })

            return drives

        except FileNotFoundError:
            log.warning("MakeMKV not found, cannot list drives")
            return []
