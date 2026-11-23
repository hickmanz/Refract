"""LED models and emitters.

A library LED has one or more **channels** (die groups such as R, G, B or one white die). Each channel has a
luminous flux, a spectrum and an angular intensity pattern. A setup places LED instances on the CAD (an emitting
face of the LED body, or a free frame); a *state* then scales channel drive levels. Because light transport is
linear, every channel is traced once and states are linear combinations of the channel results.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..optical_mesh import triangle_normals_areas
from . import faceref, kernels, rayfile, spectral
from .scene import Scene, name_matches

THETA = np.radians(np.linspace(0.0, 180.0, 721))  # pattern grid, 0.25 degree


# -- pattern and spectrum tables ---------------------------------------------------------------------
def pattern_intensity(pattern: dict | None) -> np.ndarray:
    """Relative intensity I(theta) on THETA from a pattern description (axis-symmetric)."""
    if pattern is None:
        pattern = {"type": "lambertian", "exponent": 1.0}
    kind = pattern.get("type", "lambertian")
    if kind == "lambertian":
        m = float(pattern.get("exponent", 1.0))
        if m < 0:
            raise ValueError("pattern exponent must be >= 0")
        c = np.cos(THETA)
        return np.where(c > 0, c, 0.0) ** m
    if kind == "half_angle":  # cos^m with m from the half-intensity angle
        half = float(pattern.get("half_angle_deg", 60.0))
        if not 1.0 <= half < 89.0:
            raise ValueError("half angle must be between 1 and 89 degrees")
        m = np.log(0.5) / np.log(np.cos(np.radians(half)))
        c = np.cos(THETA)
        return np.where(c > 0, c, 0.0) ** m
    if kind == "table":
        ang = np.radians(np.asarray(pattern["angles"], dtype=np.float64))
        val = np.asarray(pattern["intensity"], dtype=np.float64)
        if len(ang) != len(val) or len(ang) < 2:
            raise ValueError("pattern table needs matching angles and intensity lists (at least 2 points)")
        order = np.argsort(ang)
        return np.clip(np.interp(THETA, ang[order], val[order], right=0.0), 0.0, None)
    raise ValueError(f"unknown pattern type {kind!r}")


def pattern_cdf(intensity: np.ndarray) -> np.ndarray:
    w = intensity * np.sin(THETA)
    cdf = np.concatenate([[0.0], np.cumsum(0.5 * (w[1:] + w[:-1]) * np.diff(THETA))])
    if cdf[-1] <= 0:
        raise ValueError("pattern has no intensity")
    return cdf / cdf[-1]


def spectrum_cdf(s: np.ndarray) -> np.ndarray:
    cdf = np.cumsum(s)
    return cdf / cdf[-1]


def validate_led(doc: dict) -> None:
    chans = doc.get("channels")
    if not chans:
        raise ValueError("an LED needs at least one channel")
    seen = set()
    for ch in chans:
        cid = str(ch.get("id", "")).strip()
        if not cid or "/" in cid or cid in seen:
            raise ValueError("channel ids must be unique, non-empty and contain no '/'")
        seen.add(cid)
        if float(ch.get("flux_lm", 0)) <= 0:
            raise ValueError(f"channel {cid}: luminous flux must be > 0")
        spectral.spectrum_on_grid(ch.get("spectrum"))
        pattern_cdf(pattern_intensity(ch.get("pattern")))
        src = ch.get("source")
        if src and src.get("type") == "rays":
            _ray_weights(rayfile.load(src.get("file")))


def _ray_weights(rays: np.ndarray) -> np.ndarray:
    """Sampling CDF over the rays that leave the LED (forward hemisphere), weighted by flux."""
    w = np.where(rays[:, 5] > 1e-6, rays[:, 6].astype(np.float64), 0.0)
    if w.sum() <= 0:
        raise ValueError("The ray set has no rays pointing out of the LED (+z)")
    return np.cumsum(w) / w.sum()


# -- emitters --------------------------------------------------------------------------------------------
@dataclass
class ChannelSource:
    key: str  # "<led id>/<channel id>"
    led_id: str
    led_name: str
    channel_id: str
    channel_name: str
    flux_lm: float
    flux_w: float
    efficacy: float
    spec: np.ndarray  # normalised spectrum on the grid
    spec_cdf: np.ndarray
    pat_cdf: np.ndarray
    mode: int
    tri_v0: np.ndarray
    tri_e1: np.ndarray
    tri_e2: np.ndarray
    tri_cdf: np.ndarray
    rect_o: np.ndarray
    rect_u: np.ndarray
    rect_v: np.ndarray
    axis: np.ndarray
    ex: np.ndarray
    ey: np.ndarray
    start_body: int | None
    origin: np.ndarray = field(default_factory=lambda: np.zeros(3))
    rays: np.ndarray | None = None  # measured ray set (N x 8) when the channel has one
    rays_cdf: np.ndarray | None = None


def _frame(axis: np.ndarray, up: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    if up is None or abs(float(np.dot(axis, up))) > 0.99:
        up = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    ex = np.cross(up, axis)
    ex /= np.linalg.norm(ex)
    ey = np.cross(axis, ex)
    return ex, ey


def led_placements(setup: dict, opt_doc: dict, scene: Scene, opt: dict[str, np.ndarray], library: dict) -> list[dict]:
    """Resolve every LED placement of a setup to a concrete instance: [{led, doc, inst, body, tris/frame...}]."""
    out = []
    insts = opt_doc["instances"]
    for led in setup.get("leds", []):
        if led.get("enabled", True) is False:
            continue
        model = library["leds"].get(led.get("model"))
        if model is None:
            raise ValueError(f"LED {led.get('name', led.get('id'))!r}: model {led.get('model')!r} is not in the library")
        place = led.get("placement") or {}
        entry = {"led": led, "model": model, "inst": None, "tris": None, "frame": None, "start_body": None}
        if place.get("type", "face") == "face":
            matched = [i for i in insts if name_matches(place.get("part", ""), i)]
            if len(matched) != 1:
                raise ValueError(
                    f"LED {led.get('name', led.get('id'))!r}: part pattern {place.get('part')!r} matches {len(matched)} parts (it must match exactly one)"
                )
            inst = matched[0]
            if inst["ordinal"] not in scene.instance_to_body:
                raise ValueError(f"LED {led.get('name', led.get('id'))!r}: its part is disabled in the setup")
            t0, tn = inst["triOffset"], inst["triCount"]
            fidx = faceref.resolve_face(opt, inst, int(place["face"]), place.get("sig"), scene.diag, faceref.led_label(led), scene.warnings)
            sel = np.nonzero(opt["tri_face"][t0 : t0 + tn] == fidx)[0] + t0 if fidx is not None else np.zeros(0, dtype=int)
            if len(sel) == 0:
                raise ValueError(f"LED {led.get('name', led.get('id'))!r}: face {place.get('face')} does not exist on {inst['path']}")
            entry["inst"], entry["tris"] = inst, sel
            scene.resolved.setdefault("leds", {})[str(led.get("id", led.get("name")))] = {"instance": inst["id"], "face": int(fidx)}
        elif place["type"] == "frame":
            entry["frame"] = place
        else:
            raise ValueError(f"unknown placement type {place['type']!r}")
        sb = place.get("start_body")
        if sb:
            m = [i for i in insts if name_matches(sb, i) and i["ordinal"] in scene.instance_to_body]
            if len(m) != 1:
                raise ValueError(f"LED {led.get('name', led.get('id'))!r}: start part {sb!r} must match exactly one enabled part")
            entry["start_body"] = scene.instance_to_body[m[0]["ordinal"]]
        out.append(entry)
    return out


def build_channel_sources(setup: dict, opt_doc: dict, scene: Scene, opt: dict[str, np.ndarray], library: dict) -> list[ChannelSource]:
    placements = led_placements(setup, opt_doc, scene, opt, library)
    sources: list[ChannelSource] = []
    ids = set()
    for pl in placements:
        led, model = pl["led"], pl["model"]
        lid = str(led.get("id") or led.get("name"))
        if lid in ids:
            raise ValueError(f"duplicate LED id {lid!r}")
        ids.add(lid)
        flip = bool(led.get("placement", {}).get("flip", False))
        mode = 0
        dummy3 = np.zeros((1, 3))
        tri_v0 = tri_e1 = tri_e2 = dummy3
        tri_cdf = np.ones(1)
        rect_o = rect_u = rect_v = np.zeros(3)
        if pl["tris"] is not None:
            tris = opt["tris"][pl["tris"]]
            v = opt["verts"].astype(np.float64)
            nrm, area = triangle_normals_areas(v, tris)
            tri_v0, tri_e1, tri_e2 = v[tris[:, 0]], v[tris[:, 1]] - v[tris[:, 0]], v[tris[:, 2]] - v[tris[:, 0]]
            tri_cdf = np.cumsum(area) / area.sum()
            axis = (nrm * area[:, None]).sum(axis=0)
            axis /= np.linalg.norm(axis)
            origin = (v[tris].mean(axis=1) * area[:, None]).sum(axis=0) / area.sum()
            up = None
        else:
            fr = pl["frame"]
            origin = np.asarray(fr["origin"], dtype=np.float64)
            axis = np.asarray(fr["axis"], dtype=np.float64)
            axis = axis / np.linalg.norm(axis)
            up = np.asarray(fr["up"], dtype=np.float64) if fr.get("up") is not None else None
            shape = (model.get("emitter") or {}).get("shape", "rect")
            size = fr.get("size") or (model.get("emitter") or {}).get("size", [1.0, 1.0])
            ex0, ey0 = _frame(axis, up)
            rect_o, rect_u, rect_v = origin, ex0 * float(size[0]), ey0 * float(size[1])
            mode = 2 if shape in ("disc", "ellipse") else 1
        if flip:
            axis = -axis
        ex, ey = _frame(axis, up)
        rot = np.radians(float(led.get("placement", {}).get("rotation_deg", 0.0)))
        if rot:
            ex, ey = np.cos(rot) * ex + np.sin(rot) * ey, -np.sin(rot) * ex + np.cos(rot) * ey
        scale = float(led.get("scale", 1.0))
        for ch in model["channels"]:
            spec = spectral.spectrum_on_grid(ch.get("spectrum"))
            eff = spectral.luminous_efficacy(spec)
            flux_lm = float(ch["flux_lm"]) * scale
            rays = rays_cdf = None
            if (ch.get("source") or {}).get("type") == "rays":
                rays = rayfile.load(ch["source"]["file"])
                rays_cdf = _ray_weights(rays)
                if np.isfinite(rays[:, 7]).all():
                    spec = spectral.spectrum_on_grid(ch["source"].get("spectrum") or ch.get("spectrum"))
                    eff = spectral.luminous_efficacy(spec)
            sources.append(
                ChannelSource(
                    key=f"{lid}/{ch['id']}",
                    led_id=lid,
                    led_name=str(led.get("name", lid)),
                    channel_id=str(ch["id"]),
                    channel_name=str(ch.get("name", ch["id"])),
                    flux_lm=flux_lm,
                    flux_w=flux_lm / eff,
                    efficacy=eff,
                    spec=spec,
                    spec_cdf=spectrum_cdf(spec),
                    pat_cdf=pattern_cdf(pattern_intensity(ch.get("pattern"))),
                    mode=mode,
                    tri_v0=np.ascontiguousarray(tri_v0),
                    tri_e1=np.ascontiguousarray(tri_e1),
                    tri_e2=np.ascontiguousarray(tri_e2),
                    tri_cdf=tri_cdf,
                    rect_o=rect_o,
                    rect_u=rect_u,
                    rect_v=rect_v,
                    axis=axis,
                    ex=ex,
                    ey=ey,
                    start_body=pl["start_body"],
                    origin=origin,
                    rays=rays,
                    rays_cdf=rays_cdf,
                )
            )
    if not sources:
        raise ValueError("The setup has no LEDs. Add at least one LED and place it on a part.")
    return sources


def emitter_extent(src: ChannelSource) -> list[float]:
    """Width and height (mm) of the emitting area along the LED's ``ex`` / ``ey`` axes, for drawing the LED marker."""
    if src.mode == 0:
        pts = np.concatenate([src.tri_v0, src.tri_v0 + src.tri_e1, src.tri_v0 + src.tri_e2], axis=0) - src.origin
        a, b = pts @ src.ex, pts @ src.ey
        return [float(a.max() - a.min()), float(b.max() - b.min())]
    return [float(np.linalg.norm(src.rect_u)), float(np.linalg.norm(src.rect_v))]


