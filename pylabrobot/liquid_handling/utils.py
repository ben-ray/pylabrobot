from typing import List

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource


def _get_centers_with_margin(dim_size: float, n: int, margin: float, min_spacing: float):
  """Get the centers of the channels with a minimum margin on the edges."""
  if dim_size < margin * 2 + (n - 1) * min_spacing:
    raise ValueError("Resource is too small to space channels.")
  if dim_size - (n - 1) * min_spacing <= min_spacing * 2:
    remaining_space = dim_size - (n - 1) * min_spacing - margin * 2
    return [margin + remaining_space / 2 + i * min_spacing for i in range(n)]
  return [(i + 1) * dim_size / (n + 1) for i in range(n)]


def get_wide_single_resource_liquid_op_offsets(
  resource: Resource, num_channels: int
) -> List[Coordinate]:
  min_spacing_edge = (
    2  # minimum spacing between the edge of the container and the center of channel
  )
  min_spacing_between_channels = 9

  resource_size: float
  if resource.get_absolute_rotation().z % 180 == 0:
    resource_size = resource.get_size_y()
  elif resource.get_absolute_rotation().z % 90 == 0:
    resource_size = resource.get_size_x()
  else:
    raise ValueError("Only 90 and 180 degree rotations are supported for now.")

  centers = list(
    reversed(
      _get_centers_with_margin(
        dim_size=resource_size,
        n=num_channels,
        margin=min_spacing_edge,
        min_spacing=min_spacing_between_channels,
      )
    )
  )  # reverse because channels are from back to front

  center_offsets: List[Coordinate] = []
  if resource.get_absolute_rotation().z % 180 == 0:
    x_offset = resource.get_size_x() / 2
    center_offsets = [Coordinate(x=x_offset, y=c, z=0) for c in centers]
  elif resource.get_absolute_rotation().z % 90 == 0:
    y_offset = resource.get_size_y() / 2
    center_offsets = [Coordinate(x=c, y=y_offset, z=0) for c in centers]

  # offsets are relative to the center of the resource, but above we computed them wrt lfb
  # so we need to subtract the center of the resource
  return [c - resource.center() for c in center_offsets]


def get_tight_single_resource_liquid_op_offsets(
  resource: Resource, num_channels: int
) -> List[Coordinate]:
  min_spacing_between_channels = 9
  min_spacing_edge = (
    2  # minimum spacing between the edge of the container and the center of channel
  )

  channel_space = (num_channels - 1) * min_spacing_between_channels
  print("num_channels", num_channels)
  print("channel_space", channel_space)
  if resource.get_absolute_rotation().z % 180 == 0:
    min_y = (resource.get_size_y() - channel_space) / 2
    print("size_y", resource.get_size_y())
    print("min_y", min_y)
    print("min_spacing_edge", min_spacing_edge)
    if min_y < min_spacing_edge:
      raise ValueError("Resource is too small to space channels.")
    offsets = [
      Coordinate(0, min_y + i * min_spacing_between_channels, 0) for i in range(num_channels)
    ][::-1]
  elif resource.get_absolute_rotation().z % 90 == 0:
    min_x = (resource.get_size_x() - channel_space) / 2
    min_x = -14
    print("size_x", resource.get_size_x())
    print("min_x", min_x)
    print("min_spacing_edge", min_spacing_edge)
    print("min_spacing_between_channels", min_spacing_between_channels)
    offsets = [
      Coordinate(0, min_x + i * min_spacing_between_channels, 0) for i in range(num_channels)
    ][::-1]
    print("offsets", offsets)
  else:
    raise ValueError("Only 90 and 180 degree rotations are supported for now.")

  # offsets are relative to the center of the resource, but above we computed them wrt lfb
  # so we need to subtract the center of the resource
  print([o - resource.center() for o in offsets])
  return [o - resource.center() for o in offsets]
