"""HandBrake output parser for progress tracking."""

import re
from dataclasses import dataclass


@dataclass
class EncodeProgress:
    """Encoding progress information."""

    percent: float = 0.0
    eta: str = ""
    fps: float = 0.0
    avg_fps: float = 0.0
    pass_num: int = 1
    total_passes: int = 1
    stage: str = "encoding"


def parse_progress_line(line: str) -> EncodeProgress | None:
    """Parse a HandBrake progress line.

    HandBrake outputs progress in various formats:
    - Encoding: task 1 of 1, 45.23 % (148.34 fps, avg 152.11 fps, ETA 00h12m34s)
    - Muxing: 98.5 %
    - Scanning title 1 of 1...

    Args:
        line: Line of HandBrake output

    Returns:
        EncodeProgress if line contains progress info, None otherwise
    """
    progress = EncodeProgress()

    # Main encoding progress
    # Format: Encoding: task N of M, XX.XX % (XX.XX fps, avg XX.XX fps, ETA XXhXXmXXs)
    encoding_match = re.search(
        r"Encoding: task (\d+) of (\d+), ([\d.]+) %"
        r"(?: \(([\d.]+) fps, avg ([\d.]+) fps, ETA (\S+)\))?",
        line,
    )
    if encoding_match:
        progress.pass_num = int(encoding_match.group(1))
        progress.total_passes = int(encoding_match.group(2))
        progress.percent = float(encoding_match.group(3))
        progress.stage = "encoding"

        if encoding_match.group(4):
            progress.fps = float(encoding_match.group(4))
        if encoding_match.group(5):
            progress.avg_fps = float(encoding_match.group(5))
        if encoding_match.group(6):
            progress.eta = encoding_match.group(6)

        return progress

    # Muxing progress
    mux_match = re.search(r"Muxing: ([\d.]+) %", line)
    if mux_match:
        progress.percent = float(mux_match.group(1))
        progress.stage = "muxing"
        return progress

    # Scanning progress
    scan_match = re.search(r"Scanning title (\d+) of (\d+)", line)
    if scan_match:
        current = int(scan_match.group(1))
        total = int(scan_match.group(2))
        progress.percent = (current / total) * 100
        progress.stage = "scanning"
        return progress

    return None


def parse_encode_output(output: str) -> dict[str, bool | float | int | str | None]:
    """Parse complete HandBrake output for results.

    Args:
        output: Full HandBrake stdout/stderr output

    Returns:
        Dict with encoding results
    """
    result: dict[str, bool | float | int | str | None] = {
        "success": False,
        "duration": None,
        "size_bytes": None,
        "error": None,
    }

    # Check for success
    if "Encode done!" in output:
        result["success"] = True

    # Parse final encoding time
    time_match = re.search(r"Encode done!.*?([\d.]+) seconds", output)
    if time_match:
        result["duration"] = float(time_match.group(1))

    # Parse output file size
    size_match = re.search(r"(\d+) bytes", output)
    if size_match:
        result["size_bytes"] = int(size_match.group(1))

    # Check for errors
    if "ERROR" in output or "error" in output.lower():
        error_lines = [
            line for line in output.splitlines()
            if "error" in line.lower() or "ERROR" in line
        ]
        if error_lines:
            result["error"] = error_lines[-1]

    return result
