"""STEP upload -> import job -> viewer files + optical mesh, through the real HTTP API and job process."""

import time

import pytest
from fastapi.testclient import TestClient

from conftest import SAMPLES


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


def test_upload_import_and_parts(client):
    with open(SAMPLES / "lightbar_a.step", "rb") as fh:
        res = client.post("/api/revisions", files={"file": ("lightbar_a.step", fh)}, data={"project_id": "new", "project_name": "Bar"})
    assert res.status_code == 200, res.text
    rid, pid = res.json()["id"], res.json()["projectId"]
    meta = _wait(client, rid)
    assert meta["status"] == "ready", meta.get("error")
    assert meta["model_ready"]

    parts = client.get(f"/api/revisions/{rid}/parts").json()
    names = [i["name"] for i in parts["instances"]]
    assert "Light Pipe:1" in names and names.count("LED:1") == 1
    assert len(parts["instances"]) == 8
    assert all(i["closed"] for i in parts["instances"])

    assert client.get(f"/api/revisions/{rid}/files/model.glb").status_code == 200

    pipe = next(i for i in parts["instances"] if i["name"] == "Light Pipe:1")
    faces = [client.get(f"/api/revisions/{rid}/faces/{pipe['id']}/{f}").json() for f in range(pipe["faceCount"])]
    top = max(faces, key=lambda f: f["centroid"][2] + 0.01 * f["area"])
    assert top["planar"] and top["normal"][2] > 0.99

    project = client.get(f"/api/projects/{pid}").json()
    assert project["revisions"] == [rid] and project["latest"]["id"] == rid
    assert client.delete(f"/api/projects/{pid}").json()["ok"]
