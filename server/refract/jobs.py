"""Launches and supervises job subprocesses (one at a time, FIFO).

Two kinds of jobs run in their own process (so a native OCCT crash or a long simulation cannot take the
server down, and a simulation can be cancelled by killing it):

* ``import``  STEP -> viewer model + optical mesh      key ``import:<rid>``
* ``sim``     Monte Carlo simulation of one run         key ``sim:<rid>:<runid>``
* ``render``  Mitsuba 3 picture of a finished run        key ``render:<rid>:<runid>``
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time

from . import procutil, store

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

Key = tuple[str, str, str]  # (kind, revision id, run id or "")


def key_str(key: Key) -> str:
    return ":".join(p for p in key if p)


def meta_of(key: Key) -> dict | None:
    kind, rid, runid = key
    if kind == "render":
        return store.read_json(job_dir(key) / "render.json", {})
    return store.load_rev(rid) if kind == "import" else store.load_run(rid, runid)


def save_meta(key: Key, meta: dict) -> None:
    kind, rid, runid = key
    if kind == "import":
        store.save_rev(rid, meta)
    elif kind == "render":
        store.write_json(job_dir(key) / "render.json", meta)
    else:
        store.save_run(rid, runid, meta)


def job_dir(key: Key):
    kind, rid, runid = key
    if kind == "render":
        d = store.run_dir(rid, runid) / "render"
        d.mkdir(parents=True, exist_ok=True)
        return d
    return store.rev_dir(rid) if kind == "import" else store.run_dir(rid, runid)


class JobManager:
    def __init__(self) -> None:
        # two lanes: imports and simulations share the CPU and run one after another; pictures (Mitsuba, mostly on the
        # GPU) have their own lane so that they do not wait behind a long simulation
        self._q: "queue.Queue[Key]" = queue.Queue()
        self._rq: "queue.Queue[Key]" = queue.Queue()
        self._procs: dict[Key, subprocess.Popen] = {}
        self._cancelled: set[Key] = set()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, args=(self._q,), daemon=True, name="job-runner")
        self._thread.start()
        self._render_thread = threading.Thread(target=self._loop, args=(self._rq,), daemon=True, name="render-runner")
        self._render_thread.start()
        self._recover()

    def _recover(self) -> None:
        """Jobs that were running when the server last stopped can never finish."""
        for rev in store.list_revisions():
            rid = rev["id"]
            if rev.get("status") in ("queued", "running"):
                self._fail_orphan(("import", rid, ""), rev)
            try:
                runs = store.list_runs(rid)
            except OSError:
                continue
            for run in runs:
                if run.get("status") in ("queued", "running"):
                    self._fail_orphan(("sim", rid, run["id"]), run)
                rj = store.run_dir(rid, run["id"]) / "render" / "render.json"
                rmeta = store.read_json(rj, None) if rj.exists() else None
                if rmeta and rmeta.get("status") in ("queued", "running"):
                    self._fail_orphan(("render", rid, run["id"]), rmeta)

    def _fail_orphan(self, key: Key, meta: dict) -> None:
        owner = meta.get("jobOwner") or {}
        if owner.get("session") != store.SESSION_ID and procutil.pid_alive(int(owner.get("pid") or 0)):
            return  # another copy of the app is still running it
        meta["status"] = "failed"
        meta["error"] = "Interrupted (the app was closed while this was running)"
        save_meta(key, meta)

    # -- public ----------------------------------------------------------------
    def submit(self, key: Key) -> None:
        meta = meta_of(key) or {}
        meta.update(status="queued", error=None, jobOwner={"pid": os.getpid(), "session": store.SESSION_ID})
        if key[0] == "import":
            meta.update(model_ready=False, summary=None)
        save_meta(key, meta)
        store.write_json(job_dir(key) / "progress.json", {"stage": "queued", "phase": "Queued", "fraction": 0.0})
        (self._rq if key[0] == "render" else self._q).put(key)

    def cancel(self, key: Key) -> bool:
        meta = meta_of(key)
        if not meta or meta.get("status") not in ("queued", "running"):
            return False
        with self._lock:
            self._cancelled.add(key)
            proc = self._procs.get(key)
        meta.update(status="cancelled", error="Cancelled")
        save_meta(key, meta)
        if proc is not None and proc.poll() is None:
            _kill_tree(proc)
        return True

    def is_active(self, key: Key) -> bool:
        meta = meta_of(key) or {}
        return meta.get("status") in ("queued", "running")

    def any_active(self) -> bool:
        with self._lock:
            return bool(self._procs) or not self._q.empty() or not self._rq.empty()

    # -- worker threads --------------------------------------------------------------
    def _loop(self, jobs: "queue.Queue[Key]") -> None:
        while True:
            key = jobs.get()
            with self._lock:
                if key in self._cancelled:
                    self._cancelled.discard(key)
                    continue
            try:
                self._run(key)
            except Exception as exc:  # pragma: no cover
                meta = meta_of(key) or {}
                meta.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                save_meta(key, meta)

    def _run(self, key: Key) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = _SERVER_DIR + os.pathsep + env.get("PYTHONPATH", "")
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        kind, rid, runid = key
        args = (kind, rid, runid) if runid else (kind, rid)
        log = open(job_dir(key) / "job.stderr.log", "wb")
        proc = subprocess.Popen(
            procutil.child_command("run-job", *args),
            cwd=None if procutil.is_frozen() else _SERVER_DIR,
            env=env,
            stdout=log,
            stderr=log,
            **kwargs,
        )
        with self._lock:
            self._procs[key] = proc
        code = proc.wait()
        log.close()
        with self._lock:
            self._procs.pop(key, None)
            was_cancelled = key in self._cancelled
            self._cancelled.discard(key)
        meta = meta_of(key) or {}
        if not meta:  # the run was deleted while it was running: do not bring its folder back
            return
        if was_cancelled:
            meta.update(status="cancelled", error="Cancelled")
            save_meta(key, meta)
        elif code != 0 and meta.get("status") not in ("failed",):
            meta.update(
                status="failed",
                error=f"Processing crashed (exit code {code}). See job.stderr.log in the data folder.",
                finished=time.time(),
            )
            save_meta(key, meta)


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
