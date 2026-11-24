"""Live photorealistic view of a run in the 3D viewport.

The browser sends the camera of its 3D view; a helper process that keeps the Mitsuba 3 scene loaded (on the GPU when
there is one) renders a few samples per request and keeps adding them up until the camera moves. The exit surface
comes from the run's tallies, so while a live simulation is still running the picture follows it: whenever the
simulation saves a new pass, the emitter texture is replaced and the accumulation starts over.

    browser  --POST live-frame-->  server  --pipe-->  helper process (Mitsuba)  --JPEG-->  browser

The helper is a separate process for the same reason the simulations are: a GPU or driver problem cannot take the
server down, and it can simply be killed to free the GPU.
"""

from __future__ import annotations

import io
import math
import os
import secrets
import subprocess
import threading
import time
from multiprocessing.connection import Client, Listener
from pathlib import Path

import numpy as np

from .. import procutil, store
from .mitsuba_export import TRANSPORT_UNITS

IDLE_SECONDS = 180  # a view nobody asked a picture of for this long is closed (frees the GPU)
TRANSPORT_DEPTH = 48  # bounces of the full light transport
# Mitsuba's radiance of the LED emitters (W/(mm^2 sr)) against the exit-surface map (cd/m^2): measured on the sample light bar, the same
# exit surface comes out about 9e10 times brighter in the map. It is a unit conversion, not a fit (see mitsuba_export.TRANSPORT_UNITS).
BASE_ROOM_LUX = 500.0  # the scene is built with a room light; the real strength is set per frame


def sensor_dict(mi, cam: dict, w: int, h: int) -> dict:
    """The Mitsuba sensor of a browser camera (``position``, ``target``, ``up``, ``fov`` or ``orthoHalfH``) for a ``w`` x ``h`` picture.

    Perspective: ``fov`` is the vertical angle. Orthographic: ``orthoHalfH`` is the half height of the view. Mitsuba's
    orthographic camera shows a half width of ``scale_x`` and a half height of ``scale_y * h / w``, so both scales are
    ``orthoHalfH * w / h``. The 3D view's orthographic camera also shows what lies behind its position, which Mitsuba cannot
    see, so its origin is moved back along the view axis (and the far plane by as much).
    """
    pos, tgt, up = (list(map(float, cam[k])) for k in ("position", "target", "up"))
    ortho = cam.get("orthoHalfH")
    dist = float(sum((a - b) ** 2 for a, b in zip(pos, tgt)) ** 0.5) or 1.0
    near = float(cam.get("near", 0.0)) or max(dist * 0.005, 1e-3)  # 0 = the browser did not say
    far = float(cam.get("far", 0.0))
    if far <= near:
        far = max(dist * 50.0, near * 1000.0)
    d: dict = {
        "film": {"type": "hdrfilm", "width": w, "height": h, "pixel_format": "rgb", "component_format": "float32", "rfilter": {"type": "gaussian"}},
        "sampler": {"type": "independent", "sample_count": 4},
        "near_clip": near,
        "far_clip": far,
    }
    if ortho:
        hh = float(ortho)
        back = far * 0.5
        fwd = [(b - a) / dist for a, b in zip(pos, tgt)]
        origin = [a - f * back for a, f in zip(pos, fwd)]
        d.update(
            type="orthographic",
            near_clip=near,
            far_clip=far + back,
            to_world=mi.ScalarTransform4f().look_at(origin=origin, target=tgt, up=up) @ mi.ScalarTransform4f().scale([hh * w / h, hh * w / h, 1.0]),
        )
    else:
        d.update(type="perspective", fov=float(cam.get("fov", 35.0)), fov_axis="y", to_world=mi.ScalarTransform4f().look_at(origin=pos, target=tgt, up=up))
    return d


