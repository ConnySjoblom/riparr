"""HandBrake async wrapper for video encoding."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog

from riparr.encoder.parser import parse_progress_line


@dataclass
class ProgressInfo:
    """Extended progress information for callbacks."""

    percent: float
    fps: float = 0.0
    eta: str = ""
    stage: str = "encoding"

log = structlog.get_logger()


class HandBrakeError(Exception):
    """HandBrake operation error."""

    pass


VideoCodec = Literal["x264", "x265", "nvenc_h264", "nvenc_h265"]


class HandBrake:
    """Async wrapper for HandBrakeCLI."""

    def __init__(self, executable: str = "HandBrakeCLI") -> None:
        """Initialize HandBrake wrapper.

        Args:
            executable: Path to HandBrakeCLI binary
        """
        self.executable = executable

    def _get_encoder_name(self, codec: VideoCodec) -> str:
        """Map codec name to HandBrake encoder name."""
        mapping = {
            "x264": "x264",
            "x265": "x265",
            "nvenc_h264": "nvenc_h264",
            "nvenc_h265": "nvenc_h265",
        }
        return mapping.get(codec, "x265")

    async def encode(
        self,
        input_path: Path,
        output_path: Path,
        preset: str = "HQ 576p25 Surround",
        video_codec: VideoCodec = "x265",
        quality: int = 19,
        encoder_preset: str = "slow",
        audio_codec: str = "copy",
        subtitle_scan: bool = True,
        deinterlace: bool = True,
        progress_callback: Callable[[ProgressInfo], None] | None = None,
    ) -> Path:
        """Encode a video file.

        Args:
            input_path: Path to input MKV file
            output_path: Path for output file
            preset: HandBrake preset name
            video_codec: Video encoder to use
            quality: Video quality (CRF/CQ value)
            encoder_preset: Encoder speed preset (slow, medium, fast, etc.)
            audio_codec: Audio codec (copy, aac, ac3, etc.)
            subtitle_scan: Scan for forced subtitles and burn in
            deinterlace: Enable comb detection and decomb filter
            progress_callback: Optional callback for progress updates

        Returns:
            Path to encoded file

        Raises:
            HandBrakeError: If encoding fails
        """
        if not input_path.exists():
            raise HandBrakeError(f"Input file not found: {input_path}")

        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        encoder = self._get_encoder_name(video_codec)

        cmd = [
            self.executable,
            "-i", str(input_path),
            "-o", str(output_path),
            "--preset", preset,
            "--encoder", encoder,
            "--quality", str(quality),
            "--encoder-preset", encoder_preset,
            "--audio-lang-list", "eng,und",
            "--first-audio",
        ]

        # Audio handling
        if audio_codec == "copy":
            cmd.extend(["--aencoder", "copy"])
        else:
            cmd.extend(["--aencoder", audio_codec])

        # Subtitle scan for forced subs
        if subtitle_scan:
            cmd.extend(["--subtitle", "scan", "-F"])

        # Deinterlace (comb detection + decomb)
        if deinterlace:
            cmd.extend(["--comb-detect", "--decomb"])

        log.info(
            "Starting encode",
            input=input_path.name,
            output=output_path.name,
            encoder=encoder,
            quality=quality,
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            assert process.stdout is not None

            last_progress: float = 0.0

            async for line_bytes in process.stdout:
                line = line_bytes.decode("utf-8", errors="replace").strip()

                progress = parse_progress_line(line)
                if progress and progress_callback:
                    # Only report significant progress changes
                    if progress.percent - last_progress >= 0.5:
                        info = ProgressInfo(
                            percent=progress.percent,
                            fps=progress.fps,
                            eta=progress.eta,
                            stage=progress.stage,
                        )
                        progress_callback(info)
                        last_progress = progress.percent

            returncode = await process.wait()

            if returncode != 0:
                raise HandBrakeError(f"HandBrake failed with code {returncode}")

            if not output_path.exists():
                raise HandBrakeError(f"Output file not created: {output_path}")

            log.info(
                "Encode complete",
                output=output_path.name,
                size_mb=output_path.stat().st_size / (1024 * 1024),
            )

            return output_path

        except FileNotFoundError as e:
            raise HandBrakeError(
                f"HandBrakeCLI not found at '{self.executable}'. "
                "Please install HandBrake and ensure HandBrakeCLI is in PATH."
            ) from e
        except HandBrakeError:
            raise
        except Exception as e:
            raise HandBrakeError(f"Encoding failed: {e}") from e

    async def get_presets(self) -> list[str]:
        """List available HandBrake presets.

        Returns:
            List of preset names
        """
        cmd = [self.executable, "--preset-list"]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, _ = await process.communicate()
            output = stdout.decode("utf-8", errors="replace")

            presets = []
            for line in output.splitlines():
                # Preset lines start with whitespace and a name
                line = line.strip()
                if line and not line.startswith("<") and not line.endswith(">"):
                    # Filter out category headers
                    if "/" not in line and line not in ("General", "Web", "Devices", "Matroska"):
                        presets.append(line)

            return presets

        except FileNotFoundError:
            log.warning("HandBrakeCLI not found")
            return []

    async def scan(self, input_path: Path) -> dict[str, str | list[str] | None]:
        """Scan a video file for information.

        Args:
            input_path: Path to video file

        Returns:
            Dict with video information
        """
        cmd = [
            self.executable,
            "-i", str(input_path),
            "--scan",
            "-t", "0",
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            stdout, _ = await process.communicate()
            output = stdout.decode("utf-8", errors="replace")

            # Parse scan output
            info: dict[str, str | list[str] | None] = {
                "duration": None,
                "size": None,
                "video_codec": None,
                "resolution": None,
                "audio_tracks": [],
                "subtitle_tracks": [],
            }

            for line in output.splitlines():
                line = line.strip()

                # Duration
                if "duration:" in line.lower():
                    import re
                    match = re.search(r"duration: (\d+:\d+:\d+)", line, re.IGNORECASE)
                    if match:
                        info["duration"] = match.group(1)

                # Video codec
                if "video codec" in line.lower() or "stream #" in line.lower():
                    if "h.264" in line.lower() or "avc" in line.lower():
                        info["video_codec"] = "H.264"
                    elif "h.265" in line.lower() or "hevc" in line.lower():
                        info["video_codec"] = "H.265"

            return info

        except FileNotFoundError as e:
            raise HandBrakeError("HandBrakeCLI not found") from e
