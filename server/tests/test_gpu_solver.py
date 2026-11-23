"""The GPU solver (Warp / CUDA) against the analytic physics tests and against the CPU solver. Skipped without a CUDA device."""

import numpy as np
import pytest

import test_engine_physics as P
from refract.engine import solvers, tracer
from test_map_noise import _plate_scene

_ok, _why = solvers.gpu_status()
pytestmark = pytest.mark.skipif(not _ok, reason=f"no usable GPU solver: {_why}")


@pytest.fixture
def on_gpu(monkeypatch):
    """Every ``tracer.trace_channel`` call of the physics tests is served by the GPU solver."""
    from refract.engine import gpu_tracer

    monkeypatch.setattr(P.tracer, "trace_channel", gpu_tracer.trace_channel)


@pytest.mark.parametrize(
    "name",
    [
        "test_fresnel_slab_transmission",
        "test_beer_lambert_absorption",
        "test_lambertian_point_source_irradiance",
        "test_total_internal_reflection_prism",
        "test_no_total_internal_reflection_below_critical_index",
        "test_ballistic_fraction_of_scattering_slab",
        "test_many_volume_scatters_do_not_hit_the_surface_bounce_limit",
    ],
)
def test_analytic_physics_on_the_gpu(on_gpu, name):
    getattr(P, name)()


@pytest.mark.parametrize("mus,g,mua,thickness", [(1.0, 0.0, 0.05, 3.0), (2.0, 0.6, 0.1, 2.0), (0.5, 0.85, 0.02, 4.0)])
def test_volume_scattering_on_the_gpu(on_gpu, mus, g, mua, thickness):
    P.test_volume_scattering_matches_reference_monte_carlo(mus, g, mua, thickness)


def test_gpu_and_cpu_agree_on_the_exit_map():
    """Independent random streams (different seeds): totals agree within noise and the per-pixel difference is statistical."""
    from refract.engine import gpu_tracer

    scene, src = _plate_scene(res=24)
    det = scene.detectors[0]
    sl = slice(det.offset, det.offset + det.nu * det.nv)
    n = 400_000
    g = gpu_tracer.trace_channel(scene, src, n, seed=101)
    c = tracer.trace_channel(scene, src, n, seed=202)
    assert g.total_accounted() == pytest.approx(n, rel=1e-6)
    assert g.det_pow[0] / n == pytest.approx(c.det_pow[0] / n, rel=0.01)
    assert g.ex[sl, 1].sum() == pytest.approx(c.ex[sl, 1].sum(), rel=0.01)
    assert g.cone[sl, 1].sum() == pytest.approx(c.cone[sl, 1].sum(), rel=0.02)
    assert g.dirh.sum() == pytest.approx(c.dirh.sum(), rel=0.01)
    var = g.cone2[sl] + c.cone2[sl]
    ok = (var > 0) & (g.cone[sl, 1] > 0) & (c.cone[sl, 1] > 0)
    z = (g.cone[sl, 1][ok] - c.cone[sl, 1][ok]) / np.sqrt(var[ok])
    assert ok.sum() > 200
    assert abs(float(z.mean())) < 0.2
    assert 0.8 < float(np.sqrt((z * z).mean())) < 1.2  # 1 for independent unbiased estimates of the same map


def test_gpu_angle_rings_agree_with_the_cpu_solver():
    """The rings around the viewing cone (another cone angle without tracing again) from the GPU against the CPU solver."""
    from refract.engine import gpu_tracer, kernels

    scene, src = _plate_scene(res=24, cone=30)
    det = scene.detectors[0]
    sl = slice(det.offset, det.offset + det.nu * det.nv)
    n = 400_000
    g = gpu_tracer.trace_channel(scene, src, n, seed=101)
    c = tracer.trace_channel(scene, src, n, seed=202)
    assert g.ring.shape == (scene.total_pix, kernels.NRING) and g.ring2.shape == g.ring.shape
    for r in range(kernels.NRING):
        assert g.ring[sl, r].sum() == pytest.approx(c.ring[sl, r].sum(), rel=0.03), r
    assert g.ring2[sl].sum() == pytest.approx(c.ring2[sl].sum(), rel=0.1)
    # the rings and the cone tile the exit light the same way on both solvers
    assert (g.cone[sl, 1].sum() + g.ring[sl, 3:].sum()) / g.ex[sl, 1].sum() == pytest.approx((c.cone[sl, 1].sum() + c.ring[sl, 3:].sum()) / c.ex[sl, 1].sum(), rel=0.01)


