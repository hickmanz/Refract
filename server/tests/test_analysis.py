"""Light path analysis: face tags and group maps in the tracer, exact ray replay, and the analysis API."""

import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import SAMPLES
from refract.engine import kernels, replay, sources, tracer
from refract.engine.scene import build_scene
from test_engine_physics import Builder, detector, led, library_with

N = 120_000


def _setup_url(client, pid):
    """The setup route of the project's first (baseline) variant."""
    vid = client.get(f"/api/projects/{pid}").json()["variants"][0]["id"]
    return f"/api/projects/{pid}/variants/{vid}/setup"


def _scene():
    """An LED under a glass plate (the exit surface), between a white diffuse wall and a mirror wall."""
    b = Builder()
    b.add_box("plate", (-60, -60, 10), (60, 60, 13))
    b.add_box("wallL", (-40, -60, -5), (-38, 60, 20))
    b.add_box("wallR", (38, -60, -5), (40, 60, 20))
    setup = {
        "parts": [
            {"match": "*", "finish": "black"},
            {"match": "plate", "material": "glass", "finish": "polished"},
            {"match": "wallL", "finish": "white_diffuse"},
            {"match": "wallR", "finish": "mirror"},
        ],
        "leds": [led((0, 0, 0), "lamb")],
        "detectors": [detector("plate", 5, res=40, cone=90)],
    }
    library = library_with(glass={"n": 1.5, "absorption": 0.0})
    opt, doc = b.finish()
    scene = build_scene(opt, doc, setup, library)
    srcs = sources.build_channel_sources(setup, doc, scene, opt, library)
    return scene, srcs[0]


@pytest.fixture(scope="module")
def traced():
    scene, src = _scene()
    plain = tracer.trace_channel(scene, src, N, seed=5)
    tagged = tracer.trace_channel(scene, src, N, seed=5, analysis=True)
    return scene, src, plain, tagged


def test_auto_groups_are_the_opaque_faces(traced):
    scene = traced[0]
    names = [g["name"] for g in scene.groups]
    assert len(scene.groups) == 12 and all("wall" in n for n in names)
    is_wall = np.array([scene.bodies[b]["name"].startswith("wall") for b in scene.tri_body])
    assert np.array_equal(scene.tri_group >= 0, is_wall)  # every wall triangle is tagged, the plate is not


def test_tags_do_not_change_the_physics(traced):
    _, _, plain, tagged = traced
    for name in ("ex", "cone", "dirh", "det_pow", "abs_vol", "abs_surf", "special"):
        assert np.array_equal(getattr(plain, name), getattr(tagged, name)), name


def test_group_maps_add_up_to_the_exit_map(traced):
    scene, _, _, t = traced
    a = t.analysis
    assert np.allclose(a.tot_map[0].sum(), t.ex[:, 1].sum(), rtol=1e-9)
    assert np.allclose(a.tot_map[1].sum(), t.cone[:, 1].sum(), rtol=1e-9)
    for view in (0, 1):
        assert np.allclose(a.grp_map[view].sum(axis=0), a.tot_map[view], rtol=1e-9, atol=1e-12)
        # the light whose last group is g has touched g
        assert np.all(a.touch_map[view] + 1e-12 >= a.grp_map[view][1:])
    # light that used a wall reaches the exit, and some of it did not touch any wall
    assert a.grp_map[0][1:].sum() > 0 and a.grp_map[0][0].sum() > 0


def test_face_tallies_conserve_energy(traced):
    scene, _, _, t = traced
    a = t.analysis
    assert a.face_t[:, 1].sum() == pytest.approx(t.abs_surf.sum(), rel=1e-9)
    tagged = scene.tface_gface
    inc = a.face_t[tagged, 0]
    assert np.allclose(a.face_from.sum(axis=1), inc, rtol=1e-9)
    big = inc > 0.01 * N
    assert big.any()
    # everything that leaves a tagged face arrives somewhere (the next group, the exit, absorbed, escaped); roulette is unbiased noise
    assert np.allclose(a.face_to[big].sum(axis=1), inc[big], rtol=0.03)
    # an opaque face reflects or absorbs everything that hits it
    ft = a.face_t[tagged]
    assert np.allclose(ft[:, 1] + ft[:, 2], ft[:, 0], rtol=1e-9)


