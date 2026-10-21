import numpy as np
import pytest
from pyproj import Geod, Transformer

from wind3d_ninja.utils.wind_coordinates import earth_to_grid, grid_to_earth


@pytest.mark.parametrize("longitude", [100.0, 105.0, 110.0])
@pytest.mark.parametrize("direction", [0.0, 90.0, 180.0, 270.0])
def test_cardinal_winds_follow_independent_geodesic_displacement(longitude, direction):
    transform = Transformer.from_crs(4326, 32648, always_xy=True)
    x, y = transform.transform(longitude, 30.0)
    to_lon, to_lat, _ = Geod(ellps="WGS84").fwd(longitude, 30.0, direction + 180, 0.1)
    to_x, to_y = transform.transform(to_lon, to_lat)
    grid = np.array([to_x - x, to_y - y])
    grid *= 5 / np.linalg.norm(grid)
    expected = -5 * np.array([np.sin(np.deg2rad(direction)), np.cos(np.deg2rad(direction))])
    np.testing.assert_allclose(grid_to_earth(*grid, 32648, x, y), expected, atol=1e-7)
    np.testing.assert_allclose(earth_to_grid(*expected, 32648, x, y), grid, atol=1e-7)


def test_array_rotation_preserves_speed_mask_and_roundtrip():
    x, y = np.meshgrid([230000.0, 240000.0], [2760000.0, 2770000.0])
    u = np.array([[1.0, 2.0], [3.0, np.nan]])
    v = np.array([[4.0, 5.0], [6.0, np.nan]])
    east, north = grid_to_earth(u, v, 32648, x, y)
    np.testing.assert_allclose(np.hypot(east, north), np.hypot(u, v))
    grid_u, grid_v = earth_to_grid(east, north, 32648, x, y)
    np.testing.assert_allclose(grid_u, u)
    np.testing.assert_allclose(grid_v, v)


def test_kmz_arrow_geometry_matches_its_true_direction_label(tmp_path):
    from zipfile import ZipFile
    from xml.etree import ElementTree
    from pyproj import CRS
    from wind3d_ninja.pipeline.assimilation import _write_analysis_kmz

    x, y = Transformer.from_crs(4326, 32648, always_xy=True).transform(102.3716, 25.0256)
    path = tmp_path / "east.kmz"
    _write_analysis_kmz(path, np.array([x]), np.array([y]), np.array([[5.0]]),
                        np.array([[0.0]]), CRS.from_epsg(32648), 50)
    with ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("doc.kml"))
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    name = root.find(".//k:Placemark/k:name", ns).text
    points = root.find(".//k:coordinates", ns).text.split()
    start, tip = [list(map(float, point.split(",")[:2])) for point in points[:2]]
    bearing, _, _ = Geod(ellps="WGS84").inv(*start, *tip)
    assert "from 270.0 deg" in name
    assert bearing == pytest.approx(90, abs=0.001)
