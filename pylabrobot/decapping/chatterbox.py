"""Chatterbox (simulation) backend for the DeCapper.

Prints actions to stdout and maintains minimal in-memory state – useful for
testing notebooks offline without a real instrument.
"""

from __future__ import annotations

import logging
from typing import List

from pylabrobot.decapping.backend import DecapperBackend, DecapperVersion, SensorStatus

logger = logging.getLogger(__name__)


class DecapperChatterboxBackend(DecapperBackend):
    """Simulation / chatterbox backend that logs operations."""

    def __init__(self):
        super().__init__()
        self._initialized = False
        self._cap_state: List[List[bool]] = [
            [True] * 12 for _ in range(8)
        ]  # 8 rows × 12 positions, True = capped
        self._tube_type = 1
        self._adapter_type = 1
        self._tube_detection = True
        self._params = {
            "special[0]": "0",
            "j_labware_tube_type": "1",
            "j_labware_adapter_type": "1",
        }

    async def setup(self) -> None:
        logger.info("[chatterbox] DeCapper setup")

    async def stop(self) -> None:
        logger.info("[chatterbox] DeCapper stop")

    async def initialize(self) -> None:
        logger.info("[chatterbox] MI – Module Init (homing all axes)")
        self._initialized = True

    async def home(self) -> None:
        logger.info("[chatterbox] HOME – Moving to park position")

    async def decap(self, row: int, pattern: str = "111111111111") -> str:
        logger.info("[chatterbox] DECAP row=%d pattern=%s", row, pattern)
        output = []
        for i, ch in enumerate(pattern):
            if ch == "1" and self._cap_state[row - 1][i]:
                self._cap_state[row - 1][i] = False
                output.append("1")
            else:
                output.append("0")
        result = "".join(output)
        logger.info("[chatterbox]   output_pattern=%s", result)
        return result

    async def cap(self, row: int, pattern: str = "111111111111") -> str:
        logger.info("[chatterbox] CAP row=%d pattern=%s", row, pattern)
        output = []
        for i, ch in enumerate(pattern):
            if ch == "1" and not self._cap_state[row - 1][i]:
                self._cap_state[row - 1][i] = True
                output.append("1")
            else:
                output.append("0")
        result = "".join(output)
        logger.info("[chatterbox]   output_pattern=%s", result)
        return result

    async def put_cap(self, magazine_row: int = 8, pattern: str = "111111111111") -> str:
        logger.info("[chatterbox] PUTCAP magazine_row=%d pattern=%s", magazine_row, pattern)
        return pattern

    async def get_cap(self, magazine_row: int = 8, pattern: str = "111111111111") -> str:
        logger.info("[chatterbox] GETCAP magazine_row=%d pattern=%s", magazine_row, pattern)
        return pattern

    async def scan_plate(self, pattern: str = "11111111") -> str:
        logger.info("[chatterbox] SCAN_P pattern=%s", pattern)
        return pattern

    async def scan_magazine(self, pattern: str = "11111111") -> str:
        logger.info("[chatterbox] SCAN_M pattern=%s", pattern)
        return pattern

    async def detect_tube_type(self) -> int:
        logger.info("[chatterbox] TUBE_TYPE → %d", self._tube_type)
        return self._tube_type

    async def detect_adapter_type(self) -> int:
        logger.info("[chatterbox] ADAPTER_TYPE → %d", self._adapter_type)
        return self._adapter_type

    async def get_param(self, name: str) -> str:
        val = self._params.get(name, "0")
        logger.info("[chatterbox] get_param(%s) → %s", name, val)
        return val

    async def set_param(self, name: str, value: str) -> None:
        logger.info("[chatterbox] set_param(%s, %s)", name, value)
        self._params[name] = value

    async def set_tube_type(self, tube_type: int) -> None:
        logger.info("[chatterbox] set_tube_type(%d)", tube_type)
        self._tube_type = tube_type
        self._params["j_labware_tube_type"] = str(tube_type)

    async def set_tube_detection(self, enabled: bool) -> None:
        logger.info("[chatterbox] set_tube_detection(%s)", enabled)
        self._tube_detection = enabled
        self._params["special[0]"] = "0" if enabled else "1"

    async def get_version(self) -> DecapperVersion:
        return DecapperVersion(
            module_name="DeCapper",
            module_mode="Chatterbox",
            debug_mode="Normal",
            version="0.0.0.0",
            variant="S",
            build_date="2026-01-01 00:00:00",
            description="Simulated capping and decapping of tubes",
        )

    async def get_sensors(self) -> SensorStatus:
        return SensorStatus(
            cover_detector=False,
            lid_detector=False,
            stripperbar_init=self._initialized,
            y_motor_init=self._initialized,
            z_motor_init=self._initialized,
        )

    async def get_screwer_torques(self) -> List[int]:
        return [0] * 12

    async def abort(self) -> None:
        logger.info("[chatterbox] ABORT")

    async def leds_on(self) -> None:
        logger.info("[chatterbox] LEDS_ON")

    async def leds_off(self) -> None:
        logger.info("[chatterbox] LEDS_OFF")

    async def send_raw(self, command: str) -> str:
        logger.info("[chatterbox] raw: %s", command)
        return f"00000.000 <- {command} er 0"
