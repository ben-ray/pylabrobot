"""Concrete backend: Hamilton DeCapper over Hamilton Smart Protocol.

Talks to the Hamilton DeCapper (default ``192.168.1.1``) using the native
**Hamilton Smart Protocol** on TCP port **34567**.  This is the same protocol
the firmware exposes over RS-232 (57600 8N1) and is used by Hamilton's own
SiLA driver.

Protocol
--------
- **Send**: ``COMMAND [params...]\\n``  (plain ASCII, LF-terminated)
- **Receive**: ``COMMAND er <error_code> [<data_label> <data>...]\\r\\n``
- **Persistent**: Multiple commands can be sent on a single TCP connection.
- **No URL limit**: Unlike the HTTP/CGI web interface on port 80, this
  direct TCP protocol has no URL buffer restriction.

The device also exposes an HTTP/CGI web interface on port 80 with a
~76-character URL buffer limit, but we avoid it entirely in favour of
the Smart Protocol.  The only CGI endpoints still used are the telemetry
polling endpoints (sensor and motor values) which are not available over
the Smart Protocol.

Additional polling endpoints (HTTP, port 80):

    - ``GET /cgi/decapper_sensor_value.cgi``  → comma-separated id,value pairs
    - ``GET /cgi/decapper_dcmotor_value.cgi`` → comma-separated id,value pairs
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Dict, List, Optional, Tuple

from pylabrobot.decapping.backend import (
    DecapperBackend,
    DecapperError,
    DecapperVersion,
    SensorStatus,
)

logger = logging.getLogger(__name__)


def _parse_response(raw: str) -> Tuple[str, int, str]:
    """Parse a Hamilton Smart Protocol response line.

    The Smart Protocol (port 34567) returns::

        COMMAND er <errcode> [<data_key> <data_values>...]

    The CGI interface (port 80) returns::

        <timestamp> <- COMMAND er <errcode> [<data_key> <data_values>...]

    This parser handles both formats.

    Returns:
        (command_echo, error_code, remainder_after_error_code)
    """
    raw = raw.strip()
    # Try Smart Protocol format first: "COMMAND er <code> ..."
    match = re.match(
        r"(\S+)\s+er\s+(-?\d+)(.*)",
        raw,
        re.DOTALL,
    )
    if match is not None:
        cmd = match.group(1)
        err = int(match.group(2))
        rest = match.group(3).strip()
        return cmd, err, rest
    raise ValueError(f"Cannot parse DeCapper response: {raw!r}")


def _parse_csv_values(rest: str, label: str) -> List[str]:
    """Extract comma-separated quoted or unquoted values after *label*.

    E.g. ``r_version "DeCapper","Runfirmware",...`` → ['DeCapper', 'Runfirmware', ...]
    """
    if label in rest:
        rest = rest[rest.index(label) + len(label):].strip()
    parts = [p.strip().strip('"') for p in rest.split(",")]
    return parts


class HamiltonDecapperBackend(DecapperBackend):
    """Hamilton Smart Protocol backend for the Hamilton DeCapper.

    Communicates over a persistent TCP connection on port 34567 using the
    native Hamilton Smart Protocol.  This is the same framing used by the
    RS-232 interface (57600 8N1) and Hamilton's own SiLA driver.

    Unlike the HTTP/CGI web interface on port 80, the Smart Protocol has
    **no URL buffer limit** — commands of arbitrary length can be sent.

    The connection is opened lazily on :meth:`setup` and kept alive for the
    lifetime of the backend.  If the connection drops, it is transparently
    re-established on the next command.

    Args:
        host: IP address of the DeCapper (default ``192.168.1.1``).
        port: Hamilton Smart Protocol TCP port (default ``34567``).
        http_port: HTTP port for CGI polling endpoints (default ``80``).
        timeout: Read timeout in seconds (default ``120``).
                 Many mechanical commands take 30-60 s to complete.
        adapter_type: Labware adapter type (1-5, default 1).
        tube_type: Labware tube type (1-6, default 1).
    """

    def __init__(
        self,
        host: str = "192.168.1.1",
        port: int = 34567,
        http_port: int = 80,
        timeout: float = 120.0,
        adapter_type: int = 1,
        tube_type: int = 1,
    ):
        super().__init__()
        self.host = host
        self.port = port
        self.http_port = http_port
        self.timeout = timeout
        self.adapter_type = adapter_type
        self.tube_type = tube_type
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    # ── lifecycle ──────────────────────────────────────────────────────────────

    async def _ensure_connection(self) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Return the persistent (reader, writer) pair, reconnecting if needed."""
        if self._writer is not None and not self._writer.is_closing():
            return self._reader, self._writer  # type: ignore[return-value]
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=self.timeout,
        )
        logger.info(
            "Hamilton Smart Protocol connection opened to %s:%s",
            self.host, self.port,
        )
        return self._reader, self._writer

    async def setup(self) -> None:
        """Open a persistent TCP connection to the DeCapper."""
        try:
            await self._ensure_connection()
        except (OSError, asyncio.TimeoutError) as exc:
            raise ConnectionError(
                f"Cannot reach DeCapper at {self.host}:{self.port}: {exc}"
            ) from exc

    async def stop(self) -> None:
        """Close the persistent connection."""
        if self._writer is not None:
            self._writer.close()
            await self._writer.wait_closed()
            self._writer = None
            self._reader = None
        logger.info("HamiltonDecapperBackend stopped")

    # ── low-level Smart Protocol transport ─────────────────────────────────────

    async def _drain_stale(self, reader: asyncio.StreamReader) -> None:
        """Discard any stale data sitting in the read buffer.

        This can happen when another TCP connection to the same device
        triggers firmware echoes, or when a previous cell leaves unconsumed
        response lines.  We read with a very short timeout until the buffer
        is empty.
        """
        while True:
            try:
                stale = await asyncio.wait_for(reader.readline(), timeout=0.05)
                if stale:
                    logger.debug("⊘ drained stale: %s", stale.decode("utf-8", errors="replace").strip())
                else:
                    break
            except asyncio.TimeoutError:
                break

    async def _send_and_receive(self, command: str) -> str:
        """Send a command over the Hamilton Smart Protocol and return the
        response line.

        Protocol: send ``COMMAND\\n``, receive ``COMMAND er <code> ...\\r\\n``.
        The connection is persistent — multiple commands reuse the same socket.
        """
        reader, writer = await self._ensure_connection()

        # Discard any stale data left in the read buffer from previous
        # interactions (e.g. another connection triggering firmware echoes).
        await self._drain_stale(reader)

        logger.debug("→ %s", command)
        writer.write((command + "\n").encode("ascii"))
        await writer.drain()

        try:
            data = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
        except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
            # Connection may have dropped — close and let next call reconnect.
            logger.warning("Connection lost during read: %s", exc)
            self._writer = None
            self._reader = None
            raise

        response = data.decode("utf-8", errors="replace").strip()
        logger.debug("← %s", response)
        return response

    async def send_command(self, command: str) -> Tuple[int, str]:
        """Send a command and return ``(error_code, data_rest)``.

        Raises ``DecapperError`` on non-zero error codes.
        """
        raw = await self._send_and_receive(command)
        _cmd_echo, err_code, rest = _parse_response(raw)
        if err_code != 0:
            raise DecapperError(err_code, raw)
        return err_code, rest

    async def send_raw(self, command: str) -> str:
        """Send a raw command and return the full response string."""
        return await self._send_and_receive(command)

    # ── HTTP/CGI helpers (for telemetry polling only) ──────────────────────────

    async def _raw_get(self, path: str) -> str:
        """Open a one-shot TCP connection to the HTTP port and send a GET.

        Used only for the CGI polling endpoints (sensor / motor values) which
        are not available over the Smart Protocol.  The CGI endpoints return
        raw text without HTTP headers, so we read until EOF (FIN).
        """
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.http_port),
            timeout=self.timeout,
        )
        try:
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {self.host}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            )
            writer.write(request.encode("ascii"))
            await writer.drain()
            chunks: list[bytes] = []
            while True:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=self.timeout)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", errors="replace").strip()
        finally:
            writer.close()
            await writer.wait_closed()

    # ── high-level operations ──────────────────────────────────────────────────

    async def initialize(self) -> None:
        await self.send_command("MI")

    async def home(self) -> None:
        await self.send_command("HOME")

    async def decap(self, row: int, pattern: str = "111111111111") -> str:
        cmd = (
            f'DECAP plate_row {row} screwer_pattern "{pattern}"'
            f" j_labware_adapter_type {self.adapter_type}"
            f" j_labware_tube_type {self.tube_type}"
        )
        _err, rest = await self.send_command(cmd)
        vals = _parse_csv_values(rest, "screwer_outputpattern")
        return vals[0] if vals else ""

    async def cap(self, row: int, pattern: str = "111111111111") -> str:
        cmd = (
            f'CAP plate_row {row} screwer_pattern "{pattern}"'
            f" j_labware_adapter_type {self.adapter_type}"
            f" j_labware_tube_type {self.tube_type}"
        )
        _err, rest = await self.send_command(cmd)
        vals = _parse_csv_values(rest, "screwer_outputpattern")
        return vals[0] if vals else ""

    async def put_cap(self, magazine_row: int = 8, pattern: str = "111111111111") -> str:
        cmd = (
            f'PUTCAP magazine_row {magazine_row} screwer_pattern "{pattern}"'
            f" j_labware_adapter_type {self.adapter_type}"
            f" j_labware_tube_type {self.tube_type}"
        )
        _err, rest = await self.send_command(cmd)
        vals = _parse_csv_values(rest, "screwer_outputpattern")
        return vals[0] if vals else ""

    async def get_cap(self, magazine_row: int = 8, pattern: str = "111111111111") -> str:
        cmd = (
            f'GETCAP magazine_row {magazine_row} screwer_pattern "{pattern}"'
            f" j_labware_adapter_type {self.adapter_type}"
            f" j_labware_tube_type {self.tube_type}"
        )
        _err, rest = await self.send_command(cmd)
        vals = _parse_csv_values(rest, "screwer_outputpattern")
        return vals[0] if vals else ""

    # ── scanning ───────────────────────────────────────────────────────────────

    async def scan_plate(self, pattern: str = "11111111") -> str:
        cmd = f'SCAN_P scan_pattern "{pattern}"'
        _err, rest = await self.send_command(cmd)
        vals = _parse_csv_values(rest, "scan_outputpattern")
        return vals[0] if vals else ""

    async def scan_magazine(self, pattern: str = "11111111") -> str:
        cmd = f'SCAN_M scan_pattern "{pattern}"'
        _err, rest = await self.send_command(cmd)
        vals = _parse_csv_values(rest, "scan_outputpattern")
        return vals[0] if vals else ""

    # ── tube / adapter detection ───────────────────────────────────────────────

    async def detect_tube_type(self) -> int:
        _err, rest = await self.send_command("TUBE_TYPE")
        m = re.search(r"j_labware_tube_type\s+(\d+)", rest)
        tube_type = int(m.group(1)) if m else 0
        self.tube_type = tube_type
        return tube_type

    async def detect_adapter_type(self) -> int:
        _err, rest = await self.send_command("ADAPTER_TYPE")
        m = re.search(r"j_labware_adapter_type\s+(\d+)", rest)
        adapter_type = int(m.group(1)) if m else 0
        self.adapter_type = adapter_type
        return adapter_type

    # ── parameter access ───────────────────────────────────────────────────────

    async def get_param(self, name: str) -> str:
        """Read a firmware parameter by name using the ``RA`` command.

        Example: ``await backend.get_param("special[0]")``  →  ``"0"``
        """
        cmd = f'RA ra "{name}"'
        raw = await self._send_and_receive(cmd)
        _cmd, err, rest = _parse_response(raw)
        if err != 0:
            raise DecapperError(err, raw)
        m = re.search(rf"{re.escape(name)}\s+(.+)", rest)
        return m.group(1).strip().strip('"') if m else rest

    async def set_param(self, name: str, value: str) -> None:
        """Write a firmware parameter.

        Example: ``await backend.set_param("special[0]", "1")``
        """
        cmd = f"RA {name} {value}"
        raw = await self._send_and_receive(cmd)
        _cmd, err, _rest = _parse_response(raw)
        if err != 0:
            raise DecapperError(err, raw)

    async def set_tube_type(self, tube_type: int) -> None:
        if not 1 <= tube_type <= 6:
            raise ValueError(f"tube_type must be 1-6, got {tube_type}")
        await self.set_param("j_labware_tube_type", str(tube_type))
        self.tube_type = tube_type

    async def set_adapter_type(self, adapter_type: int) -> None:
        if not 1 <= adapter_type <= 5:
            raise ValueError(f"adapter_type must be 1-5, got {adapter_type}")
        await self.set_param("j_labware_adapter_type", str(adapter_type))
        self.adapter_type = adapter_type

    async def set_tube_detection(self, enabled: bool) -> None:
        await self.set_param("special[0]", "0" if enabled else "1")

    # ── queries ────────────────────────────────────────────────────────────────

    async def get_version(self) -> DecapperVersion:
        _err, rest = await self.send_command("R_VERSION")
        vals = _parse_csv_values(rest, "r_version")
        return DecapperVersion(
            module_name=vals[0] if len(vals) > 0 else "",
            module_mode=vals[1] if len(vals) > 1 else "",
            debug_mode=vals[2] if len(vals) > 2 else "",
            version=vals[3] if len(vals) > 3 else "",
            variant=vals[4] if len(vals) > 4 else "",
            build_date=vals[5] if len(vals) > 5 else "",
            description=vals[6] if len(vals) > 6 else "",
        )

    async def get_sensors(self) -> SensorStatus:
        _err, rest = await self.send_command("R_SENSOR")
        vals = _parse_csv_values(rest, "r_sensor")
        bools = [v.strip() == "1" for v in vals]
        while len(bools) < 5:
            bools.append(False)
        return SensorStatus(
            cover_detector=bools[0],
            lid_detector=bools[1],
            stripperbar_init=bools[2],
            y_motor_init=bools[3],
            z_motor_init=bools[4],
        )

    async def get_screwer_torques(self) -> List[int]:
        _err, rest = await self.send_command("R_SCREWER_TORQUE")
        vals = _parse_csv_values(rest, "r_screwer_torque")
        return [int(v) for v in vals if v.isdigit()]

    # ── special ────────────────────────────────────────────────────────────────

    async def abort(self) -> None:
        await self.send_command("ABORT")

    async def leds_on(self) -> None:
        await self.send_command("LEDS_ON")

    async def leds_off(self) -> None:
        await self.send_command("LEDS_OFF")

    # ── polling endpoints (HTTP/CGI, port 80) ──────────────────────────────────

    async def poll_sensors(self) -> Dict[str, str]:
        """Hit the sensor polling CGI and return {id: value} dict."""
        text = await self._raw_get("/cgi/decapper_sensor_value.cgi")
        pairs = text.strip().split(",")
        return {pairs[i]: pairs[i + 1] for i in range(0, len(pairs) - 1, 2)}

    async def poll_motors(self) -> Dict[str, str]:
        """Hit the DC motor polling CGI and return {id: value} dict."""
        text = await self._raw_get("/cgi/decapper_dcmotor_value.cgi")
        pairs = text.strip().split(",")
        return {pairs[i]: pairs[i + 1] for i in range(0, len(pairs) - 1, 2)}

    # ── serialization ──────────────────────────────────────────────────────────

    def serialize(self) -> dict:
        return {
            **super().serialize(),
            "host": self.host,
            "port": self.port,
            "http_port": self.http_port,
            "timeout": self.timeout,
            "adapter_type": self.adapter_type,
            "tube_type": self.tube_type,
        }
