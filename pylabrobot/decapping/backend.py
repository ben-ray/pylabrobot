"""Abstract backend for tube decappers / cappers.

This defines the interface contract that any concrete decapper backend must
implement.  The design follows the PyLabRobot Machine / MachineBackend pattern
(see ``pylabrobot.machines``).

Protocol overview (Hamilton DeCapper firmware reference):
    - The DeCapper has 12 screwer motors arranged in a row.
    - A 96-tube plate has 8 rows of 12 tubes → one row at a time.
    - ``DECAP`` removes caps; ``CAP`` replaces caps.
    - ``PUTCAP`` / ``GETCAP`` move caps to/from a cap magazine.
    - ``SCAN_P`` / ``SCAN_M`` detect which positions hold tubes/caps.
    - ``MI`` (Module Init) homes all axes.
"""

from __future__ import annotations

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from pylabrobot.machines.backend import MachineBackend


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class DecapperVersion:
    """Firmware / product identification."""
    module_name: str
    module_mode: str
    debug_mode: str
    version: str
    variant: str
    build_date: str
    description: str


@dataclass
class SensorStatus:
    """Snapshot of the five on-board binary sensors."""
    cover_detector: bool
    lid_detector: bool
    stripperbar_init: bool
    y_motor_init: bool
    z_motor_init: bool


ERROR_CODES = {
    0: "No error",
    1: "Internal Firmware error",
    5: "Unknown command",
    6: "Process busy",
    7: "Command aborted",
    8: "Wrong command format",
    10: "Wrong parameter format",
    11: "Parameter is read only",
    12: "Parameter not found",
    13: "Parameter has no value",
    14: "Parameter value out of range",
    100: "Y drive not initialized",
    101: "Y drive init sensor not found",
    102: "Y drive step loss (collision)",
    103: "Y drive end position not reached (timeout)",
    104: "Y drive illegal position",
    110: "Z drive not initialized",
    111: "Z drive init sensor not found",
    112: "Z drive step loss (collision)",
    113: "Z drive end position not reached (timeout)",
    114: "Z drive illegal position",
    120: "Stripperbar drive not initialized",
    121: "Stripperbar drive init sensor not found",
    122: "Stripperbar drive step loss (collision)",
    123: "Stripperbar drive end position not reached (timeout)",
    124: "Stripperbar illegal position",
    140: "Detector: cap found (not expected)",
    141: "Detector: no cap found (cap expected)",
    142: "Detector: tube found (not expected)",
    143: "Detector: no tube found (tube expected)",
    145: "Detector: no cap holder rack found",
    150: "Cover is open",
    155: "Waste overflow",
    160: "Tube type not identified",
    161: "Different tube types identified",
    162: "Detector is in labware",
    164: "Tube type has changed",
    170: "Adapter type not identified",
    180: "Labware has changed",
    190: "No tube rack found to deposit caps",
    210: "Wrong pattern (only '0' or '1' allowed)",
    300: "Command timeout",
}


class DecapperError(RuntimeError):
    """Raised when the DeCapper firmware returns a non-zero error code."""

    def __init__(self, error_code: int, raw_response: str = ""):
        self.error_code = error_code
        self.raw_response = raw_response
        desc = ERROR_CODES.get(error_code, f"Unknown error ({error_code})")
        super().__init__(f"DeCapper error {error_code}: {desc}")


# ── Abstract backend ─────────────────────────────────────────────────────────

