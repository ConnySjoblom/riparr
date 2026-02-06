"""Disc detection orchestrator.

Automatically selects the best available detection method (udev or polling)
and provides a unified interface for disc events.
"""

from collections.abc import Awaitable, Callable
from typing import Literal

import anyio
import structlog

from riparr.detection.poller import DevicePoller, detect_drives_by_scan
from riparr.detection.udev import UdevMonitor, detect_optical_drives

log = structlog.get_logger()


DetectionMethod = Literal["auto", "udev", "polling"]


class DiscWatcher:
    """Watch for disc insertion and removal events."""

    def __init__(
        self,
        devices: list[str] | None = None,
        method: DetectionMethod = "auto",
        poll_interval: float = 5.0,
    ) -> None:
        """Initialize disc watcher.

        Args:
            devices: List of device paths to watch. If None, auto-detects.
            method: Detection method (auto, udev, polling)
            poll_interval: Polling interval when using polling method
        """
        self.devices = devices
        self.method = method
        self.poll_interval = poll_interval
        self._running = False
        self._selected_method: str | None = None

    async def start(
        self,
        on_insert: Callable[[str], Awaitable[None]],
        on_remove: Callable[[str], Awaitable[None]],
        once: bool = False,
    ) -> None:
        """Start watching for disc events.

        Args:
            on_insert: Async callback when disc is inserted
            on_remove: Async callback when disc is removed
            once: Stop after first insert event
        """
        # Auto-detect devices if not specified
        if not self.devices:
            self.devices = await self._detect_devices()

        if not self.devices:
            log.warning("No optical drives found")
            return

        # Select detection method
        if self.method == "auto":
            if UdevMonitor.is_available():
                self._selected_method = "udev"
            else:
                self._selected_method = "polling"
                log.info(
                    "udev not available, using polling fallback",
                    interval=self.poll_interval,
                )
        else:
            self._selected_method = self.method

        log.info(
            "Starting disc watcher",
            method=self._selected_method,
            devices=self.devices,
        )

        self._running = True

        try:
            if self._selected_method == "udev":
                # Wrap async callbacks for sync udev monitor (runs in thread)
                def sync_on_insert(device: str) -> None:
                    anyio.from_thread.run(on_insert, device)

                def sync_on_remove(device: str) -> None:
                    anyio.from_thread.run(on_remove, device)

                monitor = UdevMonitor(self.devices)
                await monitor.monitor(sync_on_insert, sync_on_remove, once=once)
            else:
                # Poller is fully async, pass callbacks directly
                poller = DevicePoller(self.devices, interval=self.poll_interval)
                await poller.poll(on_insert, on_remove, once=once)
        except Exception as e:
            log.error("Disc watcher error", error=str(e))
            raise
        finally:
            self._running = False

    def stop(self) -> None:
        """Stop watching for disc events."""
        self._running = False

    async def _detect_devices(self) -> list[str]:
        """Auto-detect optical drive devices.

        Returns:
            List of device paths
        """
        # Try udev detection first
        if UdevMonitor.is_available():
            drives = await detect_optical_drives()
            if drives:
                return [d["device"] for d in drives]

        # Fall back to scanning /dev
        return await detect_drives_by_scan()

    async def check_disc(self, device: str) -> bool:
        """Check if a disc is present in the device.

        Args:
            device: Device path

        Returns:
            True if disc is present
        """
        poller = DevicePoller([device])
        return await poller._check_disc(device)

    async def get_drives(self) -> list[dict]:
        """Get list of available optical drives with status.

        Returns:
            List of drive info dicts
        """
        # Try udev first for more detailed info
        if UdevMonitor.is_available():
            return await detect_optical_drives()

        # Fall back to basic scan
        devices = await detect_drives_by_scan()
        drives = []

        for device in devices:
            has_disc = await self.check_disc(device)
            drives.append({
                "device": device,
                "has_disc": has_disc,
            })

        return drives


async def wait_for_disc(
    device: str | None = None,
    timeout: float | None = None,
) -> str:
    """Wait for a disc to be inserted.

    Args:
        device: Specific device to watch, or None for any
        timeout: Maximum wait time in seconds, or None for infinite

    Returns:
        Device path where disc was inserted

    Raises:
        TimeoutError: If timeout expires before disc is inserted
    """
    inserted_device: str | None = None
    event = anyio.Event()

    async def on_insert(dev: str) -> None:
        nonlocal inserted_device
        inserted_device = dev
        event.set()

    async def on_remove(dev: str) -> None:
        pass

    watcher = DiscWatcher(devices=[device] if device else None)

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(watcher.start, on_insert, on_remove, True)

            if timeout:
                with anyio.move_on_after(timeout) as cancel_scope:
                    await event.wait()
                if cancel_scope.cancelled_caught:
                    watcher.stop()
                    raise TimeoutError(f"No disc inserted within {timeout} seconds")
            else:
                await event.wait()

    except Exception:
        watcher.stop()
        raise

    if inserted_device is None:
        raise RuntimeError("No device captured")

    return inserted_device
