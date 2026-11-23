"""The in-app updater: version order, update sources (folder, manifest, GitHub), download + checksum, the API."""

import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from refract import updater


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(updater, "__version__", "0.1.0")
    updater._last_offer = None
    updater._status.update(state="idle", done=0, total=0, message="", version=None)
    yield


def _installer(folder, version, size=1_200_000):
    path = folder / f"Refract-Setup-{version}.exe"
    path.write_bytes(b"x" * size)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (folder / (path.name + ".sha256")).write_text(f"{digest}  {path.name}\n")
    return path, digest


def _custom(folder):
    return {**updater.DEFAULTS, "source": "custom", "location": str(folder)}


def test_version_order():
    k = updater.version_key
    assert k("1.2.10") > k("1.2.9")
    assert k("v1.3.0") > k("1.3.0-beta.2")
    assert k("1.3.0-beta.10") > k("1.3.0-beta.2")
    assert k("1.3.0-rc.1") > k("1.3.0-beta.9")
    assert k("1.2") == k("1.2.0")
    assert updater.is_newer("0.2.0", "0.1.0") and not updater.is_newer("0.1.0", "0.1.0") and not updater.is_newer("0.0.9", "0.1.0")


def test_folder_scan_picks_highest(tmp_path):
    _installer(tmp_path, "0.1.0")
    _, digest = _installer(tmp_path, "0.10.0")
    _installer(tmp_path, "0.9.0")
    res = updater.check(_custom(tmp_path))
    assert res["available"] and res["latest"] == "0.10.0" and res["offer"]["hasChecksum"]
    assert updater._last_offer["sha256"] == digest


def test_folder_with_nothing_newer(tmp_path):
    _installer(tmp_path, "0.1.0")
    res = updater.check(_custom(tmp_path))
    assert not res["available"] and res["latest"] == "0.1.0"


def test_missing_folder_and_empty_location(tmp_path):
    with pytest.raises(updater.UpdateError):
        updater.check(_custom(tmp_path / "nope"))
    with pytest.raises(updater.UpdateError):
        updater.check({**updater.DEFAULTS, "source": "custom", "location": ""})
    assert updater.check(_custom(tmp_path))["latest"] is None  # an empty folder is not an error


def test_manifest_in_folder(tmp_path):
    path, digest = _installer(tmp_path, "0.5.0")
    (tmp_path / "latest.json").write_text(json.dumps({"version": "0.5.0", "file": path.name, "sha256": digest, "notes": "Faster"}), encoding="utf-8-sig")
    res = updater.check(_custom(tmp_path))
    assert res["available"] and res["offer"]["notes"] == "Faster"
    assert updater._last_offer["location"] == str(path)


def test_github_release(monkeypatch):
    digest = "ab" * 32
    release = {
        "html_url": "https://github.com/hickmanz/Refract/releases/tag/v0.3.0",
        "body": "notes",
        "assets": [
            {"name": "Refract-Setup-0.3.0.exe", "browser_download_url": "https://example.test/Refract-Setup-0.3.0.exe", "size": 5},
            {"name": "Refract-Setup-0.3.0.exe.sha256", "browser_download_url": "https://example.test/sha"},
        ],
    }

    def fake_get(url, accept=None):
        if url.endswith("/releases/latest"):
            return json.dumps(release).encode()
        if url.endswith("/sha"):
            return f"{digest}  Refract-Setup-0.3.0.exe".encode()
        raise updater.UpdateError("unexpected " + url)

    monkeypatch.setattr(updater, "_http_get", fake_get)
    res = updater.check(dict(updater.DEFAULTS))
    assert res["available"] and res["latest"] == "0.3.0" and res["offer"]["page"].endswith("v0.3.0")
    assert updater._last_offer["sha256"] == digest and updater._last_offer["file"] == "Refract-Setup-0.3.0.exe"


def test_github_without_release(monkeypatch):
    def fake_get(url, accept=None):
        raise updater.UpdateError("Nothing found at x (404).")

    monkeypatch.setattr(updater, "_http_get", fake_get)
    assert updater.check(dict(updater.DEFAULTS))["latest"] is None


def _wait_state(timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if updater.status()["state"] != "downloading":
            return updater.status()
        time.sleep(0.05)
    raise AssertionError("download did not finish")


def test_download_checks_checksum(tmp_path):
    _installer(tmp_path, "0.2.0")
    updater.check(_custom(tmp_path))
    updater.start_download()
    st = _wait_state()
    assert st["state"] == "ready" and updater._path.is_file() and st["done"] == st["total"] > 0

    (tmp_path / "Refract-Setup-0.2.0.exe.sha256").write_text("0" * 64)  # a wrong checksum
    updater.check(_custom(tmp_path))
    updater.start_download()
    st = _wait_state()
    assert st["state"] == "failed" and "checksum" in st["message"]


def test_download_needs_a_check_first():
    with pytest.raises(updater.UpdateError):
        updater.start_download()


def test_settings_validation(monkeypatch):
    saved = {}
    monkeypatch.setattr(updater.store, "set_setting", lambda k, v: saved.update({k: v}))
    out = updater.set_settings({"source": "custom", "location": ' "D:\\Releases" ', "auto": False})
    assert out["location"] == "D:\\Releases" and out["auto"] is False and saved["update"] == out
    with pytest.raises(updater.UpdateError):
        updater.set_settings({"source": "ftp"})
    with pytest.raises(updater.UpdateError):
        updater.set_settings({"source": "custom", "location": "relative\\folder"})
    assert updater.set_settings({"source": "custom", "location": "https://example.com/ls"})["location"] == "https://example.com/ls"


def test_api(tmp_path):
    from refract.app import app

    client = TestClient(app)
    cfg = client.get("/api/config").json()
    assert cfg["update"]["source"] in ("github", "custom") and cfg["canInstallUpdates"] is False
    assert client.put("/api/update/settings", json={"source": "custom", "location": "relative"}).status_code == 400
    ok = client.put("/api/update/settings", json={"source": "custom", "location": str(tmp_path)})
    assert ok.status_code == 200 and ok.json()["location"] == str(tmp_path)
    assert client.post("/api/update/download").status_code == 400  # nothing checked yet
    _installer(tmp_path, "9.9.9")
    res = client.post("/api/update/check")
    assert res.status_code == 200 and res.json()["available"] and res.json()["latest"] == "9.9.9"
    client.put("/api/update/settings", json={"source": "github", "location": ""})


def test_legacy_installer_name_still_recognised(tmp_path):
    """Folders and releases from before the rename (LightSim-Setup-*) stay usable."""
    path = tmp_path / "LightSim-Setup-0.3.0.exe"
    path.write_bytes(b"x" * 1_200_000)
    res = updater.check(_custom(tmp_path))
    assert res["available"] and res["latest"] == "0.3.0"