def test_gpu_noise_falls_as_one_over_sqrt_of_the_rays():
    from refract.engine import gpu_tracer

    scene, src = _plate_scene(res=24)
    det = scene.detectors[0]
    sl = slice(det.offset, det.offset + det.nu * det.nv)

    def noise(t):
        y, v = t.cone[sl, 1], t.cone2[sl]
        ok = y > 0
        return float(np.median(np.sqrt(v[ok]) / y[ok]))

    a = noise(gpu_tracer.trace_channel(scene, src, 100_000, seed=5))
    b = noise(gpu_tracer.trace_channel(scene, src, 400_000, seed=5))
    assert b == pytest.approx(a / 2, rel=0.1)


def test_gpu_passes_continue_one_stream():
    """Tracing rays 0..N and then N..2N gives the same totals as one pass over 0..2N (rays depend on their number only)."""
    from refract.engine import gpu_tracer

    scene, src = _plate_scene(res=16)
    one = gpu_tracer.trace_channel(scene, src, 200_000, seed=9)
    a = gpu_tracer.trace_channel(scene, src, 100_000, seed=9)
    b = gpu_tracer.trace_channel(scene, src, 100_000, seed=9, start=100_000)
    assert (a.det_pow + b.det_pow)[0] == pytest.approx(one.det_pow[0], rel=1e-6)
    assert (a.ex + b.ex).sum() == pytest.approx(one.ex.sum(), rel=1e-5)


def test_gpu_serves_preview_paths():
    from refract.engine import gpu_tracer

    scene, src = _plate_scene(res=16)
    t = gpu_tracer.trace_channel(scene, src, 20_000, seed=3, record_paths=4)
    assert t.paths is not None and int(t.path_n[0]) >= 2


def _analysis_scene():
    import test_analysis as A

    return A._scene()


def test_gpu_analysis_does_not_change_the_physics():
    from refract.engine import gpu_tracer

    scene, src = _analysis_scene()
    plain = gpu_tracer.trace_channel(scene, src, 100_000, seed=5)
    tagged = gpu_tracer.trace_channel(scene, src, 100_000, seed=5, analysis=True)
    assert tagged.analysis is not None
    # the two kernels are compiled separately, so float32 rounding may differ in the last bits of a few pixels\n    assert plain.ex.sum() == pytest.approx(tagged.ex.sum(), rel=1e-6)\n    assert np.allclose(plain.ex, tagged.ex, rtol=1e-3, atol=1e-4 * float(plain.ex.max()))
    assert np.allclose(plain.det_pow, tagged.det_pow, rtol=1e-6)


def test_gpu_analysis_agrees_with_the_cpu_analysis():
    """Independent random streams: the face tables, the coarse group maps and the logs agree within noise."""
    from refract.engine import gpu_tracer

    scene, src = _analysis_scene()
    n = 400_000
    g = gpu_tracer.trace_channel(scene, src, n, seed=11, analysis=True, log_rays=n)
    c = tracer.trace_channel(scene, src, n, seed=12, analysis=True, log_rays=n)
    ga, ca = g.analysis, c.analysis
    assert ga.face_t.shape == ca.face_t.shape and ga.grp_map.shape == ca.grp_map.shape
    for col in range(4):
        assert ga.face_t[:, col].sum() == pytest.approx(ca.face_t[:, col].sum(), rel=0.02), col
    assert ga.tot_map.sum() == pytest.approx(ca.tot_map.sum(), rel=0.02)
    assert ga.grp_map.sum() == pytest.approx(ga.tot_map.sum(), rel=1e-6)  # every deposit belongs to exactly one "last group"
    assert ga.touch_map.sum() == pytest.approx(ca.touch_map.sum(), rel=0.03)
    assert ga.face_from.sum() == pytest.approx(ca.face_from.sum(), rel=0.03)
    assert ga.face_to.sum() == pytest.approx(ca.face_to.sum(), rel=0.03)
    # one log row per ray, with the same mix of outcomes
    assert len(ga.end["rid"]) == n and np.array_equal(np.sort(ga.end["rid"]), np.arange(n))
    og, oc = np.bincount(ga.end["out"], minlength=8) / n, np.bincount(ca.end["out"], minlength=8) / n
    assert np.allclose(og, oc, atol=0.01)
    assert len(ga.dep["rid"]) == pytest.approx(len(ca.dep["rid"]), rel=0.03)
    assert ga.dep["wy"].sum() == pytest.approx(ca.dep["wy"].sum(), rel=0.02)


