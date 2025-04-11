import asyncio
import concurrent.futures
from enum import Enum
from typing import Literal

from pylabrobot.heating_shaking.backend import HeaterShakerBackend
from pylabrobot.io.usb import USB


class PlateLockPosition(Enum):
  LOCKED = 1
  UNLOCKED = 0


class HamiltonHeatShaker(HeaterShakerBackend):
  """
  Backend for Hamilton Heater Shaker devices connected through a Heater Shaker Box.
  """

  def __init__(
    self,
    shaker_index: int,
    id_vendor: int = 0x8AF,
    id_product: int = 0x8002,
  ) -> None:
    """
    Multiple Hamilton Heater Shakers can be connected to the same Heat Shaker Box.
    Each has a separate 'shaker index'.
    """
    assert shaker_index >= 0, "Shaker index must be non-negative"
    self.shaker_index = shaker_index
    self.command_id = 0

    super().__init__()
    # Save USB configuration for later creation in our dedicated thread.
    self.id_vendor = id_vendor
    self.id_product = id_product
    self.io = None

    # Create a dedicated executor with a single worker.
    self._usb_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

  async def setup(self):
    """
    Create and set up the USB instance in the dedicated thread.
    USB.setup (an async method) is run via asyncio.run() so that it’s awaited in that thread.
    """
    loop = asyncio.get_running_loop()

    def create_and_setup_usb():
      usb = USB(id_vendor=self.id_vendor, id_product=self.id_product)
      # USB.setup is a coroutine – run it to completion in this thread.
      asyncio.run(usb.setup())
      return usb

    # Create and set up the USB device in our dedicated executor.
    self.io = await loop.run_in_executor(self._usb_executor, create_and_setup_usb)
    # Continue with initialization (which uses _send_command and thus runs on the same thread)
    await self._initialize_lock()

  async def stop(self):
    loop = asyncio.get_running_loop()

    def stop_usb():
      asyncio.run(self.io.stop())

    await loop.run_in_executor(self._usb_executor, stop_usb)
    self._usb_executor.shutdown(wait=False)

  def serialize(self) -> dict:
    usb_serialized = self.io.serialize()
    heater_shaker_serialized = HeaterShakerBackend.serialize(self)
    return {
      **usb_serialized,
      **heater_shaker_serialized,
      "shaker_index": self.shaker_index,
    }

  async def _send_command(self, command: str, **kwargs):
    loop = asyncio.get_running_loop()
    assert len(command) == 2, "Command must be 2 characters long"
    args = "".join([f"{key}{value}" for key, value in kwargs.items()])
    command_str = f"T{self.shaker_index}{command}id{str(self.command_id).zfill(4)}{args}"

    def write_and_read():
      self.io.write(command_str.encode())
      self.command_id = (self.command_id + 1) % 10_000
      return self.io.read()

    response = await loop.run_in_executor(self._usb_executor, write_and_read)
    return response
  
  async def initialize_shaker_drive(self):
    """Initialize the shaker drive, homing to absolute position 0"""
    return await self._send_command("SI")

  async def shake(
    self,
    speed: float = 800,
    direction: Literal[0, 1] = 0,
    acceleration: int = 1_000,
  ):
    """
    speed: steps per second  
    direction: 0 for positive, 1 for negative  
    acceleration: increments per second
    """
    int_speed = int(speed)
    assert 20 <= int_speed <= 2_000, "Speed must be between 20 and 2_000"
    assert direction in [0, 1], "Direction must be 0 or 1"
    assert 500 <= acceleration <= 10_000, "Acceleration must be between 500 and 10_000"

    if await self.get_is_shaking():
      await self._start_shaking(direction=direction, speed=int_speed, acceleration=acceleration)
    else:
      while not await self.get_is_shaking():
        await self._start_shaking(direction=direction, speed=int_speed, acceleration=acceleration)

  async def stop_shaking(self):
    """Shaker `stop_shaking` implementation."""
    await self._stop_shaking()
    await self._wait_for_stop()
    
  async def get_is_shaking(self) -> bool:
    """Check if the shaker is shaking."""
    response = (await self._send_command("RD")).decode("ascii")
    return response.endswith("1")

  async def _move_plate_lock(self, position: PlateLockPosition):
    return await self._send_command("LP", lp=position.value)

  async def lock_plate(self):
    await self._move_plate_lock(PlateLockPosition.LOCKED)

  async def unlock_plate(self):
    await self._move_plate_lock(PlateLockPosition.UNLOCKED)

  async def _initialize_lock(self):
    """Firmware command to initialize lock."""
    result = await self._send_command("LI")
    return result

  async def _start_shaking(self, direction: int, speed: int, acceleration: int):
    """Firmware command for starting shaking."""
    speed_str = str(speed).zfill(4)
    acceleration_str = str(acceleration).zfill(5)
    return await self._send_command("SB", st=direction, sv=speed_str, sr=acceleration_str)

  async def _stop_shaking(self):
    """Firmware command for stopping shaking."""
    return await self._send_command("SC")

  async def _wait_for_stop(self):
    """Firmware command for waiting for shaking to stop."""
    return await self._send_command("SW")

  async def set_temperature(self, temperature: float):
    """Set temperature in Celsius."""
    temp_str = f"{round(10 * temperature):04d}"
    return await self._send_command("TA", ta=temp_str)

  async def get_current_temperature(self) -> float:
    """Get temperature in Celsius."""
    response = (await self._send_command("RT")).decode("ascii")
    temp = str(response).split(" ")[1].strip("+")
    return float(temp) / 10

  async def deactivate(self):
    """Turn off heating."""
    return await self._send_command("TO")
