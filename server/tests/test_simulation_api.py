"""End-to-end: upload the sample light bar, suggest a setup, simulate, read results, compare revisions, export."""

import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from conftest import SAMPLES


@pytest.fixture(scope="module")
def client():
    from lightsim.app import app

    return TestClient(app)


def _wait(client, url, done=("ready", "failed", "cancelled"), timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        meta = client.get(url).json()
        if meta["status"] in done:
            return meta
        time.sleep(0.5)
    raise AssertionError(f"timeout waiting for {url}")


def _upload(client, name, project_id="new"):
    with open(SAMPLES / name, "rb") as fh:
        res = client.post("/api/revisions", files={"file": (name, fh)}, data={"project_id": project_id, "project_name": "Bar"})
    assert res.status_code == 200, res.text
    rid, pid = res.json()["id"], res.json()["projectId"]
    meta = _wait(client, f"/api/revisions/{rid}")
    assert meta["status"] == "ready", meta.get("error")
    return rid, pid


@pytest.fixture(scope="module")
def project(client):
    rid_a, pid = _upload(client, "lightbar_a.step")
    sug = client.post(f"/api/revisions/{rid_a}/suggest-setup").json()
    assert len(sug["leds"]) == 5
    # all five LEDs are the same part: the suggested emitting face must be the same (top) face on each of them
    assert len({led["placement"]["face"] for led in sug["leds"]}) == 1
    assert len({round(led["placement"]["sig"]["centroid"][2], 3) for led in sug["leds"]}) == 1
    assert sug["detectors"] and sug["detectors"][0]["faces"]
    setup = {k: sug[k] for k in ("parts", "leds", "detectors", "states")}
    assert client.put(f"/api/projects/{pid}/setup", json=setup).status_code == 200
    rid_b, _ = _upload(client, "lightbar_b.step", pid)
    yield {"pid": pid, "a": rid_a, "b": rid_b}
    client.delete(f"/api/projects/{pid}")


def _run(client, rid, rays=40_000):
    res = client.post(f"/api/revisions/{rid}/runs", json={"settings": {"rays": rays}})
    assert res.status_code == 200, res.text
    runid = res.json()["id"]
    meta = _wait(client, f"/api/runs/{runid}")
    assert meta["status"] == "ready", meta.get("error")
    return runid


def test_check_setup(client, project):
    res = client.post(f"/api/revisions/{project['a']}/check")
    assert res.status_code == 200, res.text
    doc = res.json()
    assert len(doc["channels"]) == 5 and len(doc["detectors"]) == 1
    for ch in doc["channels"]:  # what the viewer needs to draw the LED marker
        assert len(ch["size"]) == 2 and all(s > 0 for s in ch["size"])
        assert len(ch["ex"]) == 3 and len(ch["ey"]) == 3


def test_check_reports_missing_group_faces(client, project):
    setup = client.get(f"/api/projects/{project['pid']}/setup").json()
    parts = client.get(f"/api/revisions/{project['a']}/parts").json()["instances"]
    real = parts[0]["name"]
    setup["analysisGroups"] = [
        {"id": "g1", "name": "Gone", "faces": [{"part": real, "face": 99999}, {"part": "NoSuchPart", "face": 3}]},
    ]
    doc = client.post(f"/api/revisions/{project['a']}/check", json=setup).json()
    issues = {i["id"]: i["missing"] for i in doc["groupIssues"]}
    assert {m["reason"] for m in issues["g1"]} <= {"face", "part"}
    assert any(m["part"] == "NoSuchPart" and m["reason"] == "part" for m in issues["g1"])
    assert len(issues["g1"]) == 2
    assert sum("no longer exist" in w for w in doc["warnings"]) == 1


def test_run_results_and_energy(client, project):
    runid = _run(client, project["a"])
    res = client.get(f"/api/runs/{runid}/results").json()
    assert res["state"]["id"] == "all"
    # all emitted energy is accounted for
    assert abs(res["lossSum"] - 1.0) < 1e-3
    det = res["detectors"][0]
    assert 0.05 < det["efficiency"] < 0.9
    assert det["stats"]["mean"] > 0 and 0 < det["stats"]["minOverMax"] <= 1
    assert det["color"]["cct"] > 2000
    assert len(det["leds"]) == 5 and abs(sum(l["shareOfExit"] for l in det["leds"]) - 1.0) < 1e-3
    assert len(det["luminance"]) == det["nv"] and len(det["luminance"][0]) == det["nu"]
    # symmetric 5 LED bar: the centre LED contributes more than the end LEDs? just require all contribute
    assert all(l["exitLm"] > 0 for l in det["leds"])

    # one LED only state
    one = client.get(f"/api/runs/{runid}/results", params={"state": "only_led1"}).json()
    assert abs(one["emitted"]["lm"] - res["emitted"]["lm"] / 5) / (res["emitted"]["lm"] / 5) < 0.2

    assert client.get(f"/api/runs/{runid}/paths").json()["paths"]
    png = client.get(f"/api/runs/{runid}/image.png")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/runs/{runid}/export.zip").content))
    assert "metrics.csv" in z.namelist()

    runs = client.get(f"/api/revisions/{project['a']}/runs").json()
    assert runs and runs[0]["summary"]["states"]


def test_compare_revisions(client, project):
    ra = _run(client, project["a"], 20_000)
    rb = _run(client, project["b"], 20_000)
    cmp = client.get(f"/api/runs/{ra}/compare", params={"other": rb}).json()
    assert cmp["maps"]["nu"] > 0 and cmp["a"]["efficiency"] > 0 and cmp["b"]["efficiency"] > 0


def test_coloured_housing_shifts_colour(client, project):
    """A yellow reflector absorbs blue: less light reaches the exit and its colour becomes warmer."""
    pid = project["pid"]
    setup = client.get(f"/api/projects/{pid}/setup").json()
    base = _run(client, project["a"], 60_000)
    ref = client.get(f"/api/runs/{base}/results").json()["detectors"][0]
    yellow = {**setup, "parts": [({**p, "finish": "plastic_yellow"} if p["match"] == "Reflector Tray:1" else p) for p in setup["parts"]]}
    try:
        assert client.put(f"/api/projects/{pid}/setup", json=yellow).status_code == 200
        run = _run(client, project["a"], 60_000)
        out = client.get(f"/api/runs/{run}/results").json()["detectors"][0]
    finally:
        client.put(f"/api/projects/{pid}/setup", json=setup)
    assert out["efficiency"] < 0.9 * ref["efficiency"]
    assert out["color"]["cct"] < ref["color"]["cct"] - 300


def test_parameter_sweep(client, project):
    setup = client.get(f"/api/projects/{project['pid']}/setup").json()
    body = {"parameter": "tio2_wt_pct", "part": "Light Pipe:1", "values": [0.02, 0.3], "settings": {"rays": 20_000}, "name": "TiO2"}
    res = client.post(f"/api/revisions/{project['a']}/sweeps", json=body)
    assert res.status_code == 200, res.text
    runs = res.json()["runs"]
    assert [r["sweep"]["value"] for r in runs] == [0.02, 0.3]
    assert len({r["sweep"]["id"] for r in runs}) == 1
    done = [_wait(client, f"/api/runs/{r['id']}") for r in runs]
    assert all(d["status"] == "ready" for d in done), [d.get("error") for d in done]
    effs = [d["summary"]["states"][0]["detectors"][0]["efficiency"] for d in done]
    assert effs[0] != effs[1]
    # the project's own setup is untouched by the sweep
    assert client.get(f"/api/projects/{project['pid']}/setup").json() == setup

    bad = client.post(f"/api/revisions/{project['a']}/sweeps", json={**body, "part": "LED:1"})
    assert bad.status_code == 400
    bad = client.post(f"/api/revisions/{project['a']}/sweeps", json={**body, "values": [1.0]})
    assert bad.status_code == 400


def _lambertian_ray_text(n=4000, half=0.7, seed=3):
    import numpy as np

    rng = np.random.default_rng(seed)
    x, y = rng.uniform(-half, half, n), rng.uniform(-half, half, n)
    ct = np.sqrt(rng.random(n))  # cosine-weighted
    st = np.sqrt(1 - ct**2)
    ph = 2 * np.pi * rng.random(n)
    lines = ["# generated test rays", "x y z l m n flux"]
    lines += [f"{a:.4f} {b:.4f} 0 {s * np.cos(p):.5f} {s * np.sin(p):.5f} {c:.5f} 1" for a, b, s, p, c in zip(x, y, st, ph, ct)]
    return "\n".join(lines)


def test_ray_file_source(client, project):
    bad = client.post("/api/library/rays", content=b"this is not a ray file\n1 2 3")
    assert bad.status_code == 400
    res = client.post("/api/library/rays", content=_lambertian_ray_text().encode())
    assert res.status_code == 200, res.text
    info = res.json()
    assert info["count"] == 4000 and not info["hasWavelength"] and info["backwardFraction"] == 0
    assert abs(info["intensity"][12] / info["intensity"][4] - 0.5) < 0.15

    base = client.get(f"/api/projects/{project['pid']}/setup").json()
    spec = {"type": "phosphor_white", "cct": 5000}
    pat = client.post("/api/library/leds", json={"name": "T pattern", "channels": [{"id": "W", "name": "W", "flux_lm": 10, "spectrum": spec, "pattern": {"type": "lambertian", "exponent": 1}}], "emitter": {"shape": "rect", "size": [1.6, 1.6]}}).json()
    ray = client.post("/api/library/leds", json={"name": "T rays", "channels": [{"id": "W", "name": "W", "flux_lm": 10, "spectrum": spec, "source": {"type": "rays", "file": info["file"]}}], "emitter": {"shape": "rect", "size": [1.6, 1.6]}}).json()
    try:
        effs = []
        for model in (pat["id"], ray["id"]):
            setup = {**base, "leds": [{**led, "model": model} for led in base["leds"]]}
            assert client.put(f"/api/projects/{project['pid']}/setup", json=setup).status_code == 200
            runid = _run(client, project["a"], 60_000)
            effs.append(client.get(f"/api/runs/{runid}/results").json()["detectors"][0]["efficiency"])
        assert abs(effs[0] - effs[1]) < 0.04, effs
    finally:
        client.put(f"/api/projects/{project['pid']}/setup", json=base)
        client.delete(f"/api/library/leds/{pat['id']}")
        client.delete(f"/api/library/leds/{ray['id']}")


def test_mitsuba_render(client, project):
    pytest.importorskip("mitsuba")
    runid = _run(client, project["a"], 20_000)
    res = client.post(f"/api/runs/{runid}/render", json={"width": 200, "spp": 4, "state": "only_led1", "ambient": 0.0})
    assert res.status_code == 200, res.text
    t0 = time.time()
    while time.time() - t0 < 180:
        r = client.get(f"/api/runs/{runid}/render").json()["render"]
        if r["status"] in ("ready", "failed"):
            break
        time.sleep(0.5)
    assert r["status"] == "ready", r.get("error")
    png = client.get(f"/api/runs/{runid}/render.png")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    import io as _io

    from PIL import Image
    import numpy as np

    img = np.asarray(Image.open(_io.BytesIO(png.content)).convert("L")).astype(float)
    assert img.max() > 100  # something is lit
    cols = np.nonzero(img.max(axis=0) > 20)[0]
    left, right = img[:, cols.min() : cols.min() + 20].mean(), img[:, cols.max() - 20 : cols.max()].mean()
    assert left > 2 * right  # LED 1 is at the left end of the bar
    assert client.get(f"/api/runs/{runid}/render").json()["render"]["info"]["camera"]
    bad = client.post(f"/api/runs/{runid}/render", json={"width": 50})
    assert bad.status_code == 400


def test_unwrapped_exit_surface_matches_flat_projection(client, project):
    """A flat exit face unwrapped must give the same totals and the same profile as the flat projection."""
    import numpy as np

    base = client.get(f"/api/projects/{project['pid']}/setup").json()
    res = {}
    try:
        for mapping in ("plane", "unwrap"):
            setup = {**base, "detectors": [{**d, "mapping": mapping} for d in base["detectors"]]}
            assert client.put(f"/api/projects/{project['pid']}/setup", json=setup).status_code == 200
            runid = _run(client, project["a"], 60_000)
            r = client.get(f"/api/runs/{runid}/results").json()
            scene = client.get(f"/api/runs/{runid}/scene").json()
            res[mapping] = (r["detectors"][0], scene["detectors"][0])
    finally:
        client.put(f"/api/projects/{project['pid']}/setup", json=base)
    (dp, sp), (du, su) = res["plane"], res["unwrap"]
    assert sp["mode"] == 0 and su["mode"] == 1 and su["mesh"]["uv"]
    assert abs(dp["efficiency"] - du["efficiency"]) < 1e-9  # the same rays, the same exit power
    assert abs(dp["uExtentMm"] - du["uExtentMm"]) / dp["uExtentMm"] < 0.01
    prof_p = np.asarray(dp["luminance"]).mean(axis=0)
    prof_u = np.asarray(du["luminance"]).mean(axis=0)
    if len(prof_u) == len(prof_p):
        corr = max(np.corrcoef(prof_p, prof_u)[0, 1], np.corrcoef(prof_p, prof_u[::-1])[0, 1])
        assert corr > 0.97, corr


def test_live_preview_is_progressive_hidden_and_keepable(client, project):
    rid = project["a"]
    res = client.post(f"/api/revisions/{rid}/live", json={"rays": 60_000})
    assert res.status_code == 200, res.text
    first = res.json()
    assert first["settings"]["live"] is True
    # results can be read while the run is still tracing, and they grow
    seen: list[int] = []
    t0 = time.time()
    while time.time() - t0 < 300:
        r = client.get(f"/api/revisions/{rid}/live").json()["run"]
        assert r["id"] == first["id"]
        if r.get("live") and r["live"]["rays"] not in seen:
            seen.append(r["live"]["rays"])
            if r["status"] != "ready":
                got = client.get(f"/api/runs/{first['id']}/results")
                assert got.status_code == 200, got.text
                assert got.json()["detectors"][0]["efficiency"] > 0
        if r["status"] in ("ready", "failed", "cancelled"):
            break
        time.sleep(0.2)
    assert r["status"] == "ready", r.get("error")
    assert len(seen) >= 2 and seen == sorted(seen) and seen[-1] == 60_000
    # a live preview is not in the run list until it is kept
    assert first["id"] not in [x["id"] for x in client.get(f"/api/revisions/{rid}/runs").json()]
    # a restart replaces it
    second = client.post(f"/api/revisions/{rid}/live", json={"rays": 20_000}).json()
    assert second["id"] != first["id"]
    assert client.get(f"/api/runs/{first['id']}").status_code == 404
    done = _wait(client, f"/api/runs/{second['id']}")
    assert done["status"] == "ready"
    kept = client.post(f"/api/runs/{second['id']}/keep")
    assert kept.status_code == 200, kept.text
    assert second["id"] in [x["id"] for x in client.get(f"/api/revisions/{rid}/runs").json()]
    assert client.get(f"/api/revisions/{rid}/live").json()["run"] is None
    client.delete(f"/api/runs/{second['id']}")


def test_live_override_changes_the_result_without_touching_the_setup(client, project):
    rid = project["a"]
    before = client.get(f"/api/projects/{project['pid']}/setup").json()
    parts = client.get(f"/api/revisions/{rid}/parts").json()["instances"]
    pipe = next(p for p in parts if "pipe" in p["name"].lower())
    out = {}
    for tio2 in (0.01, 0.6):
        res = client.post(
            f"/api/revisions/{rid}/live",
            json={"rays": 30_000, "overrides": [{"parameter": "tio2_wt_pct", "part": pipe["name"], "value": tio2}]},
        )
        assert res.status_code == 200, res.text
        runid = res.json()["id"]
        assert _wait(client, f"/api/runs/{runid}")["status"] == "ready"
        out[tio2] = client.get(f"/api/runs/{runid}/results").json()["detectors"][0]["efficiency"]
    assert out[0.01] > 3 * out[0.6]  # heavy diffusion keeps the light from reaching the far end
    assert client.get(f"/api/projects/{project['pid']}/setup").json() == before
    bad = client.post(f"/api/revisions/{rid}/live", json={"overrides": [{"parameter": "tio2_wt_pct", "part": pipe["name"], "value": 900}]})
    assert bad.status_code == 400
    live = client.get(f"/api/revisions/{rid}/live").json()["run"]
    if live:
        client.delete(f"/api/runs/{live['id']}")


def test_live_view_frame(client, project):
    pytest.importorskip("mitsuba")
    rid = project["a"]
    runid = client.post(f"/api/revisions/{rid}/live", json={"rays": 20_000}).json()["id"]
    assert _wait(client, f"/api/runs/{runid}")["status"] == "ready"
    cam = {"position": [60, -80, 60], "target": [25, 0, 0], "up": [0, 0, 1], "fov": 40}
    body = {"camera": cam, "width": 320, "height": 200, "spp": 8}
    t0 = time.time()
    res = None
    while time.time() - t0 < 180:  # the first calls only start the renderer
        res = client.post(f"/api/runs/{runid}/live-frame", json=body)
        if res.headers["content-type"].startswith("image/"):
            break
        assert res.status_code == 202 and res.json()["status"] in ("starting", "waiting"), res.text
        time.sleep(1)
    assert res is not None and res.headers["content-type"] == "image/jpeg", res.text
    assert int(res.headers["x-spp"]) == 8
    more = client.post(f"/api/runs/{runid}/live-frame", json={**body, "spp": 8})
    assert int(more.headers["x-spp"]) == 16  # the same view keeps accumulating
    moved = client.post(f"/api/runs/{runid}/live-frame", json={**body, "camera": {**cam, "position": [70, -70, 50]}})
    assert int(moved.headers["x-spp"]) == 8  # a new view starts over
    import io as _io

    import numpy as np
    from PIL import Image

    img = np.asarray(Image.open(_io.BytesIO(more.content)).convert("L")).astype(float)
    assert img.max() > 60 and img.shape == (200, 320)  # the lit exit surface is in the picture
    # the full light transport: the LEDs light the whole model, independent of the tallies
    tbody = {**body, "mode": "transport", "spp": 32}
    t0 = time.time()
    while time.time() - t0 < 120:
        tr = client.post(f"/api/runs/{runid}/live-frame", json=tbody)
        if tr.headers["content-type"].startswith("image/"):
            break
        assert tr.status_code == 202, tr.text
        time.sleep(1)
    assert tr.headers["content-type"] == "image/jpeg", tr.text
    assert int(tr.headers["x-spp"]) == 32
    timg = np.asarray(Image.open(_io.BytesIO(tr.content)).convert("L")).astype(float)
    assert timg.shape == (200, 320) and timg.max() > 40  # the pipe glows
    # a high-resolution picture (split into passes on the GPU)
    big = client.post(f"/api/runs/{runid}/live-frame", json={**body, "width": 3200, "height": 1800, "spp": 2})
    assert big.status_code == 200 and int(big.headers["x-spp"]) == 2, big.text[:200]
    assert Image.open(_io.BytesIO(big.content)).size == (3200, 1800)
    assert client.post("/api/live-view/close").status_code == 200
    assert client.delete(f"/api/runs/{runid}").status_code == 200
    from lightsim import store

    assert not store.run_dir(rid, runid).exists()  # the helper let go of the folder

def test_run_stops_at_noise_target_and_reports_noise(client, project):
    res = client.post(f"/api/revisions/{project['a']}/runs", json={"settings": {"rays": 400_000, "target_noise": 0.5, "analysis": False}})
    assert res.status_code == 200, res.text
    runid = res.json()["id"]
    meta = _wait(client, f"/api/runs/{runid}")
    assert meta["status"] == "ready", meta.get("error")
    assert meta["raysTraced"] < 400_000  # a 50 % target is reached after the first pass
    assert meta["hasAnalysis"] is False
    det = client.get(f"/api/runs/{runid}/results").json()["detectors"][0]
    assert det["noise"] is not None and 0 < det["noise"]["median"] <= 0.5
    assert len(det["map"]) == det["nv"]
    bad = client.post(f"/api/revisions/{project['a']}/runs", json={"settings": {"target_noise": 0.001}})
    assert bad.status_code == 400


def test_results_at_another_viewing_cone(client, project):
    import os

    import numpy as np

    from lightsim import store

    runid = _run(client, project["a"], rays=60_000)
    url = f"/api/runs/{runid}/results"
    base = client.get(url).json()["detectors"][0]
    lo, hi = base["coneRange"]
    traced = base["tracedConeDeg"]
    assert base["coneDeg"] == traced and lo < traced < hi
    # the traced angle (asked for explicitly) is the default result
    same = client.get(url, params={"cone_deg": traced}).json()["detectors"][0]
    assert same["luminance"] == base["luminance"] and same["coneDeg"] == traced
    # another angle: luminance (flux per solid angle and area) changes, exit flux does not; a wider cone than the rings reach is limited
    wide = client.get(url, params={"cone_deg": hi}).json()["detectors"][0]
    assert wide["coneDeg"] == hi and wide["luminance"] != base["luminance"] and wide["exitLm"] == base["exitLm"]
    assert wide["noise"] is not None
    assert client.get(url, params={"cone_deg": 90}).json()["detectors"][0]["coneDeg"] == hi
    assert client.get(url, params={"cone_deg": 120}).status_code == 400
    # a run made before the rings existed ignores the angle
    path = store.run_dir(project["a"], runid) / "tallies.npz"
    with np.load(path) as z:
        old = {k: z[k] for k in z.files if k not in ("ring", "ring2")}
    np.savez_compressed(path, **old)
    os.utime(path, (time.time() + 5, time.time() + 5))  # the results are cached by modification time
    legacy = client.get(url, params={"cone_deg": hi}).json()["detectors"][0]
    assert legacy["coneRange"] is None and legacy["coneDeg"] == traced and legacy["luminance"] == base["luminance"]
    client.delete(f"/api/runs/{runid}")


def test_noise_target_run_keeps_analysis_with_a_final_pass(client, project):
    import numpy as np

    from lightsim import store
    from lightsim.analysis import ANALYSIS_FILE

    # stops after the first 250,000 rays; the analysis pass covers only the first 100,000, so its tables are scaled by 2.5
    res = client.post(
        f"/api/revisions/{project['a']}/runs",
        json={"settings": {"rays": 400_000, "target_noise": 0.5, "analysis": True, "analysis_rays": 100_000}},
    )
    assert res.status_code == 200, res.text
    runid = res.json()["id"]
    meta = _wait(client, f"/api/runs/{runid}")
    assert meta["status"] == "ready", meta.get("error")
    assert meta["raysTraced"] == 250_000
    assert meta["hasAnalysis"] is True
    rdir = store.run_dir(project["a"], runid)
    with np.load(rdir / "tallies.npz") as z:
        cone_y = z["cone"][..., 1].sum(axis=1)  # luminous weight inside the viewing cone, per channel, summed over 250,000 rays
    with np.load(rdir / ANALYSIS_FILE) as z:
        tot_y = z["tot_map"][:, 1].sum(axis=1)  # the same from the 100,000 re-traced rays, scaled up
        assert z["dep_rid"].max() < 100_000  # the ray logs are those of the first rays only
    assert tot_y == pytest.approx(cone_y, rel=0.1)
    # the analysis API answers for this run
    assert client.get(f"/api/runs/{runid}/analysis").status_code == 200

def test_one_channel_per_led_keeps_the_brightest_driven_channel():
    from types import SimpleNamespace as NS

    from lightsim.simrun import one_channel_per_led

    def ch(led, cid, flux):
        return NS(key=f"{led}/{cid}", led_id=led, flux_lm=flux)

    used = [ch("a", "R", 10), ch("a", "G", 30), ch("a", "B", 5), ch("b", "R", 10), ch("b", "G", 30), ch("b", "B", 5)]
    all_on = {"id": "all", "drive": {k.key: 1.0 for k in used}}
    kept = one_channel_per_led(used, [all_on])
    assert [s.key for s in kept] == ["a/G", "b/G"]  # the brightest of each LED, in the original order
    # drive counts too: with the green channels dimmed to a tenth, red is the one that carries the light
    dimmed = {"id": "dim", "drive": {**all_on["drive"], "a/G": 0.1, "b/G": 0.1}}
    assert [s.key for s in one_channel_per_led(used, [dimmed])] == ["a/R", "b/R"]
    # a channel that no state drives is not a candidate
    off = {"id": "off", "drive": {**all_on["drive"], "a/G": 0.0}}
    assert [s.key for s in one_channel_per_led(used, [off])] == ["a/R", "b/G"]


def test_one_channel_per_led_run_completes(client, project):
    res = client.post(f"/api/revisions/{project['a']}/runs", json={"settings": {"rays": 20_000, "one_channel_per_led": True}})
    assert res.status_code == 200, res.text
    runid = res.json()["id"]
    meta = _wait(client, f"/api/runs/{runid}")
    assert meta["status"] == "ready", meta.get("error")
    assert meta["settings"]["one_channel_per_led"] is True
    assert len(client.get(f"/api/runs/{runid}/scene").json()["channels"]) == 5  # the test LEDs have a single channel each


def test_solvers_endpoint_and_validation(client, project):
    doc = client.get("/api/solvers").json()
    by_id = {s["id"]: s for s in doc["solvers"]}
    assert by_id["cpu"]["available"] is True and "gpu" in by_id
    res = client.post(f"/api/revisions/{project['a']}/runs", json={"settings": {"rays": 10_000, "solver": "quantum"}})
    assert res.status_code == 400


def test_gpu_run_matches_the_cpu_run_and_keeps_analysis(client, project):
    from lightsim.engine import solvers

    ok, why = solvers.gpu_status()
    if not ok:
        pytest.skip(f"no usable GPU solver: {why}")

    def run(solver):
        res = client.post(
            f"/api/revisions/{project['a']}/runs",
            json={"settings": {"rays": 200_000, "solver": solver, "analysis": True, "analysis_rays": 50_000, "seed": 4 if solver == "gpu" else 9}},
        )
        assert res.status_code == 200, res.text
        runid = res.json()["id"]
        meta = _wait(client, f"/api/runs/{runid}")
        assert meta["status"] == "ready", meta.get("error")
        return runid, meta

    gid, gmeta = run("gpu")
    cid, cmeta = run("cpu")
    assert gmeta["solver"] == "gpu" and gmeta["solverName"] and cmeta["solver"] == "cpu"
    assert gmeta["hasAnalysis"] is True  # a CPU pass over the first 50,000 rays, scaled
    assert client.get(f"/api/runs/{gid}/analysis").status_code == 200
    g = client.get(f"/api/runs/{gid}/results").json()
    c = client.get(f"/api/runs/{cid}/results").json()
    assert abs(g["lossSum"] - 1.0) < 1e-3
    assert g["detectors"][0]["efficiency"] == pytest.approx(c["detectors"][0]["efficiency"], rel=0.03)
    assert g["detectors"][0]["stats"]["mean"] == pytest.approx(c["detectors"][0]["stats"]["mean"], rel=0.05)

def test_live_preview_on_the_gpu(client, project):
    from lightsim.engine import solvers

    ok, why = solvers.gpu_status()
    if not ok:
        pytest.skip(f"no usable GPU solver: {why}")
    rid = project["a"]
    res = client.post(f"/api/revisions/{rid}/live", json={"rays": 200_000, "solver": "gpu"})
    assert res.status_code == 200, res.text
    first = res.json()
    assert first["settings"]["solver"] == "gpu"
    done = _wait(client, f"/api/runs/{first['id']}")
    assert done["status"] == "ready", done.get("error")
    assert done["solver"] == "gpu" and done["raysTraced"] == 200_000
    got = client.get(f"/api/runs/{first['id']}/results").json()
    assert got["detectors"][0]["efficiency"] > 0 and abs(got["lossSum"] - 1.0) < 1e-3
    client.delete(f"/api/runs/{first['id']}")
