"""Receiver detectors: light that arrives on a surface (for example a table lit by the light pipe) and what it reflects to a viewer.

A Lambertian point source hangs above a large diffuse table, so every number has a closed form:
- the share of the flux that lands on the table is r^2 / (r^2 + H^2) for a table of radius r (here a square, so we count the hits);
- a Lambertian table of reflectance rho under illuminance E has luminance E rho / pi in every direction, so the flux that it sends into a
  cone of half angle a about a direction t away from the normal is  rho * E * A * sin^2(a) * cos(t).
"""

import math

import numpy as np
import pytest

from refract.engine import spectral, tracer
from refract.engine.scene import build_scene
from test_engine_physics import Builder, detector, led, library_with, run

H = 20.0  # height of the source above the table top
RHO = 0.9  # reflectance of finish "white_diffuse"


def _table(finish="white_diffuse", el=90.0, az=0.0, cone=20.0, kind="receiver", res=40):
    b = Builder()
    b.add_box("table", (-60, -60, -2), (60, 60, 0))  # face 5 is the top (z = 0)
    det = detector("table", 5, "t", res=res, cone=cone)
    if kind == "receiver":
        det.update({"type": "receiver", "view_el": el, "view_az": az})
    setup = {
        "parts": [{"match": "*", "finish": "black"}, {"match": "table", "finish": finish}],
        "leds": [led((0, 0, H), "lamb", axis=(0, 0, -1))],
        "detectors": [det],
    }
    return b, setup


def test_incident_light_is_counted_and_the_rays_carry_on():
    b, setup = _table()
    scene, src, t = run(b, setup, library_with(), n=200_000)  # (run() also checks that the energy budget closes)
    det = scene.detectors[0]
    assert det.kind == "receiver"
    # every ray of the hemisphere below the source lands on the table plane; the part of it that hits the 120 mm square is
    # r^2 / (r^2 + H^2) for the inscribed disc (r = 60) and a bit more for the corners
    assert t.rcv_pow[0] / t.n_rays == pytest.approx(0.9, abs=0.05)
    assert t.det_pow[0] == 0.0, "a receiver does not consume light: nothing goes into the exit-surface counter"
    # the table absorbs 10 % of what arrives, the rest leaves upwards and escapes
    assert t.abs_surf[0] / t.n_rays == pytest.approx(0.1 * t.rcv_pow[0] / t.n_rays, rel=0.01)
    y_in = t.ex[det.offset : det.offset + det.nu * det.nv, 1].sum()
    total_y = t.n_rays * float(src.spec @ spectral.CMF[:, 1])
    assert y_in / total_y == pytest.approx(t.rcv_pow[0] / t.n_rays, rel=0.02)


def test_incident_illuminance_profile():
    """Fraction of the flux inside radius r on the table plane: r^2 / (r^2 + H^2) (the table top is at z = 0, the source at H)."""
    b, setup = _table(res=300)
    scene, src, t = run(b, setup, library_with(), n=400_000)
    det = scene.detectors[0]
    nu, nv = det.nu, det.nv
    yv = t.ex[det.offset : det.offset + nu * nv, 1].reshape(nv, nu)
    total_y = t.n_rays * float(src.spec @ spectral.CMF[:, 1])
    uc = det.ext[0] + (np.arange(nu) + 0.5) / nu * (det.ext[1] - det.ext[0])
    vc = det.ext[2] + (np.arange(nv) + 0.5) / nv * (det.ext[3] - det.ext[2])
    rr = np.hypot(*np.meshgrid(uc, vc))
    for r in (6.0, 12.0, 25.0):
        got = yv[rr < r].sum() / total_y
        assert got == pytest.approx(r**2 / (r**2 + H**2), rel=0.03), f"r={r}"


@pytest.mark.parametrize("el", [90.0, 45.0])
def test_reflected_luminance_of_a_diffuse_table(el):
    """Flux sent into the viewing cone = rho * (incident flux) * sin^2(a) * cos(t), t = angle of the view direction from the normal."""
    cone = 20.0
    b, setup = _table(el=el, cone=cone)
    scene, _, t = run(b, setup, library_with(), n=400_000)
    det = scene.detectors[0]
    assert det.view_cos == pytest.approx(math.sin(math.radians(el)), abs=1e-9)
    sl = slice(det.offset, det.offset + det.nu * det.nv)
    ratio = t.cone[sl, 1].sum() / t.ex[sl, 1].sum()
    assert ratio == pytest.approx(RHO * math.sin(math.radians(cone)) ** 2 * math.sin(math.radians(el)), rel=0.03)
    # the rings around the cone give the light at other angles, measured about the view direction too
    assert t.ring[sl].sum() > 0


