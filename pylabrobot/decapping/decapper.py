"""Decapper – PyLabRobot-style frontend for tube capping/decapping devices.

The ``Decapper`` class is the user-facing entry-point.  It follows the
``Machine`` + ``ResourceHolder`` pattern from PyLabRobot so it can be placed
on a deck, hold a plate, and be driven by any ``DecapperBackend``.

The ``child_location`` is required and must be set to the Coordinate that
places the plate correctly for the specific workcell integration.  Physical
size defaults to 0 — only ``child_location`` matters for iSWAP handling.
"""

from __future__ import annotations

from typing import List, Optional

from pylabrobot.machines.machine import Machine, need_setup_finished
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.coordinate import Coordinate

from pylabrobot.decapping.backend import DecapperBackend, DecapperVersion, SensorStatus


class Decapper(ResourceHolder, Machine):
    """A tube (de)capping instrument.

    Typical usage::

            from pylabrobot.decapping import Decapper, HamiltonDecapperBackend
            from pylabrobot.resources.coordinate import Coordinate

            backend = HamiltonDecapperBackend(host="192.168.1.1")
            decapper = Decapper(
                name="decapper",
                backend=backend,
                child_location=Coordinate(x=50.0, y=30.0, z=150.0),
            )

            await decapper.setup()
            await decapper.initialize()          # home all axes

            result = await decapper.decap(row=1) # decap row A
            print("Decapped pattern:", result)

            await decapper.cap(row=1)            # re-cap row A
            await decapper.home()
            await decapper.stop()
    """

    def __init__(
        self,
        name: str,
        backend: DecapperBackend,
        child_location: Coordinate,
        size_x: float = 0.0,
        size_y: float = 0.0,
        size_z: float = 0.0,
        category: str = "decapper",
        model: Optional[str] = None,
    ):
        ResourceHolder.__init__(
            self,
            name=name,
            size_x=size_x,
            size_y=size_y,
            size_z=size_z,
            child_location=child_location,
            category=category,
            model=model,
        )
        Machine.__init__(self, backend=backend)
        self.backend: DecapperBackend = backend

    # ── lifecycle ──────────────────────────────────────────────────────────────

    async def setup(self, **kwargs) -> None:
        await Machine.setup(self, **kwargs)

    @need_setup_finished
    async def stop(self) -> None:
        await Machine.stop(self)

    # ── high-level operations ──────────────────────────────────────────────────

    @need_setup_finished
    async def initialize(self) -> None:
        """Home all axes (MI – Module Init)."""
        await self.backend.initialize()

    @need_setup_finished
    async def home(self) -> None:
        """Move all axes to park / home position."""
        await self.backend.home()

    @need_setup_finished
    async def decap(
        self,
        row: int,
        pattern: str = "111111111111",
    ) -> str:
        """Remove caps from *row* (1-8).

        Args:
            row: Plate row (1 = A, 2 = B, … 8 = H).
            pattern: 12-char '0'/'1' string selecting which tubes to decap.

        Returns:
            12-char output pattern indicating which positions actually decapped.
        """
        self._validate_row(row)
        self._validate_pattern(pattern, 12)
        return await self.backend.decap(row, pattern)

    @need_setup_finished
    async def cap(
        self,
        row: int,
        pattern: str = "111111111111",
    ) -> str:
        """Apply caps to *row* (1-8).

        Returns:
            12-char output pattern.
        """
        self._validate_row(row)
        self._validate_pattern(pattern, 12)
        return await self.backend.cap(row, pattern)

    @need_setup_finished
    async def decap_all(self) -> List[str]:
        """Decap every row (1-8) sequentially.

        Returns:
            List of 8 output-pattern strings (one per row).
        """
        results = []
        for row in range(1, 9):
            results.append(await self.decap(row))
        return results

    @need_setup_finished
    async def cap_all(self) -> List[str]:
        """Re-cap every row (1-8) sequentially.

        Returns:
            List of 8 output-pattern strings.
        """
        results = []
        for row in range(1, 9):
            results.append(await self.cap(row))
        return results

    @need_setup_finished
    async def put_cap(
        self,
        magazine_row: int = 8,
        pattern: str = "111111111111",
    ) -> str:
        """Move caps from the screwer head into the cap magazine."""
        return await self.backend.put_cap(magazine_row, pattern)

    @need_setup_finished
    async def get_cap(
        self,
        magazine_row: int = 8,
        pattern: str = "111111111111",
    ) -> str:
        """Pick caps from the cap magazine onto the screwer head."""
        return await self.backend.get_cap(magazine_row, pattern)

    # ── scanning ───────────────────────────────────────────────────────────────

    @need_setup_finished
    async def scan_plate(self, pattern: str = "11111111") -> str:
        """Scan which plate rows have tubes/caps.

        Args:
            pattern: 8-char '0'/'1' string selecting which rows to scan.

        Returns:
            8-char pattern where '1' = tube/cap found.
        """
        self._validate_pattern(pattern, 8)
        return await self.backend.scan_plate(pattern)

    @need_setup_finished
    async def scan_magazine(self, pattern: str = "11111111") -> str:
        """Scan which magazine rows have caps."""
        self._validate_pattern(pattern, 8)
        return await self.backend.scan_magazine(pattern)

    # ── tube / adapter detection ───────────────────────────────────────────────

    @need_setup_finished
    async def detect_tube_type(self) -> int:
        """Auto-detect tube type (1-6).

        Note:
            Raises ``DecapperError(160)`` for custom tuberacks.  Use
            :meth:`set_tube_type` and :meth:`set_tube_detection` instead.
        """
        return await self.backend.detect_tube_type()

    @need_setup_finished
    async def detect_adapter_type(self) -> int:
        """Auto-detect adapter type (1-5)."""
        return await self.backend.detect_adapter_type()

    # ── parameter access ───────────────────────────────────────────────────────

    @need_setup_finished
    async def get_param(self, name: str) -> str:
        """Read a firmware parameter by name."""
        return await self.backend.get_param(name)

    @need_setup_finished
    async def set_param(self, name: str, value: str) -> None:
        """Write a firmware parameter."""
        await self.backend.set_param(name, value)

    @need_setup_finished
    async def set_tube_type(self, tube_type: int) -> None:
        """Set tube type (1-6) directly, bypassing auto-detection.

        Useful for custom tuberacks where :meth:`detect_tube_type` fails.
        """
        await self.backend.set_tube_type(tube_type)

    @need_setup_finished
    async def set_tube_detection(self, enabled: bool) -> None:
        """Enable/disable cap/tube detection during DECAP/CAP.

        When disabled, the device skips the cap/tube presence check, which is
        required for custom tuberacks that fail ``TUBE_TYPE`` auto-detection.
        """
        await self.backend.set_tube_detection(enabled)

    # ── queries ────────────────────────────────────────────────────────────────

    @need_setup_finished
    async def get_version(self) -> DecapperVersion:
        """Return firmware version information."""
        return await self.backend.get_version()

    @need_setup_finished
    async def get_sensors(self) -> SensorStatus:
        """Read the five binary sensor states."""
        return await self.backend.get_sensors()

    @need_setup_finished
    async def get_screwer_torques(self) -> List[int]:
        """Read actual torque (mNm) for all 12 screwer motors."""
        return await self.backend.get_screwer_torques()

    # ── special ────────────────────────────────────────────────────────────────

    @need_setup_finished
    async def abort(self) -> None:
        """Abort any running command."""
        await self.backend.abort()

    @need_setup_finished
    async def leds_on(self) -> None:
        """Turn blue indicator LEDs on."""
        await self.backend.leds_on()

    @need_setup_finished
    async def leds_off(self) -> None:
        """Turn blue indicator LEDs off."""
        await self.backend.leds_off()

    @need_setup_finished
    async def send_raw(self, command: str) -> str:
        """Send an arbitrary firmware command (for debugging / advanced use)."""
        return await self.backend.send_raw(command)

    # ── validation helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _validate_row(row: int) -> None:
        if not 1 <= row <= 8:
            raise ValueError(f"row must be 1-8, got {row}")

    @staticmethod
    def _validate_pattern(pattern: str, length: int) -> None:
        if len(pattern) != length:
            raise ValueError(f"pattern must be {length} chars, got {len(pattern)}")
        if not all(c in "01" for c in pattern):
            raise ValueError(f"pattern must contain only '0' and '1', got {pattern!r}")

    # ── convenience constructor ────────────────────────────────────────────────

    @classmethod
    def hamilton_decapper(
        cls,
        child_location: Coordinate,
        name: str = "decapper",
        host: str = "192.168.1.1",
        adapter_type: int = 1,
        tube_type: int = 1,
    ) -> "Decapper":
        """Factory for a Hamilton DeCapper with sensible defaults.

        Usage::

                decapper = Decapper.hamilton_decapper(
                    child_location=Coordinate(x=50.0, y=30.0, z=150.0),
                    host="192.168.1.1",
                )
                await decapper.setup()
        """
        from pylabrobot.decapping.hamilton_backend import HamiltonDecapperBackend

        backend = HamiltonDecapperBackend(
            host=host,
            adapter_type=adapter_type,
            tube_type=tube_type,
        )
        return cls(
            name=name,
            backend=backend,
            child_location=child_location,
            model="hamilton_decapper",
        )