def _ray_uniform(seed: int, ids: np.ndarray) -> np.ndarray:
    """One uniform number in [0, 1) per ray id (splitmix64 of seed and id; independent of how the rays are batched)."""
    with np.errstate(over="ignore"):
        z = (np.uint64(int(seed) & 0xFFFFFFFFFFFFFFFF) * np.uint64(0xD6E8FEB86659FD93)) ^ (ids.astype(np.uint64) * np.uint64(0x9E3779B97F4A7C15) + np.uint64(7))
        z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        z = z ^ (z >> np.uint64(31))
    return (z >> np.uint64(11)).astype(np.float64) * 1.1102230246251565e-16


def emit(src: ChannelSource, n: int, seed: int, first_id: int, eps: float):
    """Rays first_id .. first_id + n - 1 of the channel's stream."""
    return emit_ids(src, np.arange(first_id, first_id + n, dtype=np.int64), seed, eps)


def emit_ids(src: ChannelSource, ids: np.ndarray, seed: int, eps: float):
    """The rays with the given numbers (ray k is always the same ray, whichever batch it is traced in)."""
    ids = np.ascontiguousarray(ids, dtype=np.int64)
    n = len(ids)
    o = np.empty((n, 3))
    d = np.empty((n, 3))
    lam = np.empty(n)
    rng = np.empty(n, dtype=np.uint64)
    kernels.emit_kernel(
        n, int(seed), ids,
        src.mode, src.tri_v0, src.tri_e1, src.tri_e2, src.tri_cdf, src.rect_o, src.rect_u, src.rect_v,
        src.axis, src.ex, src.ey, eps,
        THETA, src.pat_cdf, src.spec_cdf,
        o, d, lam, rng,
    )
    if src.rays is not None:
        idx = np.minimum(np.searchsorted(src.rays_cdf, _ray_uniform(seed, ids)), len(src.rays) - 1)
        s = src.rays[idx].astype(np.float64)
        o[:] = src.origin + s[:, 0:1] * src.ex + s[:, 1:2] * src.ey + s[:, 2:3] * src.axis + src.axis * eps
        d[:] = s[:, 3:4] * src.ex + s[:, 4:5] * src.ey + s[:, 5:6] * src.axis
        has = np.isfinite(s[:, 7])
        if has.any():  # a ray with a wavelength keeps it, the others take one from the LED spectrum
            lam[has] = np.clip(s[has, 7], spectral.LAM0, spectral.LAM0 + spectral.DLAM * (spectral.NL - 1))
    return o, d, lam, rng


# -- states ------------------------------------------------------------------------------------------------
def resolve_state_drive(state: dict, sources: list[ChannelSource]) -> dict[str, float]:
    """Drive level per channel key for one state. Keys of ``state['drive']`` are ``led/channel`` with ``*`` wildcards."""
    drive = state.get("drive") or {}
    out: dict[str, float] = {}
    for s in sources:
        best = None
        for pat, val in drive.items():
            led_p, _, ch_p = pat.partition("/")
            if not ch_p:
                ch_p = "*"
            ok_led = led_p in ("*", s.led_id)
            ok_ch = ch_p in ("*", s.channel_id)
            if ok_led and ok_ch:
                spec = (led_p != "*") * 2 + (ch_p != "*")
                if best is None or spec >= best[0]:
                    best = (spec, float(val))
        out[s.key] = best[1] if best else 0.0
    return out