def test_gpu_analysis_log_stops_at_log_rays_but_tables_do_not():
    from refract.engine import gpu_tracer

    scene, src = _analysis_scene()
    a = gpu_tracer.trace_channel(scene, src, 60_000, seed=2, analysis=True, log_rays=60_000).analysis
    b = gpu_tracer.trace_channel(scene, src, 60_000, seed=2, analysis=True, log_rays=10_000).analysis
    assert len(b.end["rid"]) == 10_000 and int(b.dep["rid"].max()) < 10_000
    assert np.allclose(a.face_t, b.face_t) and np.allclose(a.grp_map, b.grp_map)  # the tables take every ray


def test_gpu_replay_reproduces_the_logged_rays():
    from refract.engine import gpu_tracer, kernels, replay

    scene, src = _analysis_scene()
    n = 100_000
    t = gpu_tracer.trace_channel(scene, src, n, seed=5, analysis=True, log_rays=n)
    dep = t.analysis.dep
    ids = np.unique(dep["rid"])[:60]
    out = replay.replay(scene, src, 5, ids, solver="gpu")
    by_id = {r["id"]: r for r in replay.replay(scene, src, 5, ids[::-1], solver="gpu")}
    cnt = np.bincount(dep["rid"], minlength=n)
    for r in out:
        assert r["points"] == by_id[r["id"]]["points"]
        assert r["event"].count(kernels.EV_EXIT) == cnt[r["id"]]
        assert r["event"][0] == kernels.EV_START and len(r["points"]) >= 2


def test_gpu_passes_merge_their_analysis():
    from refract.engine import gpu_tracer
    from refract import simrun

    scene, src = _analysis_scene()
    one = gpu_tracer.trace_channel(scene, src, 80_000, seed=4, analysis=True, log_rays=80_000).analysis
    a = gpu_tracer.trace_channel(scene, src, 40_000, seed=4, analysis=True, log_rays=80_000).analysis
    b = gpu_tracer.trace_channel(scene, src, 40_000, seed=4, start=40_000, analysis=True, log_rays=80_000).analysis
    simrun._merge_analysis(a, b)
    assert np.allclose(a.face_t, one.face_t, rtol=1e-5, atol=1e-9)
    assert np.allclose(a.grp_map, one.grp_map, rtol=1e-5, atol=1e-9)
    assert len(a.end["rid"]) == len(one.end["rid"]) == 80_000

def _receiver_scene(**kw):
    import test_receiver as R
    from refract.engine import sources
    from refract.engine.scene import build_scene

    b, setup = R._table(**kw)
    opt, doc = b.finish()
    scene = build_scene(opt, doc, setup, P.library_with())
    src = sources.build_channel_sources(setup, doc, scene, opt, P.library_with())[0]
    return scene, src