def test_black_receiver_still_counts_incident_light():
    b, setup = _table(finish="black")
    scene, _, t = run(b, setup, library_with(), n=100_000)
    det = scene.detectors[0]
    sl = slice(det.offset, det.offset + det.nu * det.nv)
    assert t.rcv_pow[0] / t.n_rays == pytest.approx(0.9, abs=0.05)
    assert t.cone[sl, 1].sum() == 0.0
    assert t.abs_surf[0] / t.n_rays == pytest.approx(t.rcv_pow[0] / t.n_rays, rel=1e-6)


def test_exit_detector_is_unchanged():
    """The same face as an exit surface (not a receiver): nothing leaves a table, and nothing is counted."""
    b, setup = _table(kind="exit")
    scene, _, t = run(b, setup, library_with(), n=50_000)
    assert scene.detectors[0].kind == "exit"
    assert t.rcv_pow[0] == 0.0 and t.det_pow[0] == 0.0
    assert t.ex.sum() == 0.0


def test_view_direction_warnings():
    b, setup = _table(el=10.0, cone=20.0)
    opt, doc = b.finish()
    sc = build_scene(opt, doc, setup, library_with())
    assert any("reaches below the surface" in w for w in sc.warnings)
    b, setup = _table(el=0.0, cone=5.0)
    opt, doc = b.finish()
    sc = build_scene(opt, doc, setup, library_with())
    assert any("surface plane" in w for w in sc.warnings)


def test_receiver_faces_are_not_tagged_for_the_analysis():
    b, setup = _table()
    setup["analysisGroups"] = [{"id": "g", "name": "table", "faces": [{"part": "table", "face": None}]}]
    opt, doc = b.finish()
    sc = build_scene(opt, doc, setup, library_with())
    det = sc.detectors[0]
    assert (sc.tri_group[det.tri_ids] < 0).all()
    assert any("receiver detector are not tagged" in w for w in sc.warnings)


def test_tally_budget_ignores_the_receiver():
    """``total_accounted`` (the energy budget) must not include the incident counter."""
    b, setup = _table()
    opt, doc = b.finish()
    scene = build_scene(opt, doc, setup, library_with())
    from refract.engine import sources

    src = sources.build_channel_sources(setup, doc, scene, opt, library_with())[0]
    t = tracer.trace_channel(scene, src, 20_000, seed=1)
    assert t.rcv_pow[0] > 0
    assert t.total_accounted() == pytest.approx(20_000, rel=1e-6)


def test_analysis_and_replay_with_a_receiver():
    """Incident light on a receiver is a deposit of the analysis log: the maps add up, the replay marks the arrival as the path's exit event."""
    from refract.engine import kernels, replay, sources

    b, setup = _table(el=45.0, cone=20.0, res=16)
    opt, doc = b.finish()
    lib = library_with()
    scene = build_scene(opt, doc, setup, lib)
    src = sources.build_channel_sources(setup, doc, scene, opt, lib)[0]
    n = 60_000
    plain = tracer.trace_channel(scene, src, n, seed=5)
    t = tracer.trace_channel(scene, src, n, seed=5, analysis=True)
    for name in ("ex", "cone", "dirh", "det_pow", "rcv_pow", "abs_surf", "special"):
        assert np.array_equal(getattr(plain, name), getattr(t, name)), name  # the analysis does not change the physics
    a = t.analysis
    assert a.tot_map[0].sum() == pytest.approx(t.ex[:, 1].sum(), rel=1e-9)
    assert a.tot_map[1].sum() == pytest.approx(t.cone[:, 1].sum(), rel=1e-9)
    dep = a.dep
    assert dep["wy"].astype(np.float64).sum() == pytest.approx(t.ex[:, 1].sum(), rel=1e-4)
    cnt = np.bincount(dep["rid"], minlength=n)
    ids = np.unique(dep["rid"])[:60]
    out = replay.replay(scene, src, 5, ids)
    assert out
    for r in out:
        assert r["event"].count(kernels.EV_EXIT) == cnt[r["id"]]
        assert len(r["points"]) >= 3  # the ray goes on after the table (it is not consumed)


