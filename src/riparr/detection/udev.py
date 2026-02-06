"""Disc detection using pyudev netlink monitoring.

This provides real-time disc insertion/removal events on Linux systems
with access to the udev socket.
"""

from collections.abc import Callable

import anyio
import structlog

log = structlog.get_logger()


class UdevMonitor:
    """Monitor optical drives using pyudev netlink events."""

    def __init__(self, devices: list[str] | None = None) -> None:
        """Initialize udev monitor.

        Args:
            devices: List of device paths to monitor (e.g., ["/dev/sr0"]).
                    If None, monitors all optical drives.
        """
        self.devices = devices or []
        self._running = False

    @staticmethod
    def is_available() -> bool:
        """Check if udev monitoring is available.

        Returns:
            True if pyudev can connect to udev socket
        """
        try:
            import pyudev

            context = pyudev.Context()
            # Try to create a monitor - this will fail if udev isn't accessible
            monitor = pyudev.Monitor.from_netlink(context)
            del monitor
            return True
        except (ImportError, OSError, Exception) as e:
            log.debug("udev not available", error=str(e))
            return False

    async def monitor(
        self,
        on_insert: Callable[[str], None],
        on_remove: Callable[[str], None],
        once: bool = False,
    ) -> None:
        """Start monitoring for disc events.

        Args:
            on_insert: Callback when disc is inserted (receives device path)
            on_remove: Callback when disc is removed (receives device path)
            once: Stop after first insert event
        """
        try:
            import pyudev
        except ImportError as e:
            raise RuntimeError("pyudev is not installed") from e

        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)

        # Filter for block devices (optical drives)
        monitor.filter_by(subsystem="block", device_type="disk")

        self._running = True

        log.info("Starting udev monitor", devices=self.devices)

        # Use anyio for async iteration
        monitor.start()

        try:
            while self._running:
                # Poll with timeout to allow cancellation
                device = await anyio.to_thread.run_sync(
                    lambda: monitor.poll(timeout=1.0)
                )

                if device is None:
                    continue

                # Check if this is an optical drive we care about
                device_path = device.device_node
                if not device_path:
                    continue

                if not self._is_optical_drive(device):
                    continue

                if self.devices and device_path not in self.devices:
                    continue

                action = device.action

                if action == "change":
                    # Check if disc was inserted or removed
                    if self._has_disc(device):
                        log.debug("Disc inserted event", device=device_path)
                        on_insert(device_path)
                        if once:
                            self._running = False
                    else:
                        log.debug("Disc removed event", device=device_path)
                        on_remove(device_path)

        except Exception as e:
            log.error("udev monitor error", error=str(e))
            raise
        finally:
            self._running = False

    def stop(self) -> None:
        """Stop monitoring."""
        self._running = False

    def _is_optical_drive(self, device) -> bool:
        """Check if device is an optical drive.

        Args:
            device: pyudev device object

        Returns:
            True if device is an optical drive
        """
        # Check device type
        id_type = device.get("ID_TYPE", "")
        if id_type == "cd":
            return True

        # Check device path pattern
        device_path = device.device_node or ""
        if "/sr" in device_path or "/cdrom" in device_path:
            return True

        # Check capabilities
        id_cdrom = device.get("ID_CDROM", "")
        if id_cdrom == "1":
            return True

        return False

    def _has_disc(self, device) -> bool:
        """Check if device has a disc inserted.

        Args:
            device: pyudev device object

        Returns:
            True if disc is present
        """
        # Check ID_CDROM_MEDIA
        has_media = device.get("ID_CDROM_MEDIA", "0")
        return has_media == "1"


async def detect_optical_drives() -> list[dict[str, str]]:
    """Detect available optical drives using udev.

    Returns:
        List of drive info dicts with device path and capabilities
    """
    try:
        import pyudev
    except ImportError:
        return []

    context = pyudev.Context()
    drives = []

    for device in context.list_devices(subsystem="block", DEVTYPE="disk"):
        device_path = device.device_node
        if not device_path:
            continue

        # Check if it's an optical drive
        id_cdrom = device.get("ID_CDROM", "")
        if id_cdrom != "1":
            continue

        drive_info = {
            "device": device_path,
            "model": device.get("ID_MODEL", "Unknown"),
            "vendor": device.get("ID_VENDOR", "Unknown"),
            "has_disc": device.get("ID_CDROM_MEDIA", "0") == "1",
            "capabilities": [],
        }

        # Check capabilities
        if device.get("ID_CDROM_DVD", "") == "1":
            drive_info["capabilities"].append("DVD")
        if device.get("ID_CDROM_BD", "") == "1":
            drive_info["capabilities"].append("Blu-ray")
        if device.get("ID_CDROM_MRW", "") == "1":
            drive_info["capabilities"].append("MRW")

        drives.append(drive_info)
        log.debug("Found optical drive", **drive_info)

    return drives
