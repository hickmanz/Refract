"""Self-update: find a newer installer, download it, check it and run it.

Where the installer comes from is a setting (Settings > Updates):

* ``github`` - the releases of ``GITHUB_REPO`` (what the release workflow publishes)
* ``custom`` - a folder (also a network share) or a web address that holds ``Refract-Setup-<version>.exe``,
  optionally with ``latest.json`` (``{"version", "file", "sha256", "notes"}``) and ``<installer>.sha256``.
  A folder without ``latest.json`` is scanned for the highest ``Refract-Setup-*.exe``, so the ``dist-installer``
  folder of a local build can be used as it is.

The installer is run silently and starts the app again when it is done (see ``packaging/installer.iss``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import procutil, store
from .__version__ import __version__

GITHUB_REPO = "hickmanz/Refract"
# "LightSim-Setup-*" is the name this app had before it was renamed; it is still recognised so old releases and folders keep working
SETUP_RE = re.compile(r"^(?:Refract|LightSim)-Setup-(.+)\.exe$", re.IGNORECASE)
SOURCES = ("github", "custom")
DEFAULTS: dict[str, Any] = {"source": "github", "location": "", "auto": True, "prerelease": False}
TIMEOUT = 15
MAX_NOTES = 4000


class UpdateError(Exception):
    """Something the user can act on (no network, nothing at that location, bad checksum)."""


# -- settings ----------------------------------------------------------------------------------
def get_settings() -> dict[str, Any]:
    saved = store.get_setting("update", {})
    out = dict(DEFAULTS)
    if isinstance(saved, dict):
        out.update({k: saved[k] for k in DEFAULTS if k in saved})
    return out


def set_settings(values: dict[str, Any]) -> dict[str, Any]:
    cur = get_settings()
    if "source" in values:
        if values["source"] not in SOURCES:
            raise UpdateError("Unknown update source.")
        cur["source"] = values["source"]
    if "location" in values:
        cur["location"] = str(values["location"] or "").strip().strip('"')
    for key in ("auto", "prerelease"):
        if key in values:
            cur[key] = bool(values[key])
    if cur["source"] == "custom" and cur["location"]:
        _check_location_form(cur["location"])
    store.set_setting("update", cur)
    return cur


def _is_url(text: str) -> bool:
    return text.lower().startswith(("http://", "https://"))


def _check_location_form(loc: str) -> None:
    if not _is_url(loc) and not Path(loc).is_absolute() and not loc.startswith("\\\\"):
        raise UpdateError("Give a full folder path such as D:\\Releases or \\\\server\\share\\Refract, or a web address (https://…).")


# -- versions ----------------------------------------------------------------------------------
def version_key(text: str) -> tuple:
    """Sort key: 1.2.10 > 1.2.9, and 1.3.0 > 1.3.0-beta.2."""
    text = text.strip().lstrip("vV")
    core, _, pre = re.split(r"[+]", text)[0].partition("-")
    nums = []
    for part in core.split("."):
        m = re.match(r"\d+", part)
        nums.append(int(m.group()) if m else 0)
    nums = (nums + [0, 0, 0, 0])[: max(4, len(nums))]
    pre_key = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"[.\-]", pre)) if pre else ()
    return (tuple(nums), 1 if not pre else 0, pre_key)


def is_newer(candidate: str, current: str | None = None) -> bool:
    return version_key(candidate) > version_key(current or __version__)


# -- reading the source ------------------------------------------------------------------------
def _http_get(url: str, accept: str | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"Refract/{__version__}", **({"Accept": accept} if accept else {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError(f"Nothing found at {url} (404).")
        if exc.code in (403, 429) and "api.github.com" in url:
            raise UpdateError("GitHub refused the request (rate limit?). Try again in a while.")
        raise UpdateError(f"The server answered {exc.code} for {url}.")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"Cannot reach {urllib.parse.urlparse(url).netloc or url}: {getattr(exc, 'reason', exc)}")


def _read_sha(text: str) -> str | None:
    m = re.search(r"\b[0-9a-fA-F]{64}\b", text or "")
    return m.group().lower() if m else None


def _offer(version: str, source: str, *, file: str, notes: str = "", page: str | None = None, sha256: str | None = None, size: int | None = None) -> dict:
    return {
        "version": version.lstrip("vV"),
        "file": Path(urllib.parse.urlparse(file).path).name if _is_url(file) else Path(file).name,
        "source": source,  # URL or path the installer is read from
        "location": file,
        "sha256": sha256,
        "notes": (notes or "").strip()[:MAX_NOTES],
        "page": page,
        "size": size,
    }


def _from_github(settings: dict) -> dict | None:
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
    accept = "application/vnd.github+json"
    if settings["prerelease"]:
        releases = json.loads(_http_get(f"{api_url}?per_page=15", accept))
    else:
        try:
            releases = [json.loads(_http_get(f"{api_url}/latest", accept))]
        except UpdateError as exc:
            if "404" in str(exc):  # no (non-prerelease) release yet
                return None
            raise
    best = None
    for rel in releases:
        if rel.get("draft") or (rel.get("prerelease") and not settings["prerelease"]):
            continue
        setup = next((a for a in rel.get("assets", []) if SETUP_RE.match(a.get("name", ""))), None)
        if not setup:
            continue
        version = SETUP_RE.match(setup["name"]).group(1)
        if best is None or version_key(version) > version_key(best[0]):
            best = (version, rel, setup)
    if not best:
        return None
    version, rel, setup = best
    sha = None
    sha_asset = next((a for a in rel["assets"] if a["name"].lower() == setup["name"].lower() + ".sha256"), None)
    if sha_asset:
        try:
            sha = _read_sha(_http_get(sha_asset["browser_download_url"]).decode("utf-8", "replace"))
        except UpdateError:
            sha = None
    return _offer(version, "github", file=setup["browser_download_url"], notes=rel.get("body") or "", page=rel.get("html_url"), sha256=sha, size=setup.get("size"))


def _from_manifest(data: dict, base: str, source: str) -> dict:
    """``base`` is the folder or web directory the manifest was read from (file names in it are relative to it)."""
    version = str(data.get("version", "")).strip()
    file = str(data.get("file", "")).strip()
    if not version or not file:
        raise UpdateError("latest.json must contain \"version\" and \"file\".")
    if _is_url(file) or Path(file).is_absolute():
        loc = file
    elif _is_url(base):
        loc = urllib.parse.urljoin(base.rstrip("/") + "/", file)
    else:
        loc = str(Path(base) / file)
    return _offer(version, source, file=loc, notes=str(data.get("notes") or ""), page=data.get("page"), sha256=_read_sha(str(data.get("sha256") or "")), size=data.get("size"))


def _from_custom(settings: dict) -> dict | None:
    loc = settings["location"]
    if not loc:
        raise UpdateError("Choose a folder or web address in Settings > Updates.")
    if _is_url(loc):
        manifest_url = loc if loc.lower().split("?")[0].endswith(".json") else loc.rstrip("/") + "/latest.json"
        try:
            data = json.loads(_http_get(manifest_url))
        except ValueError:
            raise UpdateError(f"{manifest_url} is not valid JSON.")
        return _from_manifest(data, manifest_url.rsplit("/", 1)[0], "custom")

    path = Path(loc)
    if path.is_file() and path.suffix.lower() == ".json":
        return _from_manifest(_read_local_json(path), str(path.parent), "custom")
    if not path.is_dir():
        raise UpdateError(f"The folder {loc} does not exist or cannot be reached.")
    manifest = path / "latest.json"
    if manifest.is_file():
        return _from_manifest(_read_local_json(manifest), str(path), "custom")
    setups = [(SETUP_RE.match(p.name).group(1), p) for p in path.iterdir() if p.is_file() and SETUP_RE.match(p.name)]
    if not setups:
        return None
    version, setup = max(setups, key=lambda t: version_key(t[0]))
    sha_file = setup.with_name(setup.name + ".sha256")
    sha = _read_sha(sha_file.read_text(encoding="utf-8", errors="replace")) if sha_file.is_file() else None
    return _offer(version, "custom", file=str(setup), sha256=sha, size=setup.stat().st_size)


def _read_local_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise UpdateError(f"Cannot read {path}: {exc}")


_last_offer: dict | None = None


def check(settings: dict | None = None) -> dict:
    """What is available at the configured source, and is it newer than this copy."""
    global _last_offer
    settings = settings or get_settings()
    offer = _from_github(settings) if settings["source"] == "github" else _from_custom(settings)
    available = bool(offer and is_newer(offer["version"]))
    _last_offer = offer if available else None
    if not available and status()["state"] != "downloading":
        cleanup_old_downloads()  # an earlier update has been installed: its installer is not needed any more
    public = None
    if offer:
        public = {k: offer[k] for k in ("version", "file", "notes", "page", "size")}
        public["hasChecksum"] = bool(offer["sha256"])
    return {
        "current": __version__,
        "latest": offer["version"] if offer else None,
        "available": available,
        "offer": public,
        "source": settings["source"],
        "checkedAt": time.time(),
    }


# -- download and install ----------------------------------------------------------------------
def can_install() -> bool:
    return sys.platform == "win32" and procutil.is_frozen()


_lock = threading.Lock()
_status: dict[str, Any] = {"state": "idle", "done": 0, "total": 0, "message": "", "version": None}
_path: Path | None = None


def status() -> dict:
    with _lock:
        return {**_status, "canInstall": can_install()}


def _set(**kw: Any) -> None:
    with _lock:
        _status.update(kw)


def start_download() -> dict:
    """Start downloading the offer found by the last check() (in a thread; poll status())."""
    offer = _last_offer
    if not offer:
        raise UpdateError("Check for updates first.")
    with _lock:
        if _status["state"] == "downloading":
            return {**_status, "canInstall": can_install()}
        _status.update(state="downloading", done=0, total=offer.get("size") or 0, message="", version=offer["version"])
    threading.Thread(target=_download, args=(offer,), daemon=True, name="update-download").start()
    return status()


def _target_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "Refract-update"
    d.mkdir(parents=True, exist_ok=True)
    for old in d.iterdir():  # installers of other versions
        try:
            if old.is_file():
                old.unlink(missing_ok=True)
        except OSError:
            pass
    return d


def _download(offer: dict) -> None:
    global _path
    dest = _target_dir() / offer["file"]
    part = dest.with_name(dest.name + ".part")
    try:
        digest = hashlib.sha256()
        loc = offer["location"]
        if _is_url(loc):
            req = urllib.request.Request(loc, headers={"User-Agent": f"Refract/{__version__}"})
            src = urllib.request.urlopen(req, timeout=TIMEOUT)
            total = int(src.headers.get("Content-Length") or offer.get("size") or 0)
        else:
            src = open(loc, "rb")
            total = os.path.getsize(loc)
        _set(total=total)
        done = 0
        with src, open(part, "wb") as out:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                _set(done=done)
        if offer.get("sha256") and digest.hexdigest() != offer["sha256"]:
            raise UpdateError("The downloaded file does not match its checksum, so it was discarded.")
        if done < 1_000_000:
            raise UpdateError("The downloaded file is too small to be the installer.")
        os.replace(part, dest)
        _path = dest
        _set(state="ready", message="")
    except UpdateError as exc:
        _fail(part, str(exc))
    except Exception as exc:  # network drop, disk full, unreadable share …
        _fail(part, f"Download failed: {getattr(exc, 'reason', exc)}")


def _fail(part: Path, message: str) -> None:
    part.unlink(missing_ok=True)
    _set(state="failed", message=message)


def launch_installer() -> dict:
    """Run the downloaded installer silently; it replaces this copy and starts it again. The caller then quits."""
    with _lock:
        ready = _status["state"] == "ready" and _path is not None and _path.is_file()
    if not ready:
        raise UpdateError("The installer has not been downloaded yet.")
    assert _path is not None
    if not can_install():
        msg = f"Downloaded to {_path}. Updates are installed from the installed app only; run that file yourself."
        _set(message=msg)
        return {"launched": False, "message": msg, "path": str(_path)}
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: outlives this process
    subprocess.Popen(
        [str(_path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH=1"],
        creationflags=flags,
        close_fds=True,
    )
    _set(state="launching", message="Installing the update. Refract restarts by itself.")
    return {"launched": True, "message": _status["message"], "path": str(_path)}


def cleanup_old_downloads() -> None:
    """Remove installers left behind by an earlier update."""
    d = Path(tempfile.gettempdir()) / "Refract-update"
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
