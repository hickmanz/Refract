"""Designs and variants: legacy migration, per-variant setups, setup reconcile and comparison across variants."""

import time

import pytest
from fastapi.testclient import TestClient

from conftest import SAMPLES
from refract import setup_suggest, store


@pytest.fixture(scope="module")
def client():
    from refract.app import app

    return TestClient(app)


def _wait(client, url, done=("ready", "failed", "cancelled"), timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        meta = client.get(url).json()
        if meta["status"] in done:
            return meta
        time.sleep(0.5)
    raise AssertionError(f"timeout waiting for {url}")


def _upload(client, name, **form):
    with open(SAMPLES / name, "rb") as fh:
        res = client.post("/api/revisions", files={"file": (name, fh)}, data={"project_name": "Variants", **form})
    assert res.status_code == 200, res.text
    rid, pid = res.json()["id"], res.json()["projectId"]
    assert _wait(client, f"/api/revisions/{rid}")["status"] == "ready"
    return rid, pid


# -- storage ------------------------------------------------------------------------------
def _fake_rev(pid, vid, created, **extra):
    """A revision without CAD files: enough for the project / variant bookkeeping."""
    rid = store.new_id()
    store.save_rev(rid, {"id": rid, "filename": "x.step", "created": created, "status": "ready", "projectId": pid, **extra})
    store.add_revision(pid, rid, vid)
    return rid


def test_legacy_project_is_migrated_to_one_design_with_one_variant():
    pid, rid = "a1b2c3d4e5f6", "f6e5d4c3b2a1"
    store.write_json(store.project_dir(pid) / "project.json", {"id": pid, "name": "Old", "stem": "old", "created": 1.0, "lastOpened": 1.0, "revisions": [rid]})
    store.write_json(store.project_dir(pid) / "setup.json", {"parts": [], "leds": [{"id": "led1"}], "states": [], "detectors": []})
    store.save_rev(rid, {"id": rid, "filename": "old.step", "created": 1.0, "status": "ready", "projectId": pid})
    try:
        project = store.load_project(pid)
        assert project["schema"] == store.PROJECT_SCHEMA
        assert [d["name"] for d in project["designs"]] == ["Baseline"] and len(project["variants"]) == 1
        v = project["variants"][0]
        assert v["name"] == "Default" and v["designId"] == project["designs"][0]["id"] and "revisions" not in v  # membership is each revision's variantId
        assert store.load_setup(pid)["leds"] == [{"id": "led1"}]
        assert store.load_setup(pid, v["id"])["leds"] == [{"id": "led1"}]
        assert store.load_rev(rid)["variantId"] == v["id"] and store.load_rev(rid)["number"] == 1
        assert store.load_setup_for_rev(store.load_rev(rid))["leds"] == [{"id": "led1"}]
        assert store.load_project(pid)["variants"][0]["id"] == v["id"]  # migrating twice changes nothing
    finally:
        store.delete_project(pid)
        store.delete_revision(rid)


def test_variants_forked_from_each_other_are_grouped_in_the_design_of_their_root():
    """Projects from the time when variants were branches (parentId / forkRevisionId) get designs."""
    pid = "d1d2d3d4d5d6"
    ids = {"root": "e0e0e0e0e0e1", "child": "e0e0e0e0e0e2", "grand": "e0e0e0e0e0e3", "other": "e0e0e0e0e0e4"}
    mk = lambda name, created, parent=None: {"id": ids[name], "name": name, "created": created, "parentId": parent, "forkRevisionId": None, "nextRev": 2}  # noqa: E731
    store.write_json(
        store.project_dir(pid) / "project.json",
        {
            "id": pid, "name": "Tree", "stem": "tree", "created": 1.0, "lastOpened": 1.0, "schema": 2, "revisions": [],
            "variants": [mk("root", 1.0), mk("child", 2.0, ids["root"]), mk("grand", 3.0, ids["child"]), mk("other", 4.0)],
        },
    )
    try:
        project = store.load_project(pid)
        assert project["schema"] == store.PROJECT_SCHEMA and [d["name"] for d in project["designs"]] == ["root", "other"]
        design_of = {v["id"]: v["designId"] for v in project["variants"]}
        root_design, other_design = project["designs"][0]["id"], project["designs"][1]["id"]
        assert [design_of[ids[k]] for k in ("root", "child", "grand", "other")] == [root_design, root_design, root_design, other_design]
        names = {v["id"]: v["name"] for v in project["variants"]}
        assert names[ids["root"]] == "Default" and names[ids["other"]] == "Default" and names[ids["child"]] == "child"  # a design's first variant is "Default"
        assert all("parentId" not in v and "forkRevisionId" not in v for v in project["variants"])
        assert store.load_project(pid) == project  # migrating twice changes nothing
    finally:
        store.delete_project(pid)


def test_variant_crud_and_independent_setups(client):
    pid = client.post("/api/projects", json={"name": "Crud"}).json()["id"]
    try:
        project = client.get(f"/api/projects/{pid}").json()
        assert [d["name"] for d in project["designs"]] == ["Baseline"] and [v["name"] for v in project["variants"]] == ["Default"]
        base, design = project["variants"][0]["id"], project["designs"][0]["id"]
        setup = {"parts": [], "leds": [{"id": "led1", "name": "LED:1"}], "states": [], "detectors": []}
        assert client.put(f"/api/projects/{pid}/variants/{base}/setup", json=setup).status_code == 200

        project = client.post(f"/api/projects/{pid}/variants", json={"name": "Pitch 8", "copyFrom": base}).json()
        other = next(v for v in project["variants"] if v["name"] == "Pitch 8")
        assert other["designId"] == design  # a copy goes into the design of the variant it is copied from
        assert client.get(f"/api/projects/{pid}/variants/{other['id']}/setup").json() == setup  # started as a copy
        client.put(f"/api/projects/{pid}/variants/{other['id']}/setup", json={**setup, "leds": []})
        assert client.get(f"/api/projects/{pid}/variants/{base}/setup").json() == setup  # the other variant is untouched
        assert store.load_setup(pid) == setup  # no variant given: the project's first one

        assert client.patch(f"/api/projects/{pid}/variants/{other['id']}", json={"name": "Pitch 9"}).json()["variants"][1]["name"] == "Pitch 9"
        assert client.post(f"/api/projects/{pid}/variants", json={"copyFrom": "nope"}).status_code == 404
        assert client.post(f"/api/projects/{pid}/variants", json={"designId": "nope"}).status_code == 404
        assert client.delete(f"/api/projects/{pid}/variants/{other['id']}").status_code == 200
        assert client.get(f"/api/projects/{pid}/variants/{other['id']}/setup").status_code == 404
        assert client.delete(f"/api/projects/{pid}/variants/{base}").status_code == 400  # the last variant of a design stays
    finally:
        client.delete(f"/api/projects/{pid}")


def test_designs_group_variants(client):
    pid = client.post("/api/projects", json={"name": "Designs"}).json()["id"]
    try:
        project = client.get(f"/api/projects/{pid}").json()
        base, d1 = project["variants"][0]["id"], project["designs"][0]["id"]
        setup = {"parts": [], "leds": [{"id": "led1"}], "states": [], "detectors": []}
        client.put(f"/api/projects/{pid}/variants/{base}/setup", json=setup)
        r1 = _fake_rev(pid, base, 1.0)

        # a new design has one variant, starting with a copy of the chosen setup
        project = client.post(f"/api/projects/{pid}/designs", json={"name": "Dome reflector", "variantName": "Thin", "copyFrom": base}).json()
        assert [d["name"] for d in project["designs"]] == ["Baseline", "Dome reflector"]
        dome = project["designs"][1]["id"]
        thin = next(v for v in project["variants"] if v["designId"] == dome)
        assert thin["name"] == "Thin" and thin["revisions"] == []
        assert client.get(f"/api/projects/{pid}/variants/{thin['id']}/setup").json() == setup
        assert client.post(f"/api/projects/{pid}/designs", json={"copyFrom": "nope"}).status_code == 404

        # more variants in the design, and the default design of a variant given only a copy source
        project = client.post(f"/api/projects/{pid}/variants", json={"name": "Thick", "designId": dome, "copyFrom": base}).json()
        assert sorted(v["name"] for v in project["variants"] if v["designId"] == dome) == ["Thick", "Thin"]
        assert next(v for v in project["variants"] if v["name"] == "Thick")["designId"] == dome  # designId wins over the copy source's design

        # rename and move
        assert client.patch(f"/api/projects/{pid}/designs/{dome}", json={"name": "Dome"}).json()["designs"][1]["name"] == "Dome"
        project = client.patch(f"/api/projects/{pid}/variants/{thin['id']}", json={"designId": d1}).json()
        assert next(v for v in project["variants"] if v["id"] == thin["id"])["designId"] == d1
        project = client.patch(f"/api/projects/{pid}/variants/{base}", json={"designId": dome}).json()  # Baseline keeps Thin, so it stays
        assert [d["name"] for d in project["designs"]] == ["Baseline", "Dome"]
        project = client.patch(f"/api/projects/{pid}/variants/{thin['id']}", json={"designId": dome}).json()  # now Baseline is empty
        assert [d["name"] for d in project["designs"]] == ["Dome"]  # an empty design is removed
        assert client.patch(f"/api/projects/{pid}/variants/{base}", json={"designId": "nope"}).status_code == 404

        # deleting a design takes its variants and revisions along; the last design stays
        project = client.post(f"/api/projects/{pid}/designs", json={"name": "Spare"}).json()
        spare = next(d["id"] for d in project["designs"] if d["name"] == "Spare")
        assert client.delete(f"/api/projects/{pid}/designs/{dome}").status_code == 200
        assert store.load_rev(r1) is None and [d["name"] for d in store.load_project(pid)["designs"]] == ["Spare"]
        assert client.delete(f"/api/projects/{pid}/designs/{spare}").status_code == 400
    finally:
        client.delete(f"/api/projects/{pid}")


def test_revision_numbers_are_stored_and_stable(client):
    project = store.create_project("Numbers")
    pid, base = project["id"], project["variants"][0]["id"]
    try:
        r1, r2 = _fake_rev(pid, base, 1.0), _fake_rev(pid, base, 2.0)
        rm = _fake_rev(pid, base, 3.0, remeshedFrom=r2, meshQuality="fine")
        r3 = _fake_rev(pid, base, 4.0)
        number = lambda rid: store.load_rev(rid).get("number")  # noqa: E731
        assert (number(r1), number(r2), number(rm), number(r3)) == (1, 2, None, 3)  # a re-mesh is not numbered

        assert store.revision_name(store.load_rev(rm), store.load_rev(r2)) == "fine mesh of Rev 2"
        store.delete_revision(r2)
        r4 = _fake_rev(pid, base, 5.0)
        assert (number(r1), number(r3), number(r4)) == (1, 3, 4)  # nothing is renumbered, a number is not reused

        other = store.create_variant(pid, "Other", project["designs"][0]["id"])
        assert number(_fake_rev(pid, other["id"], 6.0)) == 1  # numbers count within a variant

        doc = client.get(f"/api/projects/{pid}").json()
        first = doc["variants"][0]
        assert first["revisions"] == [r1, rm, r3, r4] and first["latest"]["id"] == r4  # derived from each revision's variantId
        assert [r["id"] for r in doc["revisionList"] if r["variantId"] == base] == [r4, r3, rm, r1]  # newest first
        assert store.revision_name(store.load_rev(r3)) == "Rev 3"
    finally:
        store.delete_project(pid)


def test_old_projects_are_migrated_to_the_current_schema():
    pid, v1, v2 = "c1d2e3f4a5b6", "b1b1b1b1b1b1", "b2b2b2b2b2b2"
    r1, r2, r3 = "a0a0a0a0a0a1", "a0a0a0a0a0a2", "a0a0a0a0a0a3"
    store.write_json(
        store.project_dir(pid) / "project.json",
        {
            "id": pid, "name": "Mid", "stem": "mid", "created": 1.0, "lastOpened": 1.0,
            "revisions": [r1, r2, r3],
            "variants": [
                {"id": v1, "name": "Baseline", "created": 1.0, "revisions": [r1, r3]},  # the old per-variant lists
                {"id": v2, "name": "Alt", "created": 2.0, "revisions": [r2]},
            ],
        },
    )
    base = {"filename": "m.step", "status": "ready", "projectId": pid}
    store.save_rev(r1, {**base, "id": r1, "created": 1.0})  # no variantId yet
    store.save_rev(r2, {**base, "id": r2, "created": 2.0, "variantId": v2})
    store.save_rev(r3, {**base, "id": r3, "created": 3.0, "remeshedFrom": r1})
    try:
        project = store.load_project(pid)
        assert project["schema"] == store.PROJECT_SCHEMA
        assert all("revisions" not in v for v in project["variants"])
        assert [d["name"] for d in project["designs"]] == ["Baseline", "Alt"]  # each separate variant became a design
        assert [(v["designId"], v["nextRev"]) for v in project["variants"]] == [(project["designs"][0]["id"], 2), (project["designs"][1]["id"], 2)]
        assert (store.load_rev(r1)["variantId"], store.load_rev(r1)["number"]) == (v1, 1)
        assert (store.load_rev(r2)["variantId"], store.load_rev(r2)["number"]) == (v2, 1)
        assert store.load_rev(r3)["variantId"] == v1 and "number" not in store.load_rev(r3)  # a re-mesh is not numbered
        assert [m["id"] for m in store.variant_revisions(project, v1)] == [r1, r3]

        before = (store.load_project(pid), [store.load_rev(r) for r in (r1, r2, r3)])
        assert (store.load_project(pid), [store.load_rev(r) for r in (r1, r2, r3)]) == before  # migrating twice changes nothing
    finally:
        store.delete_project(pid)
        for rid in (r1, r2, r3):
            store.delete_revision(rid)


# -- reconcile ----------------------------------------------------------------------------
def _inst(name, ordinal):
    return {"ordinal": ordinal, "id": f"i{ordinal}", "name": name, "path": f"asm/{name}", "partName": name}


def _sug_led(k, part):
    sig = {"centroid": [10.0 * k, 0, 0], "normal": [0, 0, 1], "area": 4}
    return {"id": f"led{k}", "name": part, "model": "white_generic", "placement": {"type": "face", "part": part, "face": 5, "sig": sig}}


def test_reconcile_follows_a_changed_led_count():
    old = {
        "parts": [{"match": "*", "finish": "black"}, {"match": "Light Pipe:1", "material": "pc_tio2_0p03", "finish": "polished"}],
        "leds": [{**_sug_led(k, f"LED:{k}"), "model": "custom_led"} for k in (1, 2, 3, 4, 5)],
        "detectors": [{"id": "exit", "name": "Exit", "faces": [{"part": "Light Pipe:1", "face": 4}]}],
        "states": [{"id": "all", "name": "All", "drive": {"*": 1.0}}, {"id": "only_led5", "name": "LED:5 only", "drive": {"led5/*": 1.0}}, {"id": "dim", "name": "Dim", "drive": {"*": 0.5}}],
    }
    # the new geometry has 3 LEDs, one of them new (LED:6), plus a new reflector part
    insts = [_inst("Light Pipe:1", 0), _inst("LED:1", 1), _inst("LED:2", 2), _inst("LED:6", 3), _inst("New Reflector:1", 4)]
    suggestion = {
        "parts": [{"match": "*", "finish": "black"}, {"match": "New Reflector:1", "finish": "white_diffuse"}, {"match": "Light Pipe:1", "material": "pc_tio2_0p03"}],
        "leds": [_sug_led(1, "LED:1"), _sug_led(2, "LED:2"), _sug_led(3, "LED:6")],
        "detectors": [],
        "states": [],
    }
    new, warnings = setup_suggest.reconcile(old, suggestion, insts)

    assert [l["name"] for l in new["leds"]] == ["LED:1", "LED:2", "LED:6"]
    assert {l["model"] for l in new["leds"]} == {"custom_led"}  # the model the others use, not the suggestion's default
    assert len({l["id"] for l in new["leds"]}) == 3
    ids = {s["id"] for s in new["states"]}
    assert {"all", "dim"} <= ids  # kept
    assert "only_led5" not in ids  # its LED is gone
    assert {f"only_{l['id']}" for l in new["leds"]} <= ids  # regenerated for the LEDs that exist now
    assert any(r["match"] == "New Reflector:1" for r in new["parts"])  # the new part got its suggested rule
    assert [r for r in new["parts"] if r["match"] == "Light Pipe:1"] == [old["parts"][1]]  # the user's rule is kept
    assert any("LED:3" in w for w in warnings) and any("LED:6" in w for w in warnings)
    assert old["leds"][2]["name"] == "LED:3"  # the input is not modified


def test_reconcile_replaces_a_detector_whose_part_is_gone():
    old = {"parts": [], "leds": [], "states": [], "detectors": [{"id": "exit", "name": "Exit", "faces": [{"part": "Old Pipe", "face": 1}]}]}
    insts = [_inst("New Pipe", 0)]
    sug_det = {"id": "exit", "name": "Exit surface", "faces": [{"part": "New Pipe", "face": 3}]}
    new, warnings = setup_suggest.reconcile(old, {"parts": [], "leds": [], "detectors": [sug_det], "states": []}, insts)
    assert new["detectors"][0]["faces"] == [{"part": "New Pipe", "face": 3}]
    assert any("Detector" in w for w in warnings)


# -- end to end: variants on real geometry and comparison across them --------------------------
@pytest.fixture(scope="module")
def two_variants(client):
    rid_a, pid = _upload(client, "lightbar_a.step")
    sug = client.post(f"/api/revisions/{rid_a}/suggest-setup").json()
    setup = {k: sug[k] for k in ("parts", "leds", "detectors", "states")}
    base = client.get(f"/api/projects/{pid}").json()["variants"][0]["id"]
    assert client.put(f"/api/projects/{pid}/variants/{base}/setup", json=setup).status_code == 200

    rid_b, pid_b = _upload(client, "lightbar_b.step", project_id=pid, variant_id="new", variant_name="Alternative", copy_setup_from=base)
    assert pid_b == pid
    yield {"pid": pid, "a": rid_a, "b": rid_b, "base": base, "setup": setup}
    client.delete(f"/api/projects/{pid}")


def test_upload_into_a_new_variant(client, two_variants):
    project = client.get(f"/api/projects/{two_variants['pid']}").json()
    assert [v["name"] for v in project["variants"]] == ["Default", "Alternative"]
    alt = project["variants"][1]
    assert alt["designId"] == project["variants"][0]["designId"] and len(project["designs"]) == 1  # same design
    assert alt["revisions"] == [two_variants["b"]] and project["variants"][0]["revisions"] == [two_variants["a"]]
    assert client.get(f"/api/revisions/{two_variants['b']}").json()["variantId"] == alt["id"]
    assert client.get(f"/api/projects/{two_variants['pid']}/variants/{alt['id']}/setup").json() == two_variants["setup"]
    # a revision uploaded into a given variant joins it
    rid_c, _ = _upload(client, "lightbar_a.step", project_id=two_variants["pid"], variant_id=alt["id"])
    project = client.get(f"/api/projects/{two_variants['pid']}").json()
    assert project["variants"][1]["revisions"] == [two_variants["b"], rid_c]
    bad = client.post("/api/revisions", files={"file": ("x.step", b"x")}, data={"project_id": two_variants["pid"], "variant_id": "nope"})
    assert bad.status_code == 404
    data = {"project_id": two_variants["pid"], "variant_id": alt["id"], "design_id": "new"}  # a design only goes with a new variant
    assert client.post("/api/revisions", files={"file": ("x.step", b"x")}, data=data).status_code == 400
    data = {"project_id": two_variants["pid"], "variant_id": "new", "design_id": "nope"}
    assert client.post("/api/revisions", files={"file": ("x.step", b"x")}, data=data).status_code == 404


def test_duplicate_a_revision_as_a_new_variant_without_uploading(client, two_variants):
    pid, rid = two_variants["pid"], two_variants["a"]
    assert client.post("/api/revisions/nope/duplicate", json={}).status_code == 404
    res = client.post(f"/api/revisions/{rid}/duplicate", json={"name": "Other TiO2"})
    assert res.status_code == 200, res.text
    new_rid, new_vid = res.json()["id"], res.json()["variantId"]
    assert _wait(client, f"/api/revisions/{new_rid}")["status"] == "ready"

    project = client.get(f"/api/projects/{pid}").json()
    new_variant = next(v for v in project["variants"] if v["id"] == new_vid)
    assert new_variant["name"] == "Other TiO2" and new_variant["designId"] == project["variants"][0]["designId"]  # stays in the design
    assert new_variant["revisions"] == [new_rid]
    meta, source = client.get(f"/api/revisions/{new_rid}").json(), client.get(f"/api/revisions/{rid}").json()
    assert meta["variantId"] == new_vid and meta["number"] == 1 and meta["triangles"] == source["triangles"]
    assert client.get(f"/api/revisions/{new_rid}/runs").json() == []  # the runs are not copied
    # the setup is a copy: editing it leaves the original alone
    copy = client.get(f"/api/projects/{pid}/variants/{new_vid}/setup").json()
    assert copy == two_variants["setup"]
    client.put(f"/api/projects/{pid}/variants/{new_vid}/setup", json={**copy, "leds": []})
    assert client.get(f"/api/projects/{pid}/variants/{two_variants['base']}/setup").json() == two_variants["setup"]
    assert client.delete(f"/api/projects/{pid}/variants/{new_vid}").status_code == 200


def test_reconcile_endpoint_keeps_a_valid_setup(client, two_variants):
    res = client.post(f"/api/revisions/{two_variants['b']}/reconcile-setup")
    assert res.status_code == 200, res.text
    out = res.json()
    assert not out["fromSuggestion"] and out["setup"]["leds"] and out["setup"]["detectors"]
    check = client.post(f"/api/revisions/{two_variants['b']}/check", json=out["setup"])
    assert check.status_code == 200, check.text


def _run(client, rid, rays=20_000):
    res = client.post(f"/api/revisions/{rid}/runs", json={"settings": {"rays": rays}})
    assert res.status_code == 200, res.text
    runid = res.json()["id"]
    assert _wait(client, f"/api/runs/{runid}")["status"] == "ready"
    return runid


def test_runs_use_their_variants_setup_and_compare_across_variants(client, two_variants):
    pid = two_variants["pid"]
    alt = client.get(f"/api/projects/{pid}").json()["variants"][1]["id"]
    # the alternative loses the per-LED states: only "All LEDs on" is left
    reduced = {**two_variants["setup"], "states": [s for s in two_variants["setup"]["states"] if s["id"] == "all"]}
    assert client.put(f"/api/projects/{pid}/variants/{alt}/setup", json=reduced).status_code == 200

    ra, rb = _run(client, two_variants["a"]), _run(client, two_variants["b"])
    a, b = client.get(f"/api/runs/{ra}").json(), client.get(f"/api/runs/{rb}").json()
    assert a["variantId"] == two_variants["base"] and b["variantId"] == alt
    # each run carries the states of its own variant's setup
    assert client.get(f"/api/runs/{ra}/results", params={"state": "only_led1"}).json()["state"]["id"] == "only_led1"
    assert [s["id"] for s in client.get(f"/api/runs/{rb}/scene").json()["states"]] == ["all"]

    cmp = client.get(f"/api/projects/{pid}/compare", params={"runs": f"{ra},{rb}", "state": "only_led1"})
    assert cmp.status_code == 200, cmp.text
    doc = cmp.json()
    assert [r["runId"] for r in doc["runs"]] == [ra, rb] and doc["unit"]
    first, second = doc["runs"]
    assert first["variant"] == "Default" and second["variant"] == "Alternative"
    assert first["design"] == second["design"] == "Baseline"
    assert first["state"]["id"] == "only_led1" and not first["stateFallback"]
    assert second["state"]["id"] == "all" and second["stateFallback"]  # falls back to "all" for the variant that lacks the state
    assert first["efficiency"] > 0 and second["efficiency"] > 0 and second["uExtentMm"] > 0

    assert client.get(f"/api/projects/{pid}/compare", params={"runs": ra}).status_code == 400
    assert client.get(f"/api/projects/{pid}/compare", params={"runs": f"{ra},{ra}"}).status_code == 400
    assert client.get(f"/api/projects/{pid}/compare", params={"runs": f"{ra},nope"}).status_code == 404
    assert client.get(f"/api/projects/{pid}/compare", params={"runs": f"{ra},{rb}", "detector": 5}).json()["runs"][0]["error"]


def test_overview_lists_finished_runs_by_design_and_variant_with_the_energy_budget(client, two_variants):
    pid = two_variants["pid"]
    alt = client.get(f"/api/projects/{pid}").json()["variants"][1]["id"]
    ra, rb = _run(client, two_variants["a"]), _run(client, two_variants["b"])

    doc = client.get(f"/api/projects/{pid}/overview")
    assert doc.status_code == 200, doc.text
    doc = doc.json()
    assert [d["name"] for d in doc["designs"]] == ["Baseline"] and {v["id"] for v in doc["variants"]} >= {two_variants["base"], alt}
    by_id = {r["runId"]: r for r in doc["runs"]}
    assert {ra, rb} <= set(by_id)
    assert by_id[ra]["variantId"] == two_variants["base"] and by_id[ra]["design"] == "Baseline" and by_id[ra]["variant"] == "Default"
    assert by_id[rb]["variantId"] == alt and by_id[rb]["variant"] == "Alternative"
    for runid in (ra, rb):
        r = by_id[runid]
        assert r["mesh"]["quality"] == "normal" and r["rays"] == 20_000 and r["revision"]
        state = r["summary"]["states"][0]
        assert state["detectors"][0]["efficiency"] > 0
        loss = {l["kind"]: l["fraction"] for l in state["loss"]}
        assert "exit" in loss and loss["exit"] == pytest.approx(state["detectors"][0]["efficiency"], abs=0.05)

    # a run saved before summaries carried the energy budget is filled in on the way, and saved again
    run = store.load_run(by_id[ra]["revisionId"], ra)
    for s in run["summary"]["states"]:
        s.pop("loss")
    store.save_run(by_id[ra]["revisionId"], ra, run)
    again = {r["runId"]: r for r in client.get(f"/api/projects/{pid}/overview").json()["runs"]}
    assert all(s["loss"] for s in again[ra]["summary"]["states"])
    assert all("loss" in s for s in store.load_run(by_id[ra]["revisionId"], ra)["summary"]["states"])

    assert client.get("/api/projects/aaaaaaaaaaaa/overview").status_code == 404


def test_upload_as_a_new_design(client, two_variants):
    pid = two_variants["pid"]
    rid, pid2 = _upload(client, "lightbar_b.step", project_id=pid, variant_id="new", design_id="new", design_name="Dome reflector", copy_setup_from=two_variants["base"])
    assert pid2 == pid
    project = client.get(f"/api/projects/{pid}").json()
    assert [d["name"] for d in project["designs"]] == ["Baseline", "Dome reflector"]
    meta = client.get(f"/api/revisions/{rid}").json()
    variant = next(v for v in project["variants"] if v["id"] == meta["variantId"])
    assert variant["name"] == "Default" and variant["designId"] == project["designs"][1]["id"] and variant["revisions"] == [rid]
    assert client.get(f"/api/projects/{pid}/variants/{variant['id']}/setup").json() == two_variants["setup"]
    assert client.delete(f"/api/projects/{pid}/designs/{variant['designId']}").status_code == 200
