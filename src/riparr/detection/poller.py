"""Disc detection using polling fallback.

For environments where udev is not available (e.g., containers without
access to /run/udev), this provides a polling-based alternative.
"""

import os
from collections.abc import Callable

import anyio
import structlog

log = structlog.get_logger()


class DevicePoller:
    """Poll optical drives for disc insertion/removal."""

    def __init__(
        self,
        devices: list[str] | None = None,
        interval: float = 5.0,
    ) -> None:
        """Initialize device poller.

        Args:
            devices: List of device paths to poll (e.g., ["/dev/sr0"])
            interval: Polling interval in seconds
        """
        self.devices = devices or ["/dev/sr0"]
        self.interval = interval
        self._running = False
        self._disc_states: dict[str, bool] = {}

    async def poll(
        self,
        on_insert: Callable[[str], None],
        on_remove: Callable[[str], None],
        once: bool = False,
    ) -> None:
        """Start polling for disc events.

        Args:
            on_insert: Callback when disc is inserted
            on_remove: Callback when disc is removed
            once: Stop after first insert event
        """
        self._running = True

        # Initialize disc states
        for device in self.devices:
            self._disc_states[device] = await self._check_disc(device)
            if self._disc_states[device]:
                log.info("Disc already present", device=device)

        log.info(
            "Starting polling monitor",
            devices=self.devices,
            interval=self.interval,
        )

        while self._running:
            await anyio.sleep(self.interval)

            for device in self.devices:
                if not self._running:
                    break

                has_disc = await self._check_disc(device)
                previous_state = self._disc_states.get(device, False)

                if has_disc and not previous_state:
                    # Disc inserted
                    log.debug("Disc detected (poll)", device=device)
                    self._disc_states[device] = True
                    on_insert(device)
                    if once:
                        self._running = False
                        return

                elif not has_disc and previous_state:
                    # Disc removed
                    log.debug("Disc removed (poll)", device=device)
                    self._disc_states[device] = False
                    on_remove(device)

    def stop(self) -> None:
        """Stop polling."""
        self._running = False

    async def _check_disc(self, device: str) -> bool:
        """Check if a disc is present in the device.

        Args:
            device: Device path

        Returns:
            True if disc is present
        """
        return await anyio.to_thread.run_sync(
            lambda: self._check_disc_sync(device)
        )

    def _check_disc_sync(self, device: str) -> bool:
        """Synchronous disc check.

        Uses multiple methods to detect disc presence.
        """
        # Method 1: Check if device can be opened
        try:
            fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
            os.close(fd)
        except OSError:
            return False

        # Method 2: Check /sys/block for disc state
        device_name = os.path.basename(device)
        events_path = f"/sys/block/{device_name}/events_poll_msecs"
        try:
            with open(events_path) as f:
                # If we can read this, the device exists and may have a disc
                pass
        except (FileNotFoundError, PermissionError):
            pass

        # Method 3: Try to read the disc label using blkid
        try:
            import subprocess

            result = subprocess.run(
                ["blkid", "-p", device],
                capture_output=True,
                timeout=5,
            )
            # blkid returns 0 if it finds a filesystem
            if result.returncode == 0 and result.stdout:
                return True
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        # Method 4: Check for VIDEO_TS (DVD) or BDMV (Blu-ray) structure
        # This requires the disc to be mounted
        mount_point = self._get_mount_point(device)
        if mount_point:
            video_ts = os.path.join(mount_point, "VIDEO_TS")
            bdmv = os.path.join(mount_point, "BDMV")
            if os.path.isdir(video_ts) or os.path.isdir(bdmv):
                return True

        # Method 5: Use ioctl to check drive status (Linux specific)
        try:
            import fcntl

            fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
            try:
                # CDROM_DRIVE_STATUS ioctl
                CDROM_DRIVE_STATUS = 0x5326
                CDS_DISC_OK = 4

                status = fcntl.ioctl(fd, CDROM_DRIVE_STATUS, 0)
                return status == CDS_DISC_OK
            finally:
                os.close(fd)
        except (OSError, IOError):
            pass

        return False

    def _get_mount_point(self, device: str) -> str | None:
        """Get mount point for a device.

        Args:
            device: Device path

        Returns:
            Mount point or None
        """
        try:
            with open("/proc/mounts") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] == device:
                        return parts[1]
        except (FileNotFoundError, PermissionError):
            pass

        return None


async def detect_drives_by_scan() -> list[str]:
    """Detect optical drives by scanning /dev.

    Returns:
        List of device paths
    """
    drives = []

    # Check common optical drive device names
    for pattern in ["/dev/sr", "/dev/cdrom", "/dev/dvd"]:
        if pattern.startswith("/dev/sr"):
            # Check sr0, sr1, etc.
            for i in range(4):
                device = f"{pattern}{i}"
                if os.path.exists(device):
                    drives.append(device)
        elif os.path.exists(pattern):
            # Resolve symlink
            real_path = os.path.realpath(pattern)
            if real_path not in drives:
                drives.append(real_path)

    return drives