@pytest.mark.parametrize("finish", ["white_diffuse", "mirror"])
def test_gpu_and_cpu_agree_on_a_receiver(finish):
    """The receiver tallies (incident map, reflected cone and rings about the view direction) from the GPU against the CPU solver."""
    from refract.engine import gpu_tracer, kernels

    scene, src = _receiver_scene(finish=finish, el=60.0, cone=25.0, res=24)
    det = scene.detectors[0]
    sl = slice(det.offset, det.offset + det.nu * det.nv)
    n = 400_000
    g = gpu_tracer.trace_channel(scene, src, n, seed=101)
    c = tracer.trace_channel(scene, src, n, seed=202)
    assert g.total_accounted() == pytest.approx(n, rel=1e-6)
    assert g.det_pow[0] == 0.0 and g.rcv_pow[0] > 0
    assert g.rcv_pow[0] == pytest.approx(c.rcv_pow[0], rel=0.01)
    assert g.ex[sl, 1].sum() == pytest.approx(c.ex[sl, 1].sum(), rel=0.01)
    assert g.cone[sl, 1].sum() == pytest.approx(c.cone[sl, 1].sum(), rel=0.03)
    for r in range(kernels.NRING):
        assert g.ring[sl, r].sum() == pytest.approx(c.ring[sl, r].sum(), rel=0.05, abs=0.002 * c.ex[sl, 1].sum()), r
    assert g.dirh.sum() == pytest.approx(c.dirh.sum(), rel=0.01)


def test_gpu_analysis_of_a_receiver():
    """Incident light of a receiver is a deposit of the analysis: the maps, the logs and the replayed paths all count it, on both solvers."""
    from refract.engine import gpu_tracer, kernels, replay

    scene, src = _receiver_scene(el=45.0, cone=20.0, res=16)
    n = 100_000
    g = gpu_tracer.trace_channel(scene, src, n, seed=7, analysis=True, log_rays=n)
    c = tracer.trace_channel(scene, src, n, seed=8, analysis=True, log_rays=n)
    ga, ca = g.analysis, c.analysis
    for view in (0, 1):
        assert ga.tot_map[view].sum() == pytest.approx(ca.tot_map[view].sum(), rel=0.03), view
    assert ga.tot_map[0].sum() == pytest.approx(g.ex[:, 1].sum(), rel=1e-4)  # the all-directions map is the incident map
    assert ga.tot_map[1].sum() == pytest.approx(g.cone[:, 1].sum(), rel=1e-4)  # ... and the cone map is the reflected cone
    assert len(ga.dep["rid"]) == pytest.approx(len(ca.dep["rid"]), rel=0.02)
    dep = ga.dep
    ids = np.unique(dep["rid"])[:40]
    cnt = np.bincount(dep["rid"], minlength=n)
    for r in replay.replay(scene, src, 7, ids, solver="gpu"):
        assert r["event"].count(kernels.EV_EXIT) == cnt[r["id"]]


def test_gpu_and_cpu_agree_on_a_pass_through_exit():
    """An exit surface that lets the light go on: its own map, the light that reaches the table behind it, and the analysis log."""
    import test_receiver as R
    from refract.engine import gpu_tracer, sources
    from refract.engine.scene import build_scene

    lib_ = P.library_with(glass={"n": 1.5, "absorption": 0.0})
    b, setup = R._plate_over_table(True)
    opt, doc = b.finish()
    scene = build_scene(opt, doc, setup, lib_)
    src = sources.build_channel_sources(setup, doc, scene, opt, lib_)[0]
    n = 400_000
    g = gpu_tracer.trace_channel(scene, src, n, seed=31, analysis=True, log_rays=n)
    c = tracer.trace_channel(scene, src, n, seed=32, analysis=True, log_rays=n)
    assert g.det_pow.sum() == 0.0 and g.total_accounted() == pytest.approx(n, rel=1e-6)
    assert g.rcv_pow[0] == pytest.approx(c.rcv_pow[0], rel=0.01)
    assert g.rcv_pow[1] == pytest.approx(c.rcv_pow[1], rel=0.02)
    d0, d1 = scene.detectors
    s0, s1 = slice(d0.offset, d0.offset + d0.nu * d0.nv), slice(d1.offset, d1.offset + d1.nu * d1.nv)
    assert g.ex[s0, 1].sum() == pytest.approx(c.ex[s0, 1].sum(), rel=0.01)
    assert g.ex[s1, 1].sum() == pytest.approx(c.ex[s1, 1].sum(), rel=0.02)
    assert g.cone[s1, 1].sum() == pytest.approx(c.cone[s1, 1].sum(), rel=0.03)
    assert len(g.analysis.dep["rid"]) == pytest.approx(len(c.analysis.dep["rid"]), rel=0.02)
    assert g.analysis.tot_map[0].sum() == pytest.approx(g.ex[:, 1].sum(), rel=1e-4)