def _plate_over_table(through: bool, finish="white_diffuse"):
    """A lamp under which a glass plate (its lower face is the exit surface) hangs above a table (a receiver)."""
    b = Builder()
    b.add_box("plate", (-60, -60, -13), (60, 60, -10))
    b.add_box("table", (-200, -200, -42), (200, 200, -40))
    exit_det = detector("plate", 4, "exit", res=24, cone=30)  # (face 4: the lower face, normal -z)
    exit_det["pass_through"] = through
    table = detector("table", 5, "table", res=24, cone=20)
    table.update({"type": "receiver", "view_el": 90, "view_az": 0})
    setup = {
        "parts": [
            {"match": "*", "finish": "black"},
            {"match": "plate", "material": "glass", "finish": "polished"},
            {"match": "table", "finish": finish},
        ],
        "leds": [led((0, 0, 0), "lamb", axis=(0, 0, -1))],
        "detectors": [exit_det, table],
    }
    return b, setup


def test_pass_through_exit_lets_the_light_reach_the_table():
    lib = library_with(glass={"n": 1.5, "absorption": 0.0})
    res = {}
    for through in (False, True):
        b, setup = _plate_over_table(through)
        scene, src, t = run(b, setup, lib, n=150_000)  # (run() checks that the energy budget closes)
        assert scene.detectors[0].through is through and scene.det_kind[0] == (2 if through else 0)
        res[through] = (scene, t)
    sc0, consuming = res[False]
    sc1, thru = res[True]
    ex0 = consuming.ex[: sc0.detectors[0].nu * sc0.detectors[0].nv, 1].sum()
    ex1 = thru.ex[: sc1.detectors[0].nu * sc1.detectors[0].nv, 1].sum()
    # the exit surface measures the same light either way, plus what the table sends back up and leaves through the plate again
    assert 0.97 * ex0 < ex1 < 1.15 * ex0
    assert consuming.det_pow[0] > 0 and thru.det_pow[0] == 0.0 and 0.97 * consuming.det_pow[0] < thru.rcv_pow[0] < 1.15 * consuming.det_pow[0]
    # a consuming exit takes the light away: the table gets next to nothing; a pass-through exit hands it on
    assert consuming.rcv_pow[1] < 0.02 * consuming.det_pow[0]
    assert thru.rcv_pow[1] > 0.5 * thru.rcv_pow[0]
    assert thru.det_pow.sum() == 0.0 and thru.total_accounted() == pytest.approx(thru.n_rays, rel=1e-6)


def test_picture_looks_at_a_receiver_from_the_air_side():
    """The camera of the Mitsuba picture is on the air side of a receiver, even when the table is the lowest part of the model
    (the middle of the model then lies above it)."""
    from refract.engine import sources
    from refract.render import mitsuba_export as M

    b, setup = _plate_over_table(True)
    opt, doc = b.finish()
    scene = build_scene(opt, doc, setup, library_with(glass={"n": 1.5, "absorption": 0.0}))
    plate, table = scene.detectors
    assert M.detector_normal(scene, table)[2] == pytest.approx(1.0)  # up, towards the air above the table
    cam = M._camera(scene, {"detector": 1})
    assert cam["target"][2] == pytest.approx(-40.0) and cam["origin"][2] > cam["target"][2]  # above the table top, not below it
    first = M._camera(scene, {})
    assert first["target"][2] == pytest.approx(-13.0)  # the default is still the first detector (an exit surface keeps its rule)
    assert plate.kind == "exit"


def test_receiver_is_drawn_with_its_own_finish():
    """In the picture a receiver keeps the finish of its part under the glow of its map (a hole-like black absorber was wrong)."""
    from refract.render import mitsuba_export as M

    b, setup = _plate_over_table(True)
    opt, doc = b.finish()
    scene = build_scene(opt, doc, setup, library_with(glass={"n": 1.5, "absorption": 0.0}))
    bsdf = M._receiver_bsdf(scene, scene.detectors[1])
    assert bsdf["type"] == "twosided" and bsdf["bsdf"]["type"] == "diffuse"
    assert bsdf["bsdf"]["reflectance"]["value"][0] == pytest.approx(RHO, abs=0.02)  # white_diffuse


