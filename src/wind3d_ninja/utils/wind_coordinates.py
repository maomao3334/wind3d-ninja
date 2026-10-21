from __future__ import annotations

import numpy as np
from pyproj import CRS, Proj, Transformer


def grid_north_bearing(crs, x, y):
    """Clockwise bearing of projected grid north relative to true north."""
    reference = CRS.from_user_input(crs)
    x, y = np.broadcast_arrays(np.asarray(x, dtype=float), np.asarray(y, dtype=float))
    if reference.is_geographic:
        return np.zeros_like(x)
    longitude, latitude = Transformer.from_crs(reference, 4326, always_xy=True).transform(x, y)
    return np.asarray(Proj(reference).get_factors(longitude, latitude, errcheck=True).meridian_convergence)


def grid_to_earth(u, v, crs, x, y):
    """Rotate projected x/y wind to true east/north without changing speed."""
    angle = np.deg2rad(grid_north_bearing(crs, x, y))
    cosine, sine = np.cos(angle), np.sin(angle)
    u, v = np.asarray(u), np.asarray(v)
    return u * cosine + v * sine, v * cosine - u * sine


def earth_to_grid(u, v, crs, x, y):
    """Rotate true east/north station wind into the native projected mesh."""
    angle = np.deg2rad(grid_north_bearing(crs, x, y))
    cosine, sine = np.cos(angle), np.sin(angle)
    u, v = np.asarray(u), np.asarray(v)
    return u * cosine - v * sine, v * cosine + u * sine
