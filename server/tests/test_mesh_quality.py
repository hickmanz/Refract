"""Per-part optical mesh tolerance, mesh quality presets, re-mesh as a new revision and the optical-mesh endpoint."""

import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import SAMPLES
from refract.optical_mesh import MIN_DEFL, facet_angles_deg, part_deflection


def test_part_deflection_is_never_coarser_than_the_assembly():
    small = np.array([[0.0, 0.0, 0.0], [50.0, 6.0, 4.0]])  # a light pipe: thinnest extent 4 mm
    big = np.array([[0.0, 0.0, 0.0], [500.0, 60.0, 40.0]])
    assert part_deflection(0.147, small) == pytest.approx(0.04)
    assert part_deflection(0.147, big) == pytest.approx(0.147)
    assert part_deflection(0.147, small, 0.25) == pytest.approx(0.01)
    assert part_deflection(0.147, np.array([[0, 0, 0], [1e-4, 1, 1]])) == MIN_DEFL
    assert part_deflection(0.147, None) == pytest.approx(0.147)


def test_facet_angles():
    verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
    flat = np.tile([0.0, 0.0, 1.0], (3, 1)).astype(np.float32)
    tris = np.array([[0, 1, 2]])
    assert facet_angles_deg(verts, flat, tris)[0] == pytest.approx(0.0, abs=1e-3)
    tilted = flat.copy()
    tilted[1] = [np.sin(np.radians(5)), 0, np.cos(np.radians(5))]
    assert facet_angles_deg(verts, tilted, tris)[0] == pytest.approx(5.0, abs=1e-3)


@pytest.fixture(scope="module")
def client():
    from refract.app import app

    return TestClient(app)


def _wait(client, rid, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        meta = client.get(f"/api/revisions/{rid}").json()
        if meta["status"] in ("ready", "failed", "cancelled"):
            return meta
        time.sleep(0.5)
    raise AssertionError("import timed out")


def test_remesh_creates_a_finer_revision_and_serves_the_mesh(client):
    with open(SAMPLES / "lightbar_a.step", "rb") as fh:
        res = client.post("/api/revisions", files={"file": ("lightbar_a.step", fh)}, data={"project_id": "new", "project_name": "Mesh"})
    rid, pid = res.json()["id"], res.json()["projectId"]
    assert _wait(client, rid)["status"] == "ready"
    normal = client.get(f"/api/revisions/{rid}/parts").json()
    assert normal["quality"] == "normal"
    assert all(i["deflection"] <= normal["deflection"] for i in normal["instances"])

    assert client.post(f"/api/revisions/{rid}/remesh", json={"quality": "nonsense"}).status_code == 400
    res = client.post(f"/api/revisions/{rid}/remesh", json={"quality": "fine"})
    assert res.status_code == 200, res.text
    fine_rid = res.json()["id"]
    meta = _wait(client, fine_rid)
    assert meta["status"] == "ready", meta.get("error")
    assert meta["meshQuality"] == "fine" and meta["remeshedFrom"] == rid and meta["variantId"]
    fine = client.get(f"/api/revisions/{fine_rid}/parts").json()
    assert fine["quality"] == "fine" and fine["triangles"] > normal["triangles"]
    assert [i["faceCount"] for i in fine["instances"]] == [i["faceCount"] for i in normal["instances"]]  # same CAD faces

    project = client.get(f"/api/projects/{pid}").json()
    assert set(project["variants"][0]["revisions"]) == {rid, fine_rid}

    pipe = next(i for i in fine["instances"] if i["name"] == "Light Pipe:1")
    body = client.get(f"/api/revisions/{fine_rid}/optical-mesh", params={"instances": pipe["id"]}).content
    nv, nt = np.frombuffer(body[:8], dtype="<u4")
    assert nt == pipe["triCount"] and nv == pipe["vertCount"]
    assert len(body) == 8 + nv * 12 + nt * 12 + nt * 4
    idx = np.frombuffer(body[8 + nv * 12 : 8 + nv * 12 + nt * 12], dtype="<u4")
    assert idx.max() < nv
    assert client.get(f"/api/revisions/{fine_rid}/optical-mesh", params={"instances": "nope"}).status_code == 404
    assert client.delete(f"/api/projects/{pid}").json()["ok"]