def _two_surface_run_data(through=True, n=150_000):
    """A RunData (what simrun stores, without the files) of the plate-over-table setup: the exit surface and the table."""
    from refract.results import RunData

    b, setup = _plate_over_table(through)
    scene, src, t = run(b, setup, library_with(glass={"n": 1.5, "absorption": 0.0}), n=n)
    arrays = {k: np.asarray(getattr(t, k))[None] for k in ("ex", "cone", "ex2", "cone2", "ring", "ring2", "dirh", "det_pow", "abs_vol", "abs_surf", "special", "rcv_pow")}
    arrays.update(w0=np.array([src.flux_w / t.n_rays]), n_rays=np.array([t.n_rays]), accounted=np.ones(1), flux_lm=np.array([src.flux_lm]))
    dets = []
    for k, d in enumerate(scene.detectors):
        arrays[f"area_map_{k}"] = d.area_map
        dets.append({"nu": d.nu, "nv": d.nv, "offset": d.offset, "cone_deg": d.cone_deg, "ring_deg": d.ring_deg.tolist(), "ext": d.ext.tolist(), "view_cos": d.view_cos})
    sc = {
        "detectors": dets,
        "channels": [{"key": "c", "led_id": "l", "led_name": "l", "flux_w": src.flux_w, "flux_lm": src.flux_lm}],
        "states": [{"id": "s", "name": "s", "drive": {"c": 1.0}}],
        "bodies": [],
        "warnings": [],
    }
    return RunData("r", "x", sc, arrays, {}), scene, src, t


def test_picture_luminances_are_real_cd_per_m2(monkeypatch):
    """The luminances of the picture (exit_maps) are photometric: the exit surface carries the flux that leaves through it, and the table
    sends rho times the flux it receives (all in lm and cd/m^2). The studio light and the exposure are tied to these numbers."""
    from refract import results
    from refract.render import mitsuba_export as mx

    data, scene, src, t = _two_surface_run_data()
    monkeypatch.setattr(results.RunData, "load", classmethod(lambda cls, rid, runid: data))
    maps, peak = mx.exit_maps("r", "x", "s", 0.0)
    y = np.array([0.2126, 0.7152, 0.0722])
    lum = [(m @ y) * peak for m in maps]  # cd/m^2 of each pixel
    flux_lm = float(src.flux_lm)
    # the exit surface: the luminous flux through it is the LED's flux times the share of the power that goes through
    comb = data.combine("s")
    ex = data.detector_maps(comb, 0, results.DEFAULT_ANALYSIS)["ex"][..., 1]
    assert ex.sum() == pytest.approx(flux_lm * t.rcv_pow[0] / t.n_rays, rel=0.03)
    # the table: reflected flux = rho * received flux = pi * sum(L * dA) for a Lambertian table
    a1 = scene.detectors[1].area_map * 1e-6
    reflected = math.pi * float((lum[1] * a1).sum())
    assert reflected == pytest.approx(RHO * flux_lm * t.rcv_pow[1] / t.n_rays, rel=0.1)
    assert max(float(m.max()) for m in maps) == pytest.approx(1.0)


def test_picture_reference_is_not_set_by_one_hot_pixel_and_numbers_are_reported(monkeypatch):
    """The "bright" level of the picture counts every pixel for its area, so the big table keeps a sensible exposure next to a small
    exit surface that is a thousand times brighter; the per-surface numbers are the ones shown next to the picture."""
    from refract import results
    from refract.render import mitsuba_export as mx

    data, scene, src, t = _two_surface_run_data()
    monkeypatch.setattr(results.RunData, "load", classmethod(lambda cls, rid, runid: data))
    _, peak, info = mx.exit_maps_info("r", "x", "s", 1.0)
    assert [s["index"] for s in info["surfaces"]] == [0, 1]
    exit_s, table_s = info["surfaces"]
    assert table_s["mean"] > 0 and exit_s["mean"] > 5 * table_s["mean"]  # the exit is far brighter than the table
    assert 0 < info["reference"] < peak / 3  # one bright spot of the small exit surface does not set it
    _, _, info1 = mx.exit_maps_info("r", "x", "s", 1.0, hidden=(0,))
    assert [s["index"] for s in info1["surfaces"]] == [1]  # a hidden surface is not reported
    assert mx.white_card_luminance(300.0) == pytest.approx(300.0 / math.pi)
    vals = np.array([1.0, 2.0, 3.0, 100.0])
    assert mx._weighted_percentile(vals, np.array([1.0, 1.0, 1.0, 0.001]), 99.5) == pytest.approx(3.0)
    assert mx._weighted_percentile(vals, np.array([1.0, 1.0, 1.0, 100.0]), 99.5) == pytest.approx(100.0)