def test_logs_describe_the_rays(traced):
    scene, _, _, t = traced
    a = t.analysis
    assert len(a.end["rid"]) == N and len(np.unique(a.end["rid"])) == N
    dep = a.dep
    # luminous weight of the deposits is the exit map
    assert dep["wy"].astype(np.float64).sum() == pytest.approx(t.ex[:, 1].sum(), rel=1e-4)
    # the deposits counter of a ray matches the rows of the deposit log
    cnt = np.bincount(dep["rid"], minlength=N)
    assert np.array_equal(cnt, (a.end["tag"][np.argsort(a.end["rid"]), 3] >> 48) & 255)


def test_replay_is_exact(traced):
    scene, src, _, t = traced
    dep = t.analysis.dep
    ids = np.unique(dep["rid"])[:60]
    out = replay.replay(scene, src, 5, ids)
    again = replay.replay(scene, src, 5, ids[::-1])
    by_id = {r["id"]: r for r in again}
    cnt = np.bincount(dep["rid"], minlength=N)
    for r in out:
        assert r["points"] == by_id[r["id"]]["points"]  # the same ray whatever the company it is traced in
        assert r["event"].count(kernels.EV_EXIT) == cnt[r["id"]]  # the deposits of the log
        assert r["event"][0] == kernels.EV_START and len(r["points"]) >= 2
    # a single ray alone gives the same path again
    one = replay.replay(scene, src, 5, [int(ids[7])])[0]
    assert one["points"] == by_id[int(ids[7])]["points"]


def test_measured_ray_sampling_does_not_depend_on_batching():
    ids = np.arange(1000, 1100)
    u = sources._ray_uniform(3, ids)
    assert np.array_equal(u[20:50], sources._ray_uniform(3, ids[20:50]))
    assert 0.0 <= u.min() and u.max() < 1.0 and not np.array_equal(u, sources._ray_uniform(4, ids))


def test_user_groups_and_other():
    b = Builder()
    b.add_box("plate", (-60, -60, 10), (60, 60, 13))
    b.add_box("wall", (-40, -60, -5), (-38, 60, 20))
    setup = {
        "parts": [{"match": "*", "finish": "black"}, {"match": "plate", "material": "glass", "finish": "polished"}, {"match": "wall", "finish": "white_diffuse"}],
        "leds": [led((0, 0, 0), "lamb")],
        "detectors": [detector("plate", 5)],
        "analysisGroups": [{"id": "inner", "name": "Inner", "faces": [{"part": "wall", "face": 1}]}],
    }
    library = library_with(glass={"n": 1.5, "absorption": 0.0})
    opt, doc = b.finish()
    scene = build_scene(opt, doc, setup, library)
    assert [g["name"] for g in scene.groups] == ["Inner"]
    assert (scene.tri_group >= 0).sum() == 2  # one rectangle = two triangles


# -- the API ---------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    from refract.app import app

    return TestClient(app)


