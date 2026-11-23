"""The angle rings around the viewing cone: another cone angle from one run, without tracing again."""

import numpy as np
import pytest

from refract.engine import kernels, spectral, tracer
from refract.engine.scene import ring_edges_deg
from refract.results import DEFAULT_ANALYSIS, RunData
from test_map_noise import _plate_scene


def _run_data(scene, tally) -> RunData:
    """A one-channel RunData built straight from a tally (what simrun stores, without the files)."""
    det = scene.detectors[0]
    arrays = {k: np.asarray(getattr(tally, k))[None] for k in ("ex", "cone", "ex2", "cone2", "ring", "ring2", "dirh", "det_pow", "abs_vol", "abs_surf", "special")}
    arrays.update(w0=np.ones(1), n_rays=np.array([tally.n_rays]), accounted=np.ones(1), flux_lm=np.ones(1))
    arrays["area_map_0"] = det.area_map
    sc = {
        "detectors": [{"nu": det.nu, "nv": det.nv, "offset": det.offset, "cone_deg": det.cone_deg, "ring_deg": det.ring_deg.tolist(), "ext": det.ext.tolist()}],
        "channels": [{"key": "c", "led_id": "l", "led_name": "l", "flux_w": 1.0, "flux_lm": 1.0}],
        "states": [{"id": "s", "name": "s", "drive": {"c": 1.0}}],
        "bodies": [],
        "warnings": [],
    }
    return RunData("r", "x", sc, arrays, {})


def test_ring_edges():
    assert ring_edges_deg(30.0).tolist() == [15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0]
    e = ring_edges_deg(6.0)  # a small cone: narrower rings so that the inner edge stays above 0
    assert e[3] == 6.0 and e[0] == pytest.approx(0.0) and np.all(np.diff(e) > 0)
    e = ring_edges_deg(90.0)  # nothing outside a 90 degree cone
    assert e[3] == 90.0 and e[-1] == 90.0 and e[0] == 75.0


def test_rings_rebuild_the_cone_of_another_angle_exactly():
    """Rays depend on their number only: a run traced with cone 30 and rebuilt at 35 / 25 is the run traced with that cone."""
    n, seed = 120_000, 7
    scene, src = _plate_scene(res=24, cone=30)
    t30 = tracer.trace_channel(scene, src, n, seed=seed)
    data = _run_data(scene, t30)
    comb = data.combine("s")
    det = scene.detectors[0]
    sl = slice(det.offset, det.offset + det.nu * det.nv)
    for theta in (35.0, 25.0, 40.0, 20.0):
        s2, src2 = _plate_scene(res=24, cone=theta)
        ref = tracer.trace_channel(s2, src2, n, seed=seed)
        xyz, var, eff = data.cone_at(comb, 0, theta)
        assert eff == theta
        y_ref = ref.cone[sl, 1] * spectral.KM
        assert xyz[:, 1] == pytest.approx(y_ref, rel=1e-9, abs=1e-9 * y_ref.max())
        assert var == pytest.approx(ref.cone2[sl] * spectral.KM**2, rel=1e-9, abs=1e-9 * (ref.cone2[sl] * spectral.KM**2).max())
        assert xyz[:, 1].sum() > 0


def test_cone_at_the_traced_angle_is_the_traced_cone_and_the_range_is_limited():
    scene, src = _plate_scene(res=16, cone=30)
    t = tracer.trace_channel(scene, src, 50_000, seed=3)
    data = _run_data(scene, t)
    comb = data.combine("s")
    xyz, var, eff = data.cone_at(comb, 0, None)
    assert eff == 30.0 and np.array_equal(xyz, comb["cone"][: len(xyz)])
    assert data.cone_at(comb, 0, 30.0)[2] == 30.0
    assert data.cone_range(data.scene["detectors"][0]) == [15.0, 45.0]
    assert data.cone_at(comb, 0, 80.0)[2] == 45.0  # beyond the rings: the largest angle there is
    assert data.cone_at(comb, 0, 2.0)[2] == 15.0
    # the maps follow: the same angle gives the default maps, another angle changes the luminance and says which angle it used
    base = data.detector_maps(comb, 0, DEFAULT_ANALYSIS)
    same = data.detector_maps(comb, 0, {**DEFAULT_ANALYSIS, "cone_deg": 30.0})
    assert np.array_equal(base["lum"], same["lum"]) and base["coneDeg"] == 30.0
    other = data.detector_maps(comb, 0, {**DEFAULT_ANALYSIS, "cone_deg": 40.0})
    assert other["coneDeg"] == 40.0 and not np.allclose(other["lum"], base["lum"])
    assert other["noise"] is not None and np.isfinite(other["noise"]).all()


def test_rings_cover_the_exit_light_up_to_90_degrees():
    scene, src = _plate_scene(res=16, cone=75)  # rings reach 90 degrees: cone + the outer rings is everything that left forwards
    t = tracer.trace_channel(scene, src, 80_000, seed=5)
    det = scene.detectors[0]
    sl = slice(det.offset, det.offset + det.nu * det.nv)
    assert t.ring.shape == (scene.total_pix, kernels.NRING)
    total = t.cone[sl, 1].sum() + t.ring[sl, 3:].sum()
    assert total == pytest.approx(t.ex[sl, 1].sum(), rel=1e-6)
    # inner rings lie inside the cone: they cannot hold more than it does
    assert t.ring[sl, :3].sum() < t.cone[sl, 1].sum()
    assert (t.ring2[sl] >= 0).all() and t.ring2[sl].sum() > 0
