import json
import math


def point_in_rect(x: float, z: float, x1: float, z1: float, x2: float, z2: float) -> bool:
    min_x, max_x = min(x1, x2), max(x1, x2)
    min_z, max_z = min(z1, z2), max(z1, z2)
    return min_x <= x <= max_x and min_z <= z <= max_z


def point_in_polygon(x: float, z: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, zi = polygon[i]
        xj, zj = polygon[j]
        if ((zi > z) != (zj > z)) and (
            x < (xj - xi) * (z - zi) / (zj - zi) + xi
        ):
            inside = not inside
        j = i
    return inside


def is_inside(x: float, z: float, shape_type: str, coordinates: str) -> bool:
    coords = json.loads(coordinates)
    if shape_type == "rectangle":
        return point_in_rect(x, z, coords[0], coords[1], coords[2], coords[3])
    if shape_type == "polygon":
        return point_in_polygon(x, z, [(p[0], p[1]) for p in coords])
    return False


def coords_to_polygon(
    shape_type: str, coordinates: str
) -> list[tuple[float, float]]:
    coords = json.loads(coordinates)
    if shape_type == "rectangle":
        x1, z1, x2, z2 = coords[0], coords[1], coords[2], coords[3]
        return [(x1, z1), (x1, z2), (x2, z2), (x2, z1)]
    return [(p[0], p[1]) for p in coords]


def distance_to_segment(
    px: float, pz: float,
    x1: float, z1: float,
    x2: float, z2: float,
) -> float:
    """Shortest distance from point (px, pz) to line segment (x1,z1)-(x2,z2)."""
    dx, dz = x2 - x1, z2 - z1
    seg_len_sq = dx * dx + dz * dz
    if seg_len_sq == 0:
        return math.sqrt((px - x1) ** 2 + (pz - z1) ** 2)
    t = max(0.0, min(1.0, ((px - x1) * dx + (pz - z1) * dz) / seg_len_sq))
    cx, cz = x1 + t * dx, z1 + t * dz
    return math.sqrt((px - cx) ** 2 + (pz - cz) ** 2)


def is_near_polygon(
    px: float, pz: float,
    polygon: list[tuple[float, float]],
    radius: float,
) -> bool:
    n = len(polygon)
    if n < 2:
        return False
    for i in range(n):
        x1, z1 = polygon[i]
        x2, z2 = polygon[(i + 1) % n]
        if distance_to_segment(px, pz, x1, z1, x2, z2) <= radius:
            return True
    return False


def is_near_territory(
    x: float, z: float,
    shape_type: str,
    coordinates: str,
    radius: int,
) -> bool:
    """Check if point is within `radius` blocks of the territory border.

    NOTE: caller must ensure the point is OUTSIDE the territory first.
    """
    if radius <= 0:
        return False
    poly = coords_to_polygon(shape_type, coordinates)
    return is_near_polygon(x, z, poly, float(radius))