def _wait(client, url, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        meta = client.get(url).json()
        if meta["status"] in ("ready", "failed", "cancelled"):
            return meta
        time.sleep(0.5)
    raise AssertionError(f"timeout waiting for {url}")


@pytest.fixture(scope="module")
def run(client):
    with open(SAMPLES / "lightbar_a.step", "rb") as fh:
        res = client.post("/api/revisions", files={"file": ("lightbar_a.step", fh)}, data={"project_id": "new", "project_name": "Analysis"})
    rid, pid = res.json()["id"], res.json()["projectId"]
    assert _wait(client, f"/api/revisions/{rid}")["status"] == "ready"
    sug = client.post(f"/api/revisions/{rid}/suggest-setup").json()
    setup = {k: sug[k] for k in ("parts", "leds", "detectors", "states")}
    assert client.put(_setup_url(client, pid), json=setup).status_code == 200
    res = client.post(f"/api/revisions/{rid}/runs", json={"settings": {"rays": 40_000}})
    runid = res.json()["id"]
    meta = _wait(client, f"/api/runs/{runid}")
    assert meta["status"] == "ready", meta.get("error")
    yield runid
    client.delete(f"/api/projects/{pid}")


def test_overview_and_maps(client, run):
    ov = client.get(f"/api/runs/{run}/analysis").json()
    assert ov["groups"] and ov["exitLm"] > 0
    total = ov["directFraction"] + sum(g["lastFraction"] for g in ov["groups"])
    assert total == pytest.approx(1.0, abs=1e-3)
    g = max(ov["groups"], key=lambda x: x["lastFraction"])
    m = client.get(f"/api/runs/{run}/analysis/group-map", params={"group": g["index"], "mode": "last"}).json()
    assert len(m["values"]) == m["nv"] and len(m["values"][0]) == m["nu"]
    assert m["fraction"] == pytest.approx(g["lastFraction"], abs=1e-3)
    t = client.get(f"/api/runs/{run}/analysis/group-map", params={"group": g["index"], "mode": "touched"}).json()
    assert t["fraction"] >= m["fraction"] - 1e-6
    # the light that came straight from the LED (group -1) has a map too, but only "left it last" is meaningful for it
    d = client.get(f"/api/runs/{run}/analysis/group-map", params={"group": -1, "mode": "last"})
    assert d.status_code == 200
    assert d.json()["fraction"] == pytest.approx(ov["directFraction"], abs=1e-3)
    assert client.get(f"/api/runs/{run}/analysis/group-map", params={"group": -1, "mode": "touched"}).status_code == 404


def test_groups_know_where_they_are(client, run):
    ov = client.get(f"/api/runs/{run}/analysis").json()
    located = [g for g in ov["groups"] if g["geometry"]]
    assert located
    for g in located:
        lo, hi = g["geometry"]["bbox"]
        assert all(a <= b + 1e-9 for a, b in zip(lo, hi))
        assert all(a - 1e-6 <= c <= b + 1e-6 for a, c, b in zip(lo, g["geometry"]["centroid"], hi))
    flat = [g for g in located if g["geometry"]["normal"]]
    assert flat and all(abs(sum(c * c for c in g["geometry"]["normal"]) - 1.0) < 1e-6 for g in flat)
    # names say what the part is (instance name, plus the part name when it differs)
    assert all(" · face " in g["name"] for g in ov["groups"] if g["auto"])


def test_region_families_and_rays(client, run):
    poly = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]
    left = client.post(f"/api/runs/{run}/analysis/region", json={"polygon": poly}).json()
    right = client.post(f"/api/runs/{run}/analysis/region", json={"polygon": [[1 - x, y] for x, y in poly]}).json()
    assert left["fluxFraction"] + right["fluxFraction"] == pytest.approx(1.0, abs=0.02)
    assert left["byGroup"] and sum(r["fraction"] for r in left["byGroup"]) == pytest.approx(1.0, abs=1e-3)
    assert left["byLed"] and left["byBounces"] and len(left["byEvent"]) == 6

    fams = client.get(f"/api/runs/{run}/analysis/families").json()["families"]
    assert fams and sum(f["fraction"] for f in fams) <= 1.0 + 1e-6
    fm = client.get(f"/api/runs/{run}/analysis/family-map", params={"family": fams[0]["id"]}).json()
    assert fm["fraction"] == pytest.approx(fams[0]["fraction"], abs=1e-3)

    rays = client.post(f"/api/runs/{run}/analysis/rays", json={"filter": {"region": {"detector": 0, "polygon": poly}}, "count": 25}).json()
    assert 0 < len(rays["paths"]) <= 25
    p = rays["paths"][0]
    assert len(p["points"]) == len(p["event"]) == len(p["gface"]) == len(p["group"]) == len(p["w"])
    assert kernels.EV_EXIT in p["event"]
    # the ray drawn for a family goes through the groups of the route
    fam_rays = client.post(f"/api/runs/{run}/analysis/rays", json={"filter": {"family": fams[0]["id"]}, "count": 10}).json()
    assert fam_rays["paths"]
    # an end-log filter
    lost = client.post(f"/api/runs/{run}/analysis/rays", json={"filter": {"outcome": "absorbed"}, "count": 10}).json()
    assert lost["matched"] > 0 and lost["paths"] and all(len(q["points"]) >= 2 for q in lost["paths"])


def test_face_inspector_hits_and_guidance(client, run):
    hits = client.get(f"/api/runs/{run}/analysis/hits").json()
    assert hits["faces"] and hits["maxDensity"] > 0
    ov = client.get(f"/api/runs/{run}/analysis").json()
    g = max(ov["groups"], key=lambda x: x["incident"])
    f = g["faces"][0]
    rep = client.get(f"/api/runs/{run}/analysis/face", params={"instance": f["instance"], "face": f["face"]}).json()
    assert rep["tagged"] and rep["incident"] > 0
    assert rep["comesFrom"] and rep["goesTo"]
    assert sum(r["fraction"] for r in rep["comesFrom"]) == pytest.approx(1.0, abs=1e-3)
    assert client.get(f"/api/runs/{run}/analysis/face", params={"instance": "nope", "face": 1}).status_code == 404

    gd = client.get(f"/api/runs/{run}/analysis/guidance").json()
    assert gd["suggestions"] is not None and gd["predicted"]["cv"] <= gd["current"]["cv"] + 1e-6
    assert len(gd["deficit"]) == gd["nv"]