# ═════════════════════════════ the helper process ═════════════════════════════════════════════
class _Worker:
    def __init__(self, rid: str, runid: str) -> None:
        import mitsuba as mi

        from . import mitsuba_export as mx

        self.mi, self.mx = mi, mx
        self.variant = mx.select_variant()
        self.rid, self.runid = rid, runid
        self.rdir = store.run_dir(rid, runid)
        self.setup = store.read_json(self.rdir / "setup.json")
        self.library = store.read_json(self.rdir / "library.json")
        if not self.setup or not self.library:
            raise ValueError("The run has no setup snapshot")
        self.scene = None
        self.t_scene = None  # the full light transport scene, built when asked for
        self.t_params = None
        self.t_state: str | None = None  # the state the transport scene was built for
        self.t_ref = 1.0
        self.t_ref_key: tuple | None = None
        self.sdict: dict = {}
        self.params = None
        self.room_scene = None  # a receiver drawn with another surface: its finish under the room light only (added to the picture)
        self.room_params = None
        self.scale = 1.0
        self.exit_info: dict = {"surfaces": [], "reference": 0.0}  # per lit surface: peak and mean luminance (cd/m^2); the "bright" level of the picture
        self.n_exit = 0
        self.map_key: tuple | None = None
        self.hidden: tuple[int, ...] = ()  # exit surfaces (detector indices) the scene leaves dark
        self.sensors: dict[tuple, object] = {}
        self.acc: np.ndarray | None = None
        self.nspp = 0
        self.cam_key: tuple | None = None
        self.state_id: str | None = None
        self.state_ids: list[str] = [st["id"] for st in mx.effective_states(self.setup)]

    def _state(self, view: dict) -> str:
        """The state to draw: the one asked for when the setup has it, else the first."""
        want = view.get("state")
        return want if want in self.state_ids else self.state_ids[0]

    # -- the scene follows the run's tallies ------------------------------------------------------------
    def _tallies_stamp(self) -> int:
        p = self.rdir / "tallies.npz"
        return p.stat().st_mtime_ns if p.exists() else 0

    def _ensure_scene(self, view: dict) -> str | None:
        """Builds the scene on the first use and refreshes the exit-surface texture when the run saved more rays.
        Returns a reason when there is nothing to draw yet."""
        mi, mx = self.mi, self.mx
        stamp = self._tallies_stamp()
        if not stamp:
            return "The simulation has not saved any rays yet"
        state = self._state(view)
        hidden = mx.hidden_exits(view)
        key = (stamp, state, round(float(view.get("smooth", 1.0)), 2), hidden)
        if self.scene is not None and hidden != self.hidden:
            self.scene = None  # another set of lit exit surfaces: the scene has other emitters, so it is built again
        if self.scene is not None and key == self.map_key:
            return None
        try:
            if self.scene is None:
                d, info = mx.build_mitsuba_scene(
                    self.rid, self.setup, self.library, state,
                    {"mode": "map", "width": 640, "room_lux": BASE_ROOM_LUX, "smooth": float(view.get("smooth", 1.0)), "hidden_exits": list(hidden)},
                    self.rdir / "live_meshes", self.runid,
                )
                self.hidden = hidden
                d.pop("sensor", None)
                self.sdict = d
                self.scene = mi.load_dict(d)
                self.params = mi.traverse(self.scene)
                room = info.get("roomScene")
                if room:
                    room.pop("sensor", None)
                    self.room_scene = mi.load_dict(room)
                    self.room_params = mi.traverse(self.room_scene)
                else:
                    self.room_scene, self.room_params = None, None
                self.scale = float(info["radianceScale"])
                self.exit_info = info["exitInfo"]
                self.n_exit = sum(1 for k in d if k.startswith("exit_"))
                self.state_id = info.get("state")
            else:
                maps, peak, self.exit_info = mx.exit_maps_info(self.rid, self.runid, state, float(view.get("smooth", 1.0)), hidden)
                # The texture of a spectral emitter holds spectral-upsampling coefficients, not RGB, so writing the new
                # map into the parameters would draw garbage: the scene is loaded again with the new bitmaps instead.
                for k, rgb in enumerate(maps):
                    if k not in hidden:
                        self.sdict[f"exit_{k}"]["emitter"]["radiance"]["bitmap"] = mi.Bitmap(rgb)
                self.scene = mi.load_dict(self.sdict)
                self.params = mi.traverse(self.scene)
                self.scale = float(peak)
                self.state_id = state
        except ValueError as exc:  # e.g. no light has reached the exit surface yet
            return str(exc)
        self.map_key = key
        self.acc, self.nspp = None, 0
        return None

    def _ensure_transport(self, view: dict) -> str | None:
        """The full light transport scene: the LEDs themselves are the emitters, so it does not depend on the tallies."""
        state = self._state(view)
        if self.t_scene is not None and self.t_state == state:
            return None
        mi, mx = self.mi, self.mx
        try:
            d, info = mx.build_mitsuba_scene(
                self.rid, self.setup, self.library, state,
                {"mode": "transport", "width": 640, "max_depth": TRANSPORT_DEPTH, "room_lux": BASE_ROOM_LUX},
                self.rdir / "live_meshes_t", self.runid,
            )
        except ValueError as exc:
            return str(exc)
        d.pop("sensor", None)
        self.t_scene = mi.load_dict(d)
        self.t_params = mi.traverse(self.t_scene)
        self.t_state = state
        self.acc, self.nspp = None, 0  # another light: the picture starts over
        return None

    def _transport_reference(self, view: dict) -> str | None:
        """The exposure of a transport picture in a dark room is tied to the bright level of the exit surfaces of the simulation, so that
        the two modes show the same brightness (and orbiting does not change it)."""
        stamp = self._tallies_stamp()
        if not stamp:
            return "The simulation has not saved any rays yet"
        state = self._state(view)
        key = (stamp, state, round(float(view.get("smooth", 1.0)), 2))
        if key != self.t_ref_key:
            try:
                _, _, info = self.mx.exit_maps_info(self.rid, self.runid, state, float(view.get("smooth", 1.0)))
            except ValueError as exc:
                return str(exc)
            self.t_ref, self.t_ref_key = float(info["reference"]) / TRANSPORT_UNITS, key
        return None

    # -- camera and light --------------------------------------------------------------------------------
    def _sensor(self, cam: dict, w: int, h: int):
        mi = self.mi
        pos, tgt, up = (list(map(float, cam[k])) for k in ("position", "target", "up"))
        ortho = cam.get("orthoHalfH")
        key = (tuple(pos), tuple(tgt), tuple(up), cam.get("fov"), ortho, w, h, cam.get("near", 0.0), cam.get("far", 0.0))
        sensor = self.sensors.get(key)
        if sensor is None:
            sensor = mi.load_dict(sensor_dict(mi, cam, w, h))
            if len(self.sensors) > 6:
                self.sensors.clear()
            self.sensors[key] = sensor
        return sensor

    def _environment(self, params, cam: dict, strength: float) -> None:
        """Turns the studio environment with the camera (so the light stays where it is relative to the view) and sets
        its strength: the radiance of a white surface facing the camera."""
        mi = self.mi
        if "environment.scale" not in params.keys():
            return
        m = self.mx.environment_matrix(cam["position"], cam["target"], cam["up"])
        params["environment.to_world"] = mi.Transform4f(m.tolist())
        params["environment.scale"] = float(max(strength, 0.0))
        params.update()

    # -- one request ---------------------------------------------------------------------------------------------
    def frame(self, req: dict) -> dict:
        t0 = time.time()
        mi, mx = self.mi, self.mx
        view, cam = req["view"], req["camera"]
        w, h = int(req["size"][0]), int(req["size"][1])
        transport = view.get("mode") == "transport"
        waiting = (self._ensure_transport(view) or self._transport_reference(view)) if transport else self._ensure_scene(view)
        if waiting:
            return {"ok": False, "waiting": waiting}
        scene = self.t_scene if transport else self.scene
        # The room light is a real one: a white card facing the camera has a luminance of lux / pi cd/m^2. The scene is in units of the
        # brightest exit-surface pixel (map) or of Mitsuba's emitters (transport, the much smaller unit TRANSPORT_UNITS).
        lux = max(float(view.get("room_lux", BASE_ROOM_LUX)), 0.0)
        card = mx.white_card_luminance(lux)
        strength = card / TRANSPORT_UNITS if transport else card / self.scale
        # exposure like a camera with fixed settings: the same whatever the room light and whichever surfaces are lit, so a dim room looks dim.
        # Exposure mode "surfaces" takes the "bright" level of the lit exit surfaces as the reference instead, and "room" exposes for the
        # room (a white card in the room light comes out mid-grey, as a camera that meters the room would do; a dark room keeps the fixed one).
        mode = str(view.get("exposure_mode", "fixed"))
        if mode == "surfaces":
            ref = self.t_ref if transport else float(self.exit_info.get("reference") or self.scale * 0.9)
        else:
            lum = card if (mode == "room" and card > 0) else mx.FIXED_EXPOSURE_LUMINANCE
            ref = lum / TRANSPORT_UNITS if transport else lum
        bucket = round(math.log(max(strength, 1e-300)) / 0.15) if strength > 0 else None  # a slowly improving reference must not restart the picture every pass
        ckey = (tuple(cam["position"]), tuple(cam["target"]), tuple(cam["up"]), cam.get("fov"), cam.get("orthoHalfH"), w, h, bucket, transport)
        if req.get("reset") or ckey != self.cam_key or self.acc is None:
            self.acc, self.nspp, self.cam_key = None, 0, ckey
            self._environment(self.t_params if transport else (self.params if self.room_params is None else self.room_params), cam, strength)
        sensor = self._sensor(cam, w, h)
        spp = max(1, int(req.get("spp", 4)))
        # the GPU draws all samples of a pass at once: a big picture is split so that memory stays small
        per_pass = max(1, min(spp, (1 << 22) // max(w * h, 1)))
        img, done = None, 0
        while done < spp:
            n = min(per_pass, spp - done)
            part = np.array(mi.render(scene, sensor=sensor, spp=n, seed=self.nspp + done), dtype=np.float32)
            if self.room_scene is not None and not transport:
                part = part + np.array(mi.render(self.room_scene, sensor=sensor, spp=n, seed=self.nspp + done + 7919), dtype=np.float32)
            img = part * n if img is None else img + part * n
            done += n
        img = np.nan_to_num(img[..., :3] / spp, nan=0.0, posinf=0.0, neginf=0.0)
        self.acc = img * spp if self.acc is None else self.acc + img * spp
        self.nspp += spp
        rgb = (self.acc / self.nspp).astype(np.float64)
        # a fixed reference, not the picture's own percentile: orbiting must not change the brightness
        if transport:
            png = mx.tonemap(rgb, float(view.get("exposure", 0.0)), 99.5, bool(view.get("white_balance", True)), reference=ref)
        else:
            png = mx.tonemap(rgb * self.scale, float(view.get("exposure", 0.0)), 99.5, bool(view.get("white_balance", True)), reference=ref)
        from PIL import Image

        buf = io.BytesIO()
        Image.fromarray(png).save(buf, format="JPEG", quality=int(req.get("quality", 85)))
        live = store.read_json(self.rdir / "live.json", {}) or {}
        lum = {"card": card, "lux": lux, "surfaces": [] if transport else self.exit_info.get("surfaces", [])}  # the numbers behind the picture (cd/m^2)
        return {"ok": True, "jpeg": buf.getvalue(), "spp": self.nspp, "ms": round((time.time() - t0) * 1000), "rays": live.get("rays"), "variant": self.variant, "lum": lum}


def worker_main(rid: str, runid: str) -> int:
    """Entry point of the helper process (``--run-job liveview <rid> <runid>``)."""
    conn = Client(("127.0.0.1", int(os.environ["REFRACT_LV_PORT"])), authkey=bytes.fromhex(os.environ["REFRACT_LV_KEY"]))
    try:
        worker = _Worker(rid, runid)
        conn.send({"ready": True, "variant": worker.variant})
    except Exception as exc:
        conn.send({"error": f"{type(exc).__name__}: {exc}"})
        return 1
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            return 0
        if msg.get("cmd") == "quit":
            return 0
        try:
            conn.send(worker.frame(msg))
        except (EOFError, OSError):
            return 0
        except Exception as exc:  # report it and keep serving
            import traceback

            try:
                conn.send({"ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1500:]})
            except (EOFError, OSError):
                return 0


# ═════════════════════════════ the server side ════════════════════════════════════════════════
class _Session:
    def __init__(self, rid: str, runid: str) -> None:
        self.rid, self.runid = rid, runid
        self.status = "starting"  # starting | ready | error
        self.error = ""
        self.variant = ""
        self.proc: subprocess.Popen | None = None
        self.conn = None
        self.lock = threading.Lock()
        self.last_used = time.time()
        threading.Thread(target=self._start, daemon=True, name="live-view-start").start()

    def _start(self) -> None:
        listener = None
        try:
            key = secrets.token_bytes(16)
            listener = Listener(("127.0.0.1", 0), authkey=key)
            env = dict(os.environ)
            env["REFRACT_LV_PORT"] = str(listener.address[1])
            env["REFRACT_LV_KEY"] = key.hex()
            server_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            env["PYTHONPATH"] = server_dir + os.pathsep + env.get("PYTHONPATH", "")
            kwargs: dict = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            logdir = store.run_dir(self.rid, self.runid)
            log = open(logdir / "liveview.stderr.log", "wb")
            self.proc = subprocess.Popen(
                procutil.child_command("run-job", "liveview", self.rid, self.runid),
                cwd=None if procutil.is_frozen() else server_dir,
                env=env, stdout=log, stderr=log, **kwargs,
            )
            log.close()  # the helper has its own handle; ours would keep the folder from being deleted
            listener._listener._socket.settimeout(120)  # type: ignore[attr-defined]
            self.conn = listener.accept()
            msg = self.conn.recv()
            if msg.get("ready"):
                self.variant = msg.get("variant", "")
                self.status = "ready"
            else:
                self.error = msg.get("error", "The live view could not start")
                self.status = "error"
        except Exception as exc:
            self.error = f"The live view could not start ({type(exc).__name__}: {exc})"
            self.status = "error"
        finally:
            if listener is not None:
                listener.close()
            if self.status == "error":
                self.close()

    def frame(self, req: dict) -> dict:
        self.last_used = time.time()
        with self.lock:
            try:
                self.conn.send(req)
                return self.conn.recv()
            except (EOFError, OSError, BrokenPipeError):
                self.status, self.error = "error", "The live view stopped unexpectedly. See liveview.stderr.log in the run's folder."
                self.close()
                return {"ok": False, "error": self.error, "fatal": True}

    def close(self) -> None:
        conn, proc = self.conn, self.proc
        self.conn = None
        try:
            if conn is not None:
                conn.send({"cmd": "quit"})
                conn.close()
        except Exception:
            pass
        if proc is not None and proc.poll() is None:
            try:
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


_sessions_lock = threading.Lock()
_session: _Session | None = None
_watchdog_started = False


def _watchdog() -> None:
    global _session
    while True:
        time.sleep(15)
        with _sessions_lock:
            s = _session
            if s is not None and time.time() - s.last_used > IDLE_SECONDS:
                _session = None
                threading.Thread(target=s.close, daemon=True).start()


def request_frame(rid: str, runid: str, req: dict) -> dict:
    """-> {"status": "starting"} | {"status": "waiting", "reason"} | {"status": "error", "error"} | {"status": "ok", "jpeg", ...}"""
    global _session, _watchdog_started
    with _sessions_lock:
        if not _watchdog_started:
            threading.Thread(target=_watchdog, daemon=True, name="live-view-watchdog").start()
            _watchdog_started = True
        s = _session
        if s is not None and (s.rid, s.runid) != (rid, runid):
            threading.Thread(target=s.close, daemon=True).start()
            s = _session = None
        if s is not None and s.status == "error":
            err = s.error
            _session = None  # the next request tries again
            return {"status": "error", "error": err}
        if s is None:
            s = _session = _Session(rid, runid)
        s.last_used = time.time()
    if s.status == "starting":
        return {"status": "starting"}
    reply = s.frame(req)
    if reply.get("ok"):
        return {"status": "ok", **reply}
    if reply.get("waiting"):
        return {"status": "waiting", "reason": reply["waiting"]}
    return {"status": "error", "error": reply.get("error", "The live view failed")}


def close_session(runid: str | None = None, wait: bool = False) -> None:
    """Closes the helper process (of one run). `wait`: return only when it has gone, so that the run's folder can be deleted."""
    global _session
    with _sessions_lock:
        s = _session
        if s is None or not (runid is None or s.runid == runid):
            return
        _session = None
    if wait:
        s.close()
    else:
        threading.Thread(target=s.close, daemon=True).start()