class DecapperBackend(MachineBackend, metaclass=ABCMeta):
    """Abstract interface for a tube decapper / capper device.

    Every method below maps (roughly) to a firmware command exposed over the
    Hamilton DeCapper's HTTP/CGI interface.  A concrete backend translates these
    into actual HTTP traffic (see ``HamiltonDecapperBackend``) or into
    simulation stubs (see ``DecapperChatterboxBackend``).
    """

    # ── lifecycle ──────────────────────────────────────────────────────────────

    @abstractmethod
    async def setup(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    # ── high-level operations (row-level, 12 at a time) ────────────────────────

    @abstractmethod
    async def initialize(self) -> None:
        """MI – Module Init.  Homes Y, Z, and stripperbar axes."""
        ...

    @abstractmethod
    async def home(self) -> None:
        """HOME – Move all axes to home / park position."""
        ...

    @abstractmethod
    async def decap(
        self,
        row: int,
        pattern: str = "111111111111",
    ) -> str:
        """DECAP – Remove caps from *row* (1-8).

        Args:
            row: Plate row number (1-8).
            pattern: 12-char binary string selecting which of the 12 tubes to
                             decap ('1' = active, '0' = skip).

        Returns:
            ``screwer_outputpattern`` – which positions actually decapped.
        """
        ...

    @abstractmethod
    async def cap(
        self,
        row: int,
        pattern: str = "111111111111",
    ) -> str:
        """CAP – Apply caps to *row* (1-8).

        Returns:
            ``screwer_outputpattern``.
        """
        ...

    @abstractmethod
    async def put_cap(
        self,
        magazine_row: int = 8,
        pattern: str = "111111111111",
    ) -> str:
        """PUTCAP – Move caps from screwer head to cap magazine row.

        Returns:
            ``screwer_outputpattern``.
        """
        ...

    @abstractmethod
    async def get_cap(
        self,
        magazine_row: int = 8,
        pattern: str = "111111111111",
    ) -> str:
        """GETCAP – Pick caps from cap magazine row onto screwer head.

        Returns:
            ``screwer_outputpattern``.
        """
        ...

    # ── scanning ───────────────────────────────────────────────────────────────

    @abstractmethod
    async def scan_plate(self, pattern: str = "11111111") -> str:
        """SCAN_P – Scan plate rows for tube / cap presence.

        Args:
            pattern: 8-char binary string (rows 1-8) to scan.

        Returns:
            ``scan_outputpattern`` – 8-char result.
        """
        ...

    @abstractmethod
    async def scan_magazine(self, pattern: str = "11111111") -> str:
        """SCAN_M – Scan cap-magazine rows for cap presence.

        Returns:
            ``scan_outputpattern`` – 8-char result.
        """
        ...

    # ── tube / adapter detection ───────────────────────────────────────────────

    @abstractmethod
    async def detect_tube_type(self) -> int:
        """TUBE_TYPE – Auto-detect and set tube type.

        Returns:
            ``j_labware_tube_type`` (1-6).

        Note:
            This will raise ``DecapperError(160)`` if the tube type does not match
            any of the 6 predefined types.  For custom tuberacks, use
            :meth:`set_tube_type` and :meth:`set_tube_detection` instead.
        """
        ...

    @abstractmethod
    async def detect_adapter_type(self) -> int:
        """ADAPTER_TYPE – Auto-detect and set adapter type.

        Returns:
            ``j_labware_adapter_type`` (1-5).
        """
        ...

    # ── parameter access ───────────────────────────────────────────────────────

    @abstractmethod
    async def get_param(self, name: str) -> str:
        """Read a firmware parameter by name.

        Args:
            name: Parameter name, e.g. ``"j_labware_tube_type"`` or ``"special[0]"``.

        Returns:
            The parameter value as a string.
        """
        ...

    @abstractmethod
    async def set_param(self, name: str, value: str) -> None:
        """Write a firmware parameter.

        Args:
            name: Parameter name.
            value: New value (as a string).
        """
        ...

    @abstractmethod
    async def set_tube_type(self, tube_type: int) -> None:
        """Directly set the stored tube type (1-6) without auto-detection.

        Useful for custom tuberacks where TUBE_TYPE auto-detection fails.
        """
        ...

    @abstractmethod
    async def set_tube_detection(self, enabled: bool) -> None:
        """Enable or disable cap/tube detection during DECAP/CAP.

        When disabled (``special[0]=1``), the device skips the cap/tube presence
        check before decapping or capping.  This is required for custom tuberacks
        that fail TUBE_TYPE auto-detection (error 160).

        Args:
            enabled: ``True`` to enable detection (default), ``False`` to disable.
        """
        ...

    # ── queries ────────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_version(self) -> DecapperVersion:
        """R_VERSION – Read firmware identification."""
        ...

    @abstractmethod
    async def get_sensors(self) -> SensorStatus:
        """R_SENSOR – Read binary sensor states."""
        ...

    @abstractmethod
    async def get_screwer_torques(self) -> List[int]:
        """R_SCREWER_TORQUE – Read actual torque [mNm] for all 12 screwers."""
        ...

    # ── special ────────────────────────────────────────────────────────────────

    @abstractmethod
    async def abort(self) -> None:
        """ABORT – Abort any running command."""
        ...

    @abstractmethod
    async def leds_on(self) -> None:
        """LEDS_ON – Turn blue indicator LEDs on."""
        ...

    @abstractmethod
    async def leds_off(self) -> None:
        """LEDS_OFF – Turn blue indicator LEDs off."""
        ...

    @abstractmethod
    async def send_raw(self, command: str) -> str:
        """Send an arbitrary firmware command string and return the raw
        response.  Useful for commands not yet wrapped by this interface."""
        ...
