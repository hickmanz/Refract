"""File-based storage.

Layout inside the data folder (default ``%LOCALAPPDATA%/refract``, override with ``REFRACT_DATA``)::

    projects/<pid>/project.json     name, revisions (index of all revision ids, oldest first), designs (e.g. Baseline, Dome reflector),
                                    variants (each in one design, e.g. diffuser thicknesses: its own setup and revisions)
    projects/<pid>/setups/<vid>.json  LED / part / state / detector setup of one variant, shared by that variant's revisions
    projects/<pid>/setup.json       legacy single setup; copied to the first variant when an old project is opened
    revisions/<rid>/source.step     the uploaded CAD
    revisions/<rid>/meta.json       import status, variantId (the one source of truth for which variant it is in), number (Rev N)
    revisions/<rid>/model.glb|json  viewer model
    revisions/<rid>/optical.npz|json  fine optical mesh
    revisions/<rid>/runs/<runid>/   run.json (status, setup snapshot, metrics) + tallies
    library.json                    user materials / finishes / LEDs (built-ins live in code)
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .envutil import env

SCHEMA_VERSION = 1


def _platform_base() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def _migrate_legacy_dir(parent: Path) -> Path:
    """The app was called LightSim before it was Refract; its folder moves along with the name.

    The old folder is renamed in one step (nothing is copied or deleted). If that is not possible right now (a
    copy of the old app still has files open in it), the old folder is used as it is and the move is tried again at
    the next start, so no data is ever duplicated or lost.
    """
    new, old = parent / "refract", parent / "lightsim"
    if not new.exists() and old.is_dir():
        try:
            os.replace(old, new)
        except OSError:
            return old
    return new


def config_dir() -> Path:
    """Always local to this machine and user: holds config.json (the data-folder setting)."""
    override = env("CONFIG")
    base = Path(override) if override else _migrate_legacy_dir(_platform_base())
    base.mkdir(parents=True, exist_ok=True)
    return base


def default_data_dir() -> Path:
    return config_dir()


def env_data_dir() -> str | None:
    return env("DATA")


def _read_config() -> dict:
    try:
        return json.loads((config_dir() / "config.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_config(cfg: dict) -> None:
    tmp = config_dir() / "config.json.tmp"
    tmp.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    os.replace(tmp, config_dir() / "config.json")


_configured_dir: str | None = _read_config().get("dataDir") or None
_ensured: set[str] = set()
_ensure_lock = threading.Lock()


def configured_data_dir() -> str | None:
    return _configured_dir


def data_dir() -> Path:
    """Environment variable > settings > default."""
    base = Path(env_data_dir() or _configured_dir or default_data_dir())
    key = str(base)
    if key not in _ensured:
        with _ensure_lock:
            (base / "revisions").mkdir(parents=True, exist_ok=True)
            (base / "projects").mkdir(parents=True, exist_ok=True)
            _ensured.add(key)
    return base


def set_data_dir(path: str | None) -> None:
    global _configured_dir
    cfg = _read_config()
    if path:
        cfg["dataDir"] = path
    else:
        cfg.pop("dataDir", None)
    _write_config(cfg)
    _configured_dir = path or None


def get_setting(key: str, default: Any = None) -> Any:
    """A per-machine app setting from config.json (next to the data-folder setting)."""
    return _read_config().get(key, default)


def set_setting(key: str, value: Any) -> None:
    cfg = _read_config()
    cfg[key] = value
    _write_config(cfg)


def validate_data_dir(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ValueError("Please give a full path such as D:\\Refract")
    try:
        (p / "revisions").mkdir(parents=True, exist_ok=True)
        probe = p / f".write-test-{uuid.uuid4().hex[:8]}"
        probe.write_text("ok", encoding="utf-8")
        if probe.read_text(encoding="utf-8") != "ok":
            raise OSError("read-back mismatch")
        probe.unlink()
    except OSError as exc:
        raise ValueError(f"Cannot read and write in that folder: {exc}") from exc
    return p


def copy_data(src: Path, dst: Path) -> int:
    copied = 0
    if src.resolve() == dst.resolve():
        return 0
    for sub in ("projects", "revisions"):
        s, d = src / sub, dst / sub
        if not s.is_dir():
            continue
        d.mkdir(parents=True, exist_ok=True)
        for item in s.iterdir():
            if not item.is_dir() or (d / item.name).exists():
                continue
            shutil.copytree(item, d / item.name, ignore=shutil.ignore_patterns("*.tmp"))
            copied += 1
    lib = src / "library.json"
    if lib.exists() and not (dst / "library.json").exists():
        shutil.copy2(lib, dst / "library.json")
    rays = src / "rays"
    if rays.is_dir():
        (dst / "rays").mkdir(parents=True, exist_ok=True)
        for f in rays.glob("*.npy"):
            if not (dst / "rays" / f.name).exists():
                shutil.copy2(f, dst / "rays" / f.name)
    return copied


SESSION_ID = uuid.uuid4().hex  # one per running server process


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: Any) -> None:
    """Atomic JSON write (readers never see a partial file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # a reader holds the file briefly on Windows
            time.sleep(0.05 * (attempt + 1))
    os.replace(tmp, path)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _check_id(value: str) -> str:
    if not value.isalnum():
        raise ValueError("invalid id")
    return value


# -- revisions ---------------------------------------------------------------------------
def revisions_dir() -> Path:
    return data_dir() / "revisions"


def rev_dir(rid: str) -> Path:
    return revisions_dir() / _check_id(rid)


def load_rev(rid: str) -> dict | None:
    return read_json(rev_dir(rid) / "meta.json")


def save_rev(rid: str, meta: dict) -> None:
    write_json(rev_dir(rid) / "meta.json", meta)


def list_revisions() -> list[dict]:
    out = []
    for d in revisions_dir().iterdir():
        if d.is_dir():
            meta = read_json(d / "meta.json")
            if meta:
                out.append(meta)
    out.sort(key=lambda m: m.get("created", 0), reverse=True)
    return out


def delete_revision(rid: str) -> None:
    meta = load_rev(rid) or {}
    pid = meta.get("projectId")
    shutil.rmtree(rev_dir(rid), ignore_errors=True)
    if pid:
        proj = load_project(pid)
        if proj is not None:
            proj["revisions"] = [r for r in proj.get("revisions", []) if r != rid]
            save_project(proj)


# -- runs --------------------------------------------------------------------------------
def runs_dir(rid: str) -> Path:
    d = rev_dir(rid) / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_dir(rid: str, runid: str) -> Path:
    return runs_dir(rid) / _check_id(runid)


def load_run(rid: str, runid: str) -> dict | None:
    return read_json(run_dir(rid, runid) / "run.json")


def save_run(rid: str, runid: str, run: dict) -> None:
    write_json(run_dir(rid, runid) / "run.json", run)


def list_runs(rid: str) -> list[dict]:
    out = []
    for d in runs_dir(rid).iterdir():
        if d.is_dir():
            run = read_json(d / "run.json")
            if run and run.get("id") and run.get("revisionId"):  # a stub left by a run that was deleted while it ran is not a run
                out.append(run)
    out.sort(key=lambda r: r.get("created", 0), reverse=True)
    return out


def delete_run(rid: str, runid: str) -> None:
    shutil.rmtree(run_dir(rid, runid), ignore_errors=True)


def find_run(runid: str) -> dict | None:
    """Runs are addressed by id alone in the API; the run document records its revision."""
    _check_id(runid)
    for d in revisions_dir().iterdir():
        if (d / "runs" / runid / "run.json").exists():
            run = read_json(d / "runs" / runid / "run.json")
            if run and run.get("id"):
                return run
    return None


# -- projects ----------------------------------------------------------------------------
def projects_dir() -> Path:
    d = data_dir() / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def project_dir(pid: str) -> Path:
    return projects_dir() / _check_id(pid)


def load_project(pid: str) -> dict | None:
    project = read_json(project_dir(pid) / "project.json")
    if project is not None:
        project = _migrate_project(project)
    return project


def save_project(project: dict) -> None:
    write_json(project_dir(project["id"]) / "project.json", project)


def list_projects() -> list[dict]:
    out = []
    for d in projects_dir().iterdir():
        if d.is_dir():
            p = read_json(d / "project.json")
            if p:
                out.append(_migrate_project(p))
    out.sort(key=lambda p: p.get("lastOpened", p.get("created", 0)), reverse=True)
    return out


def stem_of(filename: str) -> str:
    return Path(filename).stem.lower()


def find_project_by_stem(stem: str) -> dict | None:
    for p in list_projects():
        if p.get("stem") == stem:
            return p
    return None


def create_project(name: str) -> dict:
    now = time.time()
    design = _new_design(DEFAULT_DESIGN_NAME, now)
    project = {
        "id": new_id(),
        "name": name,
        "stem": stem_of(name),
        "created": now,
        "lastOpened": now,
        "revisions": [],
        "schema": PROJECT_SCHEMA,
        "designs": [design],
        "variants": [_new_variant(DEFAULT_VARIANT_NAME, design["id"], now)],
    }
    save_project(project)
    return project


# -- designs and variants ----------------------------------------------------------------------------
# A project has designs (e.g. "Baseline", "Dome reflector"); a design groups variants (e.g. diffuser thicknesses), and a variant is
# one setup plus its revisions (the uploaded STEP files, Rev 1, Rev 2, ...). Designs only group variants: nothing else is shared.
DEFAULT_DESIGN_NAME = "Baseline"
DEFAULT_VARIANT_NAME = "Default"
PROJECT_SCHEMA = 3  # 3: designs group the variants; 2: membership lives in each revision's variantId, revisions are numbered


def _new_design(name: str, created: float | None = None) -> dict:
    return {"id": new_id(), "name": name, "created": created if created is not None else time.time()}


def _new_variant(name: str, design_id: str | None, created: float | None = None) -> dict:
    """A variant: its own setup (``setups/<id>.json``) and revisions; ``nextRev`` numbers the revisions."""
    return {"id": new_id(), "name": name, "created": created if created is not None else time.time(), "designId": design_id, "nextRev": 1}


def _migrate_project(project: dict) -> dict:
    """Bring an older project.json to the current schema (idempotent; a no-op once ``schema`` is current).

    - Projects made before variants existed get one variant with the old setup.
    - Every revision gets the ``variantId`` it had in the old per-variant revision lists, and a stable ``number``
      within its variant (re-meshes are not numbered); the lists are dropped.
    - Variants are grouped in designs: each variant that was not forked from another becomes a design (named after it) with one
      variant called "Default", and the variants forked from it (directly or not) go into that design.
    """
    schema = project.get("schema", 0)
    if schema >= PROJECT_SCHEMA:
        return project
    pid = project["id"]
    now = time.time()
    if "variants" not in project:
        baseline = _new_variant(DEFAULT_DESIGN_NAME, None, project.get("created", now))
        project["variants"] = [baseline]
        old = read_json(project_dir(pid) / "setup.json")
        if old is not None:
            write_json(setups_dir(pid) / f"{baseline['id']}.json", old)
    if schema < 2:
        _migrate_membership(project)
    _migrate_designs(project)
    project["schema"] = PROJECT_SCHEMA
    save_project(project)
    return project


def _migrate_membership(project: dict) -> None:
    variants = project["variants"]
    first = variants[0]["id"] if variants else None
    known = {v["id"] for v in variants}
    member: dict[str, str] = {}  # the old per-variant lists
    for v in variants:
        for rid in v.pop("revisions", None) or []:
            member.setdefault(rid, v["id"])
    metas, changed = [], set()
    for rid in project.get("revisions", []):
        meta = load_rev(rid)
        if meta is None:
            continue
        vid = meta.get("variantId")
        if vid not in known:
            vid = member.get(rid) or first
            if vid is not None:
                meta["variantId"] = vid
                changed.add(rid)
        metas.append(meta)
    for v in variants:
        mine = sorted((m for m in metas if m.get("variantId") == v["id"]), key=lambda m: m.get("created", 0))
        n = max((m["number"] for m in mine if m.get("number")), default=0)
        for m in mine:
            if m.get("number") or m.get("remeshedFrom"):
                continue
            n += 1
            m["number"] = n
            changed.add(m["id"])
        v.setdefault("nextRev", n + 1)
    for m in metas:
        if m["id"] in changed:
            save_rev(m["id"], m)


def _migrate_designs(project: dict) -> None:
    """Variants that were forked from another (``parentId``) join the design of the variant they descend from."""
    variants = project["variants"]
    by_id = {v["id"]: v for v in variants}

    def root(v: dict) -> dict:
        seen = {v["id"]}
        while v.get("parentId") in by_id and v["parentId"] not in seen:
            v = by_id[v["parentId"]]
            seen.add(v["id"])
        return v

    designs: list[dict] = []
    design_of_root: dict[str, str] = {}
    for v in variants:
        r = root(v)
        if r["id"] not in design_of_root:
            d = _new_design(r["name"], r.get("created"))
            designs.append(d)
            design_of_root[r["id"]] = d["id"]
    roots = {v["id"]: root(v)["id"] for v in variants}  # before the parent links are dropped
    for v in variants:
        v["designId"] = design_of_root[roots[v["id"]]]
        v.pop("parentId", None)
        v.pop("forkRevisionId", None)
    for rid in design_of_root:
        by_id[rid]["name"] = DEFAULT_VARIANT_NAME
    project["designs"] = designs


def setups_dir(pid: str) -> Path:
    return project_dir(pid) / "setups"


def find_design(project: dict, did: str | None) -> dict | None:
    """The design with this id; the project's first design when ``did`` is empty."""
    designs = project.get("designs", [])
    if not did:
        return designs[0] if designs else None
    for d in designs:
        if d["id"] == did:
            return d
    return None


def design_variants(project: dict, did: str) -> list[dict]:
    """The variants of a design, oldest first."""
    return sorted((v for v in project.get("variants", []) if v.get("designId") == did), key=lambda v: v.get("created", 0))


def find_variant(project: dict, vid: str | None) -> dict | None:
    """The variant with this id; the project's first variant when ``vid`` is empty."""
    variants = project.get("variants", [])
    if not vid:
        return variants[0] if variants else None
    for v in variants:
        if v["id"] == vid:
            return v
    return None


def variant_of_rev(project: dict, meta: dict) -> dict | None:
    """The variant a revision belongs to (every revision of a migrated project has a variantId)."""
    vid = meta.get("variantId")
    return find_variant(project, vid) if vid else None


def variant_revisions(project: dict, vid: str) -> list[dict]:
    """Meta of the revisions of a variant, oldest first. Membership is each revision's ``variantId``."""
    out = []
    for rid in project.get("revisions", []):
        meta = load_rev(rid)
        if meta and meta.get("variantId") == vid:
            out.append(meta)
    out.sort(key=lambda m: m.get("created", 0))
    return out


def revision_name(meta: dict, source: dict | None = None) -> str:
    """How a revision is called in lists: "Rev 2" (plus its label), "fine mesh of Rev 2" for a re-mesh, else its label or file name."""
    label = meta.get("label")
    if meta.get("number"):
        return f"Rev {meta['number']}" + (f" \u201c{label}\u201d" if label else "")
    if meta.get("remeshedFrom") and source is not None and source.get("number"):
        return f"{meta.get('meshQuality', 'normal')} mesh of Rev {source['number']}"
    return label or meta.get("filename") or meta.get("id", "")


def _copy_setup(pid: str, src_vid: str | None, dst_vid: str) -> None:
    if not src_vid:
        return
    setup = load_setup(pid, src_vid)
    if setup is not None:
        save_setup(pid, setup, dst_vid)


def create_design(pid: str, name: str, variant_name: str | None = None, copy_from: str | None = None) -> tuple[dict, dict]:
    """New design with its first variant; with ``copy_from`` (a variant id) the variant starts with a copy of that setup."""
    project = load_project(pid)
    if project is None:
        raise KeyError(pid)
    if copy_from and find_variant(project, copy_from) is None:
        raise KeyError(copy_from)
    design = _new_design(name)
    variant = _new_variant(variant_name or DEFAULT_VARIANT_NAME, design["id"])
    project.setdefault("designs", []).append(design)
    project["variants"].append(variant)
    save_project(project)
    _copy_setup(pid, copy_from, variant["id"])
    return design, variant


def rename_design(pid: str, did: str, name: str) -> dict:
    project = load_project(pid)
    design = find_design(project, did) if project else None
    if design is None:
        raise KeyError(did)
    design["name"] = name
    save_project(project)
    return design


def delete_design(pid: str, did: str) -> list[str]:
    """Remove a design with its variants, revisions and setups; returns the removed revision ids (the last design cannot go)."""
    project = load_project(pid)
    design = find_design(project, did) if project else None
    if design is None:
        raise KeyError(did)
    if len(project["designs"]) <= 1:
        raise ValueError("a project needs at least one design")
    gone: list[str] = []
    for v in design_variants(project, did):
        gone += [m["id"] for m in variant_revisions(project, v["id"])]
        (setups_dir(pid) / f"{_check_id(v['id'])}.json").unlink(missing_ok=True)
    for rid in gone:
        shutil.rmtree(rev_dir(rid), ignore_errors=True)
    project["variants"] = [v for v in project["variants"] if v.get("designId") != did]
    project["designs"] = [d for d in project["designs"] if d["id"] != did]
    project["revisions"] = [r for r in project.get("revisions", []) if r not in gone]
    save_project(project)
    return gone


def create_variant(pid: str, name: str, design_id: str | None = None, copy_from: str | None = None) -> dict:
    """New variant in a design (default: the design of ``copy_from``, else the first design). With ``copy_from`` (a variant id)
    it starts with a copy of that variant's setup."""
    project = load_project(pid)
    if project is None:
        raise KeyError(pid)
    src = None
    if copy_from:
        src = find_variant(project, copy_from)
        if src is None:
            raise KeyError(copy_from)
    design = find_design(project, design_id or (src or {}).get("designId"))
    if design is None:
        raise KeyError(design_id or pid)
    variant = _new_variant(name, design["id"])
    project["variants"].append(variant)
    save_project(project)
    _copy_setup(pid, src["id"] if src else None, variant["id"])
    return variant


def rename_variant(pid: str, vid: str, name: str) -> dict:
    project = load_project(pid)
    variant = find_variant(project, vid) if project else None
    if variant is None:
        raise KeyError(vid)
    variant["name"] = name
    save_project(project)
    return variant


def move_variant(pid: str, vid: str, design_id: str) -> dict:
    """Put a variant into another design. A design that is left without variants is removed."""
    project = load_project(pid)
    variant = find_variant(project, vid) if project else None
    if variant is None:
        raise KeyError(vid)
    if find_design(project, design_id) is None or not design_id:
        raise KeyError(design_id)
    variant["designId"] = design_id
    used = {v.get("designId") for v in project["variants"]}
    project["designs"] = [d for d in project["designs"] if d["id"] in used]
    save_project(project)
    return variant


def delete_variant(pid: str, vid: str) -> list[str]:
    """Remove a variant with its revisions and setup; returns the removed revision ids.

    The last variant of a design cannot go (delete the design instead).
    """
    project = load_project(pid)
    variant = find_variant(project, vid) if project else None
    if variant is None:
        raise KeyError(vid)
    if len(design_variants(project, variant.get("designId"))) <= 1:
        raise ValueError("a design needs at least one variant; delete the design instead")
    gone = [m["id"] for m in variant_revisions(project, vid)]
    for rid in gone:
        shutil.rmtree(rev_dir(rid), ignore_errors=True)
    project["variants"] = [v for v in project["variants"] if v["id"] != vid]
    project["revisions"] = [r for r in project.get("revisions", []) if r not in gone]
    save_project(project)
    (setups_dir(pid) / f"{_check_id(vid)}.json").unlink(missing_ok=True)
    return gone


def add_revision(pid: str, rid: str, vid: str | None = None) -> None:
    """Put a revision into a variant (the first one when ``vid`` is empty); a new revision gets the variant's next number."""
    project = load_project(pid)
    if project is None:
        raise KeyError(pid)
    variant = find_variant(project, vid)
    if variant is None:
        raise KeyError(vid)
    if rid not in project["revisions"]:
        project["revisions"].append(rid)
    meta = load_rev(rid)
    if meta is not None:
        meta["variantId"] = variant["id"]
        if not meta.get("number") and not meta.get("remeshedFrom"):
            n = variant.get("nextRev") or 1
            meta["number"] = n
            variant["nextRev"] = n + 1
        save_rev(rid, meta)
    project["lastOpened"] = time.time()
    save_project(project)


def touch_project(pid: str) -> None:
    project = load_project(pid)
    if project:
        project["lastOpened"] = time.time()
        save_project(project)


def delete_project(pid: str) -> None:
    project = load_project(pid)
    if project:
        for rid in project.get("revisions", []):
            shutil.rmtree(rev_dir(rid), ignore_errors=True)
    shutil.rmtree(project_dir(pid), ignore_errors=True)


_SIZE_TTL = 30.0  # seconds; the home screen polls every few seconds and a walk of big run folders is not free
_size_cache: dict[tuple[str, str], tuple[float, int]] = {}
_size_lock = threading.Lock()


def _tree_size(path: Path) -> int:
    total = 0
    stack = [str(path)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:  # a file vanished while it was being counted
                        continue
        except OSError:
            continue
    return total


def project_size(project: dict) -> int:
    """Bytes on disk used by a project: its own folder plus the folder of each of its revisions (what ``delete_project`` removes).

    The result is cached for a short while per project.
    """
    pid = project["id"]
    key = (str(data_dir()), pid)
    now = time.monotonic()
    with _size_lock:
        hit = _size_cache.get(key)
        if hit is not None and now - hit[0] < _SIZE_TTL:
            return hit[1]
    total = _tree_size(project_dir(pid))
    for rid in project.get("revisions", []):
        total += _tree_size(rev_dir(rid))
    with _size_lock:
        _size_cache[key] = (now, total)
    return total


def _setup_variant_id(pid: str, vid: str | None) -> str:
    project = load_project(pid)
    variant = find_variant(project, vid) if project else None
    if variant is None:
        raise KeyError(vid or pid)
    return variant["id"]


def load_setup(pid: str, vid: str | None = None) -> dict | None:
    """Setup of a variant (the project's first variant when ``vid`` is empty)."""
    return read_json(setups_dir(pid) / f"{_check_id(_setup_variant_id(pid, vid))}.json")


def save_setup(pid: str, setup: dict, vid: str | None = None) -> None:
    write_json(setups_dir(pid) / f"{_check_id(_setup_variant_id(pid, vid))}.json", setup)


def load_setup_for_rev(meta: dict) -> dict | None:
    """The setup that applies to a revision: the one of its variant."""
    pid = meta["projectId"]
    project = load_project(pid)
    variant = variant_of_rev(project, meta) if project else None
    return load_setup(pid, variant["id"]) if variant else None


# -- library -----------------------------------------------------------------------------
def library_path() -> Path:
    return data_dir() / "library.json"


def load_user_library() -> dict:
    lib = read_json(library_path(), {}) or {}
    for kind in ("materials", "finishes", "leds"):
        lib.setdefault(kind, {})
    return lib


def save_user_library(lib: dict) -> None:
    write_json(library_path(), lib)
