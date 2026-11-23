"""Exit-map accuracy: exact pixel areas and the per-pixel noise estimate."""

import numpy as np
import pytest

from refract.engine import pixel_area, sources, tracer
from refract.engine.scene import build_scene
from refract.results import RunData  # noqa: F401  (import check: the module loads with the new fields)
from test_engine_physics import Builder, detector, led, library_with


def _plate_scene(res=40, cone=90):
    b = Builder()
    b.add_box("plate", (-60, -60, 10), (60, 60, 13))
    setup = {
        "parts": [{"match": "*", "finish": "black"}, {"match": "plate", "material": "glass", "finish": "polished"}],
        "leds": [led((0, 0, 0), "lamb")],
        "detectors": [detector("plate", 5, res=res, cone=cone)],
    }
    library = library_with(glass={"n": 1.5, "absorption": 0.0})
    opt, doc = b.finish()
    scene = build_scene(opt, doc, setup, library)
    srcs = sources.build_channel_sources(setup, doc, scene, opt, library)
    return scene, srcs[0]


def test_pixel_area_of_a_triangle_is_exact():
    # right triangle with legs of 2 pixels: 1 in the corner pixel, 1/2 in two neighbours, nothing beyond the hypotenuse
    xy = np.array([[[0.0, 0.0], [2.0, 0.0], [0.0, 2.0]]])
    a = pixel_area.exact_pixel_areas(xy, np.array([2.0]), 4, 3)
    assert a[0, 0] == pytest.approx(1.0)
    assert a[0, 1] == pytest.approx(0.5)
    assert a[1, 0] == pytest.approx(0.5)
    assert a[1, 1] == pytest.approx(0.0, abs=1e-12)
    assert a.sum() == pytest.approx(2.0)
    # the 3D area is what is shared out, whatever the projected size
    b = pixel_area.exact_pixel_areas(xy, np.array([8.0]), 4, 3)
    assert b.sum() == pytest.approx(8.0)
    assert b[0, 1] == pytest.approx(2.0)


def test_flat_exit_face_has_equal_pixel_areas():
    scene, _ = _plate_scene()
    det = scene.detectors[0]
    a = det.area_map
    assert a.sum() == pytest.approx(det.area_total, rel=1e-9)
    # (the map axes need not follow the sides of the face: the pixels that lie wholly on the face are the ones near the maximum)
    full = a[a > (1 - 1e-9) * a.max()]
    assert len(full) > 100
    assert full.std() / full.mean() < 1e-9  # random sampling gave about 10 % here
    assert full.mean() == pytest.approx((det.ext[1] - det.ext[0]) / det.nu * (det.ext[3] - det.ext[2]) / det.nv, rel=1e-9)
    assert a.max() <= full.mean() * (1 + 1e-9)


def test_map_noise_estimate_scales_and_matches_repeats():
    scene, src = _plate_scene()
    det = scene.detectors[0]
    sl = slice(det.offset, det.offset + det.nu * det.nv)
    ny, nx = det.nv, det.nu
    centre = (slice(ny // 2 - 6, ny // 2 + 6), slice(nx // 2 - 6, nx // 2 + 6))

    def rel_noise(t):
        y = t.cone[sl, 1].reshape(ny, nx)
        v = t.cone2[sl].reshape(ny, nx)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.sqrt(v) / y, y

    n1, n2 = 100_000, 400_000
    t1 = tracer.trace_channel(scene, src, n1, seed=11)
    t2 = tracer.trace_channel(scene, src, n2, seed=11)
    r1, _ = rel_noise(t1)
    r2, _ = rel_noise(t2)
    # four times the rays: half the noise
    assert np.median(r1[centre]) / np.median(r2[centre]) == pytest.approx(2.0, rel=0.1)

    # the estimate agrees with how much repeated runs of the same size really scatter (independent seeds, pixel by pixel)
    runs = [rel_noise(tracer.trace_channel(scene, src, 60_000, seed=s)) for s in (101, 20_003, 3_000_017, 40_000_031, 500_000_009, 6_000_000_011)]
    ys = np.stack([y[centre] for _, y in runs])
    est = np.sqrt(np.mean([r[centre] ** 2 for r, _ in runs]))
    seen = np.sqrt(np.mean(ys.var(axis=0, ddof=1) / ys.mean(axis=0) ** 2))
    assert seen == pytest.approx(est, rel=0.12)