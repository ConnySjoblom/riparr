"""DVD ID computation for disc identification.

Uses the pydvdid-m library to compute a CRC64 hash of the disc structure,
which can then be used to look up metadata from the ARM database.
"""

import structlog

log = structlog.get_logger()


def compute_dvd_id(device_or_path: str) -> str:
    """Compute the DVD ID (CRC64 hash) for a disc.

    The DVD ID is computed from the disc's IFO files structure,
    making it unique to each DVD release.

    Args:
        device_or_path: Device path (e.g., /dev/sr0) or mount point

    Returns:
        64-character hexadecimal DVD ID string

    Raises:
        RuntimeError: If DVD ID computation fails
    """
    try:
        from pydvdid_m import DvdId

        # pydvdid-m expects a mount point, not a device
        # If given a device, try to find the mount point
        mount_point = _get_mount_point(device_or_path)

        if mount_point is None:
            raise RuntimeError(
                f"Device {device_or_path} is not mounted. "
                "Mount the disc and try again, or pass the mount point directly."
            )

        dvd_id = DvdId(mount_point)
        crc = dvd_id.compute_crc64()

        log.info("Computed DVD ID", device=device_or_path, dvd_id=crc)
        return crc

    except ImportError as e:
        raise RuntimeError(
            "pydvdid-m is not installed. Install with: pip install pydvdid-m"
        ) from e
    except Exception as e:
        raise RuntimeError(f"Failed to compute DVD ID: {e}") from e


def _get_mount_point(device: str) -> str | None:
    """Get the mount point for a device.

    Args:
        device: Device path (e.g., /dev/sr0) or existing path

    Returns:
        Mount point path or None if not mounted
    """
    import os
    from pathlib import Path

    # If it's already a directory, assume it's a mount point
    if os.path.isdir(device):
        return device

    # Check /proc/mounts for the mount point
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mount_device = parts[0]
                    mount_point = parts[1]

                    # Match device or symlink target
                    if mount_device == device:
                        return mount_point

                    # Handle symlinks (e.g., /dev/cdrom -> /dev/sr0)
                    try:
                        if Path(mount_device).resolve() == Path(device).resolve():
                            return mount_point
                    except (OSError, ValueError):
                        pass

    except FileNotFoundError:
        pass

    # Check common mount points
    common_mounts = [
        "/mnt/dvd",
        "/mnt/cdrom",
        "/media/cdrom",
        "/media/dvd",
        f"/media/{os.getenv('USER', 'user')}/",
        "/run/media/",
    ]

    for mount in common_mounts:
        if os.path.isdir(mount):
            # Check if this is the right disc by looking for VIDEO_TS
            video_ts = Path(mount) / "VIDEO_TS"
            if video_ts.is_dir():
                return mount

            # Also check subdirectories (for auto-mounted discs)
            if mount.endswith("/"):
                for subdir in Path(mount).iterdir():
                    if subdir.is_dir():
                        video_ts = subdir / "VIDEO_TS"
                        if video_ts.is_dir():
                            return str(subdir)

    return None


def get_disc_label(device: str) -> str | None:
    """Get the volume label of a disc using blkid.

    Args:
        device: Device path (e.g., /dev/sr0)

    Returns:
        Volume label or None
    """
    import subprocess

    try:
        result = subprocess.run(
            ["blkid", "-s", "LABEL", "-o", "value", device],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    return None
