"""The camera of the live photoreal picture: it must frame the scene like the 3D view does."""

import numpy as np
import pytest

mi = pytest.importorskip("mitsuba")


@pytest.fixture(autouse=True)
def _scalar_variant():
    try:
        mi.set_variant("scalar_rgb")
    except Exception:  # pragma: no cover - a build without the scalar variant
        pytest.skip("Mitsuba has no scalar_rgb variant")


CAM = {"position": [0.0, -10.0, 0.0], "target": [0.0, 0.0, 0.0], "up": [0.0, 0.0, 1.0]}


def _ray(sensor, u, v):
    ray, _ = sensor.sample_ray(0.0, 0.5, [u, v], [0.5, 0.5])
    return np.array(ray.o, dtype=float), np.array(ray.d, dtype=float)


@pytest.mark.parametrize("size", [(200, 100), (100, 200), (100, 100)])
def test_orthographic_view_has_the_requested_half_height(size):
    from refract.render import live_view

    w, h = size
    s = mi.load_dict(live_view.sensor_dict(mi, {**CAM, "orthoHalfH": 2.0}, w, h))
    top, bottom = _ray(s, 0.5, 0.0)[0], _ray(s, 0.5, 1.0)[0]
    right = _ray(s, 1.0, 0.5)[0]
    assert top[2] == pytest.approx(2.0, abs=1e-3) and bottom[2] == pytest.approx(-2.0, abs=1e-3)
    assert right[0] == pytest.approx(2.0 * w / h, abs=1e-3)  # the same scale across as up: no stretching


def test_orthographic_origin_is_behind_the_camera_position():
    """The 3D view's orthographic camera shows what is behind its position: the Mitsuba origin lies further back."""
    from refract.render import live_view

    d = live_view.sensor_dict(mi, {**CAM, "orthoHalfH": 1.0}, 100, 100)
    o, direction = _ray(mi.load_dict(d), 0.5, 0.5)
    assert o[1] < CAM["position"][1] and direction[1] == pytest.approx(1.0, abs=1e-5)
    assert d["far_clip"] > 10.0 + 1.0  # still reaches the far side of the target


@pytest.mark.parametrize("size", [(200, 100), (100, 200)])
def test_perspective_fov_is_vertical_and_not_mirrored(size):
    from refract.render import live_view

    w, h = size
    s = mi.load_dict(live_view.sensor_dict(mi, {**CAM, "fov": 40.0}, w, h))
    _, up = _ray(s, 0.5, 0.0)
    assert np.degrees(np.arctan2(up[2], up[1])) == pytest.approx(20.0, abs=1e-3)  # half of the vertical angle, image top is up
    _, right = _ray(s, 0.9, 0.5)
    assert right[0] > 0  # the right side of the picture is the world's +x here (looking along +y with z up)