def test_check_lists_the_analysis_groups(client, run):
    rid = client.get(f"/api/runs/{run}").json()["revisionId"]
    chk = client.post(f"/api/revisions/{rid}/check").json()
    assert chk["groups"] and all({"index", "id", "name", "color", "auto", "faces"} <= set(g) for g in chk["groups"])
    assert all(f["face"] is None or isinstance(f["face"], int) for g in chk["groups"] for f in g["faces"])


def test_flux_slice(client, run):
    ov = client.get(f"/api/runs/{run}/analysis").json()
    boxes = [g["geometry"]["bbox"] for g in ov["groups"] if g["geometry"]]
    lo = np.min([b[0] for b in boxes], axis=0)
    hi = np.max([b[1] for b in boxes], axis=0)
    axis = int(np.argmax(hi - lo))  # a cut across the long way of the part
    origin = ((lo + hi) / 2).tolist()
    normal = [0.0, 0.0, 0.0]
    normal[axis] = 1.0

    def cut(**kw):
        body = {"origin": origin, "normal": normal, "count": 4000, **kw}
        res = client.post(f"/api/runs/{run}/analysis/slice", json=body)
        assert res.status_code == 200, res.text
        return res.json()

    both = cut()
    assert len(both["values"]) == both["nv"] and len(both["values"][0]) == both["nu"]
    assert both["max"] > 0 and both["total"] > 0 and both["crossings"] > 0 and len(both["corners"]) == 4
    # the two ways add up to both, and the outcomes add up to everything
    along, against = cut(direction="along"), cut(direction="against")
    assert along["total"] + against["total"] == pytest.approx(both["total"], rel=1e-6)
    parts = [cut(outcome=o)["total"] for o in ("exit", "absorbed", "other")]
    assert sum(parts) == pytest.approx(both["total"], rel=1e-6)
    # frame: u x v = normal
    assert np.allclose(np.cross(both["u"], both["v"]), normal)
    # moving the cut does not need new rays (same sample) and still answers
    origin[axis] = float(lo[axis] + 0.9 * (hi[axis] - lo[axis]))
    assert cut()["rays"] == both["rays"]
    assert client.post(f"/api/runs/{run}/analysis/slice", json={"origin": [0, 0, 0], "normal": [0, 0, 0]}).status_code == 400


def test_flow_bands_add_up(client, run):
    fl = client.get(f"/api/runs/{run}/analysis/flow", params={"bands": 4}).json()
    assert fl["families"] and fl["along"] in ("u", "v") and len(fl["matrix"]) == len(fl["families"])
    for fam, row in zip(fl["families"], fl["matrix"]):
        assert len(row) == 4 and sum(row) == pytest.approx(fam["fraction"], abs=2e-3)
    assert sum(sum(r) for r in fl["matrix"]) == pytest.approx(fl["covered"], abs=5e-3)


def test_map_of_a_ray_filter(client, run):
    def rmap(flt):
        res = client.post(f"/api/runs/{run}/analysis/rays-map", json={"filter": flt})
        assert res.status_code == 200, res.text
        return res.json()

    everything = rmap({})
    assert everything["fraction"] == pytest.approx(1.0, abs=1e-6) and np.sum(everything["values"]) > 0
    # a route's map is the family map of that route, and the rays of a filter land where the map says
    fams = client.get(f"/api/runs/{run}/analysis/families").json()["families"]
    fam = client.get(f"/api/runs/{run}/analysis/family-map", params={"family": fams[0]["id"]}).json()
    assert rmap({"family": fams[0]["id"]})["values"] == fam["values"]
    # filters narrow it down; light that does not reach the exit lands nowhere
    led = client.get(f"/api/runs/{run}/analysis").json()
    assert rmap({"bounces": [0, 1]})["fraction"] <= everything["fraction"]
    assert rmap({"outcome": "absorbed"})["fraction"] == 0.0


def test_map_of_rays_through_several_groups(client, run):
    ov = client.get(f"/api/runs/{run}/analysis").json()
    a, b = sorted(ov["groups"], key=lambda g: -g["touchedFraction"])[:2]

    def frac(touched):
        res = client.post(f"/api/runs/{run}/analysis/rays-map", json={"filter": {"touched": touched}})
        assert res.status_code == 200, res.text
        return res.json()["fraction"]

    both = frac([a["index"], b["index"]])
    assert frac(a["index"]) == pytest.approx(frac([a["index"]]), abs=1e-9)
    assert both <= min(frac(a["index"]), frac(b["index"])) + 1e-9
