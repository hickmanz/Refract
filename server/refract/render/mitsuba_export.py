"""Builds a Mitsuba 3 scene from a Refract setup and renders a camera view of it.

The Monte Carlo engine in ``refract.engine`` stays the source of the numbers. This is the picture: what the
exit surface looks like to a camera, with the same geometry, materials, finishes and LED drive levels.

What carries over exactly: every part's triangles; a transparent part becomes a dielectric with the
material's refractive index (at 550 nm), a homogeneous medium with its absorption and scattering coefficients
(spectral) and Henyey-Greenstein anisotropy; finishes become smooth or Beckmann-rough surfaces; opaque finishes
become diffuse / specular blends. Each LED is a spectral area emitter on its emitting face with its flux.

What is approximate: Mitsuba's area emitter is Lambertian, so a narrow or table angular pattern is replaced by
a cosine distribution of the same total flux (reported in ``warnings``); the refractive index has no
dispersion; the camera sees only the parts' own light (a dark room).
"""

from __future__ import annotations

import importlib.util
import math
import os
from pathlib import Path

import numpy as np

from .. import store
from ..engine import sources, spectral
from ..envutil import env
from ..engine.scene import Scene, build_scene
from ..optical_mesh import load_optical
from ..simrun import effective_states

LAM = spectral.LAM


_VARIANT: str | None = None


def select_variant() -> str:
    """Use the GPU (CUDA, NVIDIA only) when it works, otherwise the CPU; ``REFRACT_MITSUBA_VARIANT`` forces a variant."""
    global _VARIANT
    import mitsuba as mi

    if _VARIANT is None:
        wanted = env("MITSUBA_VARIANT")
        candidates = [wanted] if wanted else ["cuda_ad_spectral", "scalar_spectral"]
        for v in candidates:
            try:
                mi.set_variant(v)
                if v.startswith("cuda"):  # a tiny render proves the driver, OptiX and the GPU are usable
                    mi.render(mi.load_dict(mi.cornell_box()), spp=1)
                _VARIANT = v
                break
            except Exception:
                continue
        if _VARIANT is None:
            mi.set_variant("scalar_spectral")
            _VARIANT = "scalar_spectral"
    elif mi.variant() != _VARIANT:
        mi.set_variant(_VARIANT)
    return _VARIANT


_AVAILABLE: tuple[bool, str] | None = None


def available() -> tuple[bool, str]:
    """Whether Mitsuba 3 can be imported (checked once; an incomplete install counts as missing)."""
    global _AVAILABLE
    if _AVAILABLE is None:
        if importlib.util.find_spec("mitsuba") is None:
            _AVAILABLE = (False, "Mitsuba 3 is not installed. Install it with: pip install mitsuba")
        else:
            try:
                importlib.import_module("mitsuba")
                _AVAILABLE = (True, "")
            except Exception as exc:  # incomplete or incompatible install
                _AVAILABLE = (False, f"Mitsuba 3 could not be loaded ({type(exc).__name__}: {exc}). Rendering a picture is not available.")
    return _AVAILABLE


def _regular(values: np.ndarray) -> dict:
    return {
        "type": "regular",
        "wavelength_min": float(LAM[0]),
        "wavelength_max": float(LAM[-1]),
        "values": ", ".join(f"{float(v):.8g}" for v in values),
    }


def _write_ply(path: Path, verts: np.ndarray, tris: np.ndarray) -> None:
    import trimesh

    mesh = trimesh.Trimesh(vertices=verts, faces=tris, process=False)
    mesh.export(str(path))


def _surface_bsdf(code: int, p: np.ndarray, material_n550: float | None, spec: np.ndarray | None = None) -> dict:
    sigma = float(p[0] if code == 0 else p[2])
    alpha = max(math.sqrt(2.0) * sigma, 1e-3)
    if code == 0:  # dielectric boundary
        n = material_n550 or 1.5
        if sigma < 1e-3:
            return {"type": "dielectric", "int_ior": float(n), "ext_ior": 1.0}
        return {"type": "roughdielectric", "distribution": "beckmann", "alpha": alpha, "int_ior": float(n), "ext_ior": 1.0}
    rd, rs = float(p[0]), float(p[1])
    # colour: the diffuse / specular level times the colour of the finish's spectral shape (white when it has none)
    cd = np.array([rd, rd, rd]) * (spectral.reflectance_to_linear_rgb(spec[0]) if spec is not None else 1.0)
    cs = np.array([rs, rs, rs]) * (spectral.reflectance_to_linear_rgb(spec[1]) if spec is not None else 1.0)
    tot = cd + cs
    if float(tot.max()) > 1.0:
        cd, cs = cd / tot.max(), cs / tot.max()
    a = rd + rs
    rgb = lambda v: {"type": "rgb", "value": [float(x) for x in np.clip(v, 0.0, 1.0)]}  # noqa: E731
    if a <= 1e-9:
        inner: dict = {"type": "diffuse", "reflectance": rgb([0.0, 0.0, 0.0])}
    elif rs <= 1e-9:
        inner = {"type": "diffuse", "reflectance": rgb(cd)}
    else:
        w = rs / a  # the blend picks the mirror part with weight w
        spec_bsdf = (
            {"type": "conductor", "material": "none", "specular_reflectance": rgb(cs / w)}
            if sigma < 1e-3
            else {"type": "roughconductor", "material": "none", "distribution": "beckmann", "alpha": alpha, "specular_reflectance": rgb(cs / w)}
        )
        if rd <= 1e-9:
            inner = {**spec_bsdf, "specular_reflectance": rgb(cs)}
        else:
            inner = {"type": "blendbsdf", "weight": w, "bsdf_0": {"type": "diffuse", "reflectance": rgb(cd / (1.0 - w))}, "bsdf_1": spec_bsdf}
    return {"type": "twosided", "bsdf": inner}


def _medium(scene: Scene, mat: int) -> dict | None:
    ka, mus, g = scene.mat_ka[mat], scene.mat_mus[mat], scene.mat_g[mat]
    st = ka + mus
    if float(st.max()) <= 0.0:
        return None
    albedo = np.divide(mus, st, out=np.zeros_like(st), where=st > 0)
    # a clear, purely absorbing material has albedo 0 at every wavelength, which Mitsuba's "regular" spectrum rejects
    # ("no probability mass found"); a tiny positive floor is the same physics
    albedo = np.maximum(albedo, 1e-6)
    g550 = float(np.interp(550.0, LAM, g))
    return {
        "type": "homogeneous",
        "sigma_t": _regular(st),
        "albedo": _regular(albedo),
        "phase": {"type": "hg", "g": g550},
        "scale": 1.0,
    }


def _emitters(srcs: list[sources.ChannelSource], drive: dict[str, float], eps: float, outdir: Path, warnings: list[str]) -> dict:
    out: dict = {}
    by_led: dict[str, list[sources.ChannelSource]] = {}
    for s in srcs:
        by_led.setdefault(s.led_id, []).append(s)
    for lid, chans in by_led.items():
        active = [(c, drive.get(c.key, 0.0)) for c in chans if drive.get(c.key, 0.0) > 0]
        if not active:
            continue
        c0 = chans[0]
        if c0.mode == 0:
            v0, e1, e2 = c0.tri_v0, c0.tri_e1, c0.tri_e2
            verts = np.concatenate([v0, v0 + e1, v0 + e2])
            n = len(v0)
            tris = np.stack([np.arange(n), np.arange(n) + n, np.arange(n) + 2 * n], axis=1)
            nrm = np.cross(e1, e2)
            area = 0.5 * np.linalg.norm(nrm, axis=1).sum()
            flip = np.einsum("ij,j->i", nrm, c0.axis) < 0
            tris[flip] = tris[flip][:, [0, 2, 1]]
        else:  # frame placement: rectangle (or the bounding square of a disc)
            o, u, v = c0.rect_o, c0.rect_u, c0.rect_v
            verts = np.array([o - 0.5 * u - 0.5 * v, o + 0.5 * u - 0.5 * v, o + 0.5 * u + 0.5 * v, o - 0.5 * u + 0.5 * v])
            tris = np.array([[0, 1, 2], [0, 2, 3]])
            if float(np.dot(np.cross(u, v), c0.axis)) < 0:
                tris = tris[:, [0, 2, 1]]
            area = float(np.linalg.norm(np.cross(u, v))) * (math.pi / 4 if c0.mode == 2 else 1.0)
        verts = verts + c0.axis * eps * 4
        # radiance spectrum in W / (mm^2 sr nm): sum of the active channels
        spec = np.zeros(spectral.NL)
        for c, d in active:
            spec += d * c.flux_w * c.spec / spectral.DLAM
        radiance = spec / (math.pi * max(area, 1e-9))
        path = outdir / f"led_{lid}.ply"
        _write_ply(path, verts, tris)
        out[f"led_{lid}"] = {
            "type": "ply",
            "filename": str(path),
            "bsdf": {"type": "diffuse", "reflectance": {"type": "rgb", "value": [0.0, 0.0, 0.0]}},
            "emitter": {"type": "area", "radiance": _regular(radiance)},
        }
    if any(c.rays is not None for c in srcs):
        warnings.append("Measured ray sets are drawn as Lambertian area emitters here; their exact angular distribution is only used by the simulation.")
    return out


def _write_quad_uv(path: Path, corners: np.ndarray, normal: np.ndarray) -> None:
    """One quad with texture coordinates (corner 0 = (0,0), corner 2 = (1,1)) and a given facing, as ASCII PLY."""
    uv = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    idx = [(0, 1, 2), (0, 2, 3)]
    if float(np.dot(np.cross(corners[1] - corners[0], corners[3] - corners[0]), normal)) < 0:
        idx = [(0, 2, 1), (0, 3, 2)]
    lines = ["ply", "format ascii 1.0", "element vertex 4", "property float x", "property float y", "property float z", "property float u", "property float v", "element face 2", "property list uchar int vertex_indices", "end_header"]
    for p, (u, v) in zip(corners, uv):
        lines.append(f"{p[0]:.6f} {p[1]:.6f} {p[2]:.6f} {u} {v}")
    for a, b, c3 in idx:
        lines.append(f"3 {a} {b} {c3}")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def _write_soup_uv(path: Path, mesh: dict, offset: float) -> None:
    """The triangles of an unwrapped exit surface (separate vertices, texture coordinates in 0..1), pushed out along their normals."""
    pos = np.asarray(mesh["positions"], dtype=np.float64).reshape(-1, 3) + np.asarray(mesh["normals"], dtype=np.float64).reshape(-1, 3) * offset
    uv = np.asarray(mesh["uv"], dtype=np.float64).reshape(-1, 2)
    nt = len(pos) // 3
    lines = ["ply", "format ascii 1.0", f"element vertex {len(pos)}", "property float x", "property float y", "property float z", "property float u", "property float v", f"element face {nt}", "property list uchar int vertex_indices", "end_header"]
    lines += [f"{p[0]:.6f} {p[1]:.6f} {p[2]:.6f} {t[0]:.6f} {t[1]:.6f}" for p, t in zip(pos, uv)]
    lines += [f"3 {3 * i} {3 * i + 1} {3 * i + 2}" for i in range(nt)]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


_XYZ_TO_RGB = np.array([[3.2404542, -1.5371385, -0.4985314], [-0.9692660, 1.8760108, 0.0415560], [0.0556434, -0.2040259, 1.0572252]])


def hidden_exits(view: dict) -> tuple[int, ...]:
    """Indices of the exit surfaces (detectors) the picture leaves dark: ``view["hidden_exits"]``, sorted and without duplicates."""
    return tuple(sorted({int(i) for i in (view.get("hidden_exits") or [])}))


TRANSPORT_UNITS = 9.0e10  # see live_view: Mitsuba's radiance of the LED emitters against the exit-surface map in cd/m^2 (a unit conversion)
DEFAULT_ROOM_LUX = 500.0  # a normal office: the illuminance on a surface that faces the camera
# The exposure of a picture is fixed, like a camera that is not changed between shots: this luminance (cd/m^2) becomes the reference level
# (0.6 of the display range). It is the white card of a 500 lux room, so the default room is well exposed, a dim room looks dim and a
# bright one burns out, and the glow of a surface has the same brightness whatever else is lit. "Auto exposure" picks the level from the
# lit surfaces instead.
FIXED_EXPOSURE_LUMINANCE = 160.0


def white_card_luminance(room_lux: float) -> float:
    """cd/m^2 of a white (reflectance 1) Lambertian card facing the camera in a room of ``room_lux`` lux: E / pi."""
    return max(float(room_lux), 0.0) / math.pi


def _weighted_percentile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    """The ``q`` percent point of ``values`` when every value counts ``weights`` (here: the area of its pixel)."""
    values, weights = np.asarray(values, dtype=np.float64).ravel(), np.asarray(weights, dtype=np.float64).ravel()
    keep = (weights > 0) & np.isfinite(values)
    if not keep.any():
        return 0.0
    values, weights = values[keep], weights[keep]
    order = np.argsort(values)
    cum = np.cumsum(weights[order])
    return float(values[order][min(int(np.searchsorted(cum, cum[-1] * q / 100.0)), len(values) - 1)])


# Mitsuba's spectral variants turn the RGB of a bitmap emitter into a smooth spectrum but, unlike for a plain RGB colour, do not weight it
# with the D65 illuminant, so equal RGB comes out as an equal-energy (reddish) light: white 1 : 1 : 1 renders as 1 : 0.79 : 0.76, and the
# yellow of red + green as orange. The response of the render to a bitmap colour is almost linear (the mean error of this matrix is
# 0.3 %, the worst 2.6 %), measured by rendering a patch of every colour in cuda_ad_spectral: rendered = bitmap @ this matrix.
# (The RGB variants are exact, but only the spectral ones can run the full light transport mode.)
_SPECTRAL_BITMAP_RESPONSE = np.array([[1.151, -0.002, -0.002], [0.051, 0.956, -0.012], [0.017, -0.006, 0.916]])


def _spectral_bitmap_correction() -> np.ndarray | None:
    """The matrix to apply to the colours of a bitmap emitter so that the render shows them as they are, or None when the Mitsuba variant
    in use renders them exactly (an RGB variant) or Mitsuba is not in use."""
    try:
        import mitsuba as mi

        variant = mi.variant()
    except Exception:
        return None
    return np.linalg.inv(_SPECTRAL_BITMAP_RESPONSE) if variant and "spectral" in variant else None


def exit_maps(rid: str, runid: str, state_id: str | None, view_smooth: float = 1.0, hidden: tuple[int, ...] = ()) -> tuple[list[np.ndarray], float]:
    """The luminance map of every exit surface of a run, normalised to 0..1, and the peak (cd/m^2) they were divided by.

    See :func:`exit_maps_info` for what else is known about them.
    """
    maps, peak, _ = exit_maps_info(rid, runid, state_id, view_smooth, hidden)
    return maps, peak


def exit_maps_info(rid: str, runid: str, state_id: str | None, view_smooth: float = 1.0, hidden: tuple[int, ...] = ()) -> tuple[list[np.ndarray], float, dict]:
    """``(maps, peak, info)``: the map of every exit surface normalised to 0..1, the peak (cd/m^2) they were divided by, and ``info``.

    The maps are photometric: a pixel's value times ``peak`` is its luminance in cd/m^2 (the simulation counts lumens, so
    ``cone / (area * pi * sin^2(cone))`` is cd/m^2). Surfaces listed in ``hidden`` are not drawn: their map is all zero and they do
    not take part in the peak, so the picture is exposed for the surfaces that are shown.

    ``info["surfaces"]``: per lit surface its index, peak and area-weighted mean luminance (cd/m^2). ``info["reference"]``: a
    robust "bright" level of the picture (the 99.5th percentile of the lit pixels, every pixel counting for its area), which a
    small, very bright exit surface does not dominate the way it dominates the peak.
    """
    from ..results import DEFAULT_ANALYSIS, RunData

    data = RunData.load(rid, runid)
    comb = data.combine(state_id or data.state_ids()[0])
    maps_rgb: list[tuple[int, object, np.ndarray]] = []
    surfaces: list[dict] = []
    ref_values: list[np.ndarray] = []
    ref_weights: list[np.ndarray] = []
    for k, det in enumerate(data.scene["detectors"]):
        if k in hidden:
            maps_rgb.append((k, det, np.zeros((1, 1, 3), dtype=np.float32)))  # placeholder: a hidden surface has no emitter
            continue
        maps = data.detector_maps(comb, k, DEFAULT_ANALYSIS)
        cone, area = maps["cone"], maps["area"]
        sin2 = math.sin(math.radians(float(det["cone_deg"]))) ** 2 * max(float(det.get("view_cos", 1.0)), 1e-6)  # (a receiver's cone is about its view direction)
        sm = float(view_smooth)
        if sm > 0:  # the maps are Monte Carlo estimates; smooth flux and area together so the edges stay put
            from scipy import ndimage

            cone = np.stack([ndimage.gaussian_filter(cone[..., i], sm, mode="nearest") for i in range(3)], axis=-1)
            area_s = ndimage.gaussian_filter(area, sm, mode="nearest")
        else:
            area_s = area
        with np.errstate(divide="ignore", invalid="ignore"):
            xyz = np.where(area[..., None] > 0, cone / (area_s[..., None] * 1e-6 * math.pi * sin2), 0.0)
        rgb = np.clip(xyz @ _XYZ_TO_RGB.T, 0.0, None).astype(np.float32)
        maps_rgb.append((k, det, rgb))
        y = np.asarray(xyz[..., 1], dtype=np.float64)
        on = area > 0
        surfaces.append(
            {
                "index": k,
                "peak": float(y[on].max()) if on.any() else 0.0,
                "mean": float((y[on] * area[on]).sum() / area[on].sum()) if on.any() else 0.0,
            }
        )
        ref_values.append(rgb.max(axis=-1))
        ref_weights.append(np.where(on, area, 0.0))
    peak = max((float(r.max()) for _, _, r in maps_rgb), default=0.0)
    if peak <= 0:
        if hidden and len(hidden) >= len(maps_rgb):
            raise ValueError("Every exit surface is switched off in the picture. Show at least one.")
        raise ValueError("The exit surface received no light in this state")
    reference = _weighted_percentile(np.concatenate([v.ravel() for v in ref_values]), np.concatenate([w.ravel() for w in ref_weights]), 99.5) if ref_values else 0.0
    info = {"surfaces": surfaces, "reference": reference if reference > 0 else peak * 0.9}
    maps_out = [r for _, _, r in maps_rgb]
    fix = _spectral_bitmap_correction()
    if fix is not None:  # (the light a bitmap emitter gives in the spectral variants is not the colour in the bitmap: put that right)
        maps_out = [np.clip(r @ fix, 0.0, None) for r in maps_out]
        peak = max(float(r.max()) for r in maps_out)
    # bitmap emitters are reflectance-like (0..1); the scale is put back after rendering
    return [(r / peak).astype(np.float32) for r in maps_out], peak, info


def detector_normal(scene: Scene, det) -> np.ndarray:
    """The side of a detector that is drawn and looked at.

    An exit surface faces away from the middle of the model (the outside of the light pipe). A receiver (a table below the
    model, say) is looked at from the air side of its faces, which is their outward normal whatever the model around it is:
    the middle of the model can lie on either side of it.
    """
    n = det.n / np.linalg.norm(det.n)
    if det.kind == "receiver":
        return n
    mid = 0.5 * (scene.V.min(axis=0) + scene.V.max(axis=0))
    return -n if float(np.dot(n, det.corners.mean(axis=0) - mid)) < 0 else n


def _receiver_bsdf(scene: Scene, det) -> dict:
    """A receiver is a real surface: the glow of its map is drawn on top of its own finish, so it is lit by the room light like the
    rest of the part (an emitter over a black absorber would look like a hole). Falls back to black for a clear part."""
    fins = scene.tri_fin[det.tri_ids]
    k = int(np.bincount(fins).argmax()) if len(fins) else -1
    if k >= 0 and int(scene.fin_type[k]) == 1:
        return _surface_bsdf(1, scene.fin_p[k], None, scene.fin_spec[k])
    return {"type": "diffuse", "reflectance": {"type": "rgb", "value": [0.0, 0.0, 0.0]}}


def _exit_emitters(rid: str, runid: str, scene: Scene, state_id: str, outdir: Path, warnings: list[str], view_smooth: float = 1.0, hidden: tuple[int, ...] = ()) -> dict:
    """Exit surfaces drawn from the simulation: each detector map becomes a Lambertian emitter texture (except the hidden ones)."""
    import mitsuba as mi

    maps, peak, info = exit_maps_info(rid, runid, state_id, view_smooth, hidden)
    out: dict = {}
    black = {"type": "diffuse", "reflectance": {"type": "rgb", "value": [0.0, 0.0, 0.0]}}
    shown = [k for k in range(len(scene.detectors)) if k not in hidden]
    # A receiver's map already holds all the light the simulation sent to it. Another lit surface (a pass-through exit above a table)
    # is an emitter in the picture and would light the finish of the receiver a second time. So when a receiver is drawn together with
    # another surface, the receivers are black in this scene (just their glow), and their finish, lit by the room light only, goes into a
    # second scene (``_room``) whose picture is added to this one.
    split = len(shown) >= 2 and any(scene.detectors[k].kind == "receiver" for k in shown)
    room: dict = {}
    for k, (det, rgb) in enumerate(zip(scene.detectors, maps)):
        if k in hidden:
            continue
        n = detector_normal(scene, det)
        path = outdir / f"exit_{k}.ply"
        if det.mesh:
            _write_soup_uv(path, det.mesh, scene.eps * 4)
        else:
            _write_quad_uv(path, det.corners + n * scene.eps * 4, n)
        finish = _receiver_bsdf(scene, det) if det.kind == "receiver" else black
        out[f"exit_{k}"] = {
            "type": "ply",
            "filename": str(path),
            "flip_tex_coords": False,
            "bsdf": black if (split and det.kind == "receiver") else finish,
            "emitter": {"type": "area", "radiance": {"type": "bitmap", "bitmap": mi.Bitmap(rgb), "filter_type": "bilinear"}},
        }
        if split and det.kind == "receiver":
            room[f"exit_{k}"] = {"type": "ply", "filename": str(path), "flip_tex_coords": False, "bsdf": finish}
    out["_scale"] = peak
    out["_info"] = info
    out["_room"] = room
    return out


def build_mitsuba_scene(rid: str, setup: dict, library: dict, state_id: str | None, view: dict, outdir: Path, runid: str | None = None) -> tuple[dict, dict]:
    """Returns (mitsuba scene dict, info) with ``info`` holding warnings and the camera used.

    ``view["mode"]``: ``"map"`` (default) draws the exit surface from the simulation result of ``runid``, which is smooth
    and fast; ``"transport"`` traces the full light path again in Mitsuba (accurate in principle, but the LEDs sit behind
    dielectric boundaries and are only found by chance, so it is very noisy unless many samples per pixel are used).
    """
    mode = view.get("mode", "map")
    radiance_scale = 1.0
    exit_info: dict = {"surfaces": [], "reference": 0.0}
    room_shapes: dict = {}  # map mode, a receiver drawn with another surface: the receivers' finish, in a second scene lit by the room only
    import mitsuba as mi

    select_variant()
    mdir = store.rev_dir(rid)
    opt = load_optical(mdir / "optical.npz")
    opt_doc = store.read_json(mdir / "optical.json")
    scene = build_scene(opt, opt_doc, setup, library)
    if not scene.detectors:
        raise ValueError("The setup has no exit surface. Pick it first.")
    srcs = sources.build_channel_sources(setup, opt_doc, scene, opt, library)
    states = effective_states(setup)
    state = next((s for s in states if s["id"] == state_id), states[0])
    drive = sources.resolve_state_drive(state, srcs)
    if not any(v > 0 for v in drive.values()):
        raise ValueError(f"State {state.get('name', state['id'])!r} has no LED switched on")

    outdir.mkdir(parents=True, exist_ok=True)
    warnings = list(scene.warnings)
    d: dict = {"type": "scene"}

    # -- geometry ----------------------------------------------------------------------------------
    media: dict[int, dict | None] = {}
    for bi, body in enumerate(scene.bodies):
        mat = int(scene.body_mat[bi])
        if body["transparent"] and mat not in media:
            media[mat] = _medium(scene, mat) if mode == "transport" else None
    for bi, body in enumerate(scene.bodies):
        mat = int(scene.body_mat[bi])
        n550 = float(np.interp(550.0, LAM, scene.mat_n[mat])) if body["transparent"] else None
        in_body = scene.tri_body == bi
        for k in np.unique(scene.tri_fin[in_body]):
            sel = np.nonzero(in_body & (scene.tri_fin == k))[0]
            tris = scene.T[sel]
            used, inv = np.unique(tris, return_inverse=True)
            verts = scene.V[used]
            path = outdir / f"body{bi}_f{int(k)}.ply"
            _write_ply(path, verts, inv.reshape(-1, 3))
            bsdf = _surface_bsdf(int(scene.fin_type[k]), scene.fin_p[k], n550, scene.fin_spec[k])
            if mode == "map" and body["transparent"]:  # the glow comes from the exit texture; the rest of the pipe is a dim body
                bsdf = {"type": "twosided", "bsdf": {"type": "roughplastic", "alpha": 0.08, "int_ior": 1.58, "diffuse_reflectance": {"type": "rgb", "value": [0.05, 0.05, 0.05]}}}
            shape: dict = {"type": "ply", "filename": str(path), "face_normals": True, "bsdf": bsdf}
            if body["transparent"] and media.get(mat) is not None:
                shape["interior"] = media[mat]
            d[f"b{bi}_{int(k)}"] = shape
    if mode == "map":
        if not runid:
            raise ValueError("The simulation run is needed to draw the exit surface")
        ex = _exit_emitters(rid, runid, scene, state["id"], outdir, warnings, float(view.get("smooth", 1.0)), hidden_exits(view))
        radiance_scale = float(ex.pop("_scale"))
        exit_info = ex.pop("_info")
        room_shapes = ex.pop("_room")
        d.update(ex)
        warnings.append("The exit surface is drawn from the simulation result as a Lambertian surface. The sides of the light pipe are shown dark.")
        if any(dd.kind == "receiver" for dd in scene.detectors):
            warnings.append("A receiver is drawn as a Lambertian surface with the apparent luminance it has toward its viewing direction (the picture is the same from any angle).")
    else:
        d.update(_emitters(srcs, drive, scene.eps, outdir, warnings))
        if not any(k.startswith("led_") for k in d):
            raise ValueError("No LED is lit in this state")
    if mode == "transport" and any(s.pat_cdf is not None and not _is_lambertian(s) for s in srcs if s.rays is None):
        warnings.append("Mitsuba area emitters are Lambertian: LED angular patterns that are narrower or wider than cos^1 are approximated by a cosine distribution of the same flux.")

    # -- camera ------------------------------------------------------------------------------------
    cam = _camera(scene, view)
    lux = max(float(view.get("room_lux", DEFAULT_ROOM_LUX if mode == "map" else 0.0)), 0.0)
    card = white_card_luminance(lux)
    if lux > 0:
        # a soft studio environment so that the housing and the reflections in the parts can be seen. It is a real room:
        # a white surface facing the camera has a luminance of lux / pi cd/m^2. The pictures are in units of the peak of the exit
        # surfaces (map; the caller multiplies by ``radianceScale``) or of the Mitsuba emitters (transport, see TRANSPORT_UNITS).
        env = {
            "type": "envmap",
            "bitmap": studio_bitmap(),
            "scale": card / (radiance_scale if mode == "map" else TRANSPORT_UNITS),
            "to_world": mi.ScalarTransform4f(environment_matrix(cam["origin"], cam["target"], cam["up"]).tolist()),
        }
        if not room_shapes:
            d["environment"] = env
    width = int(view.get("width", 640))
    height = max(int(round(width / float(view.get("aspect", 1.6)))), 16)
    look_at = mi.ScalarTransform4f().look_at(origin=cam["origin"], target=cam["target"], up=cam["up"])
    if cam["projection"] == "orthographic":
        # the same framing as the perspective picture at the target: Mitsuba's orthographic camera shows a half width of its scale
        half_width = cam["distance"] * math.tan(math.radians(cam["fov"]) / 2.0)
        sensor: dict = {"type": "orthographic", "to_world": look_at @ mi.ScalarTransform4f().scale([half_width, half_width, 1.0])}
    else:
        sensor = {"type": "perspective", "fov": cam["fov"], "fov_axis": "x", "to_world": look_at}
    d["sensor"] = {
        **sensor,
        "near_clip": max(scene.diag * 1e-3, 0.01),
        "far_clip": scene.diag * 20,
        "sampler": {"type": "multijitter", "sample_count": int(view.get("spp", 64))},
        "film": {"type": "hdrfilm", "width": width, "height": height, "pixel_format": "rgb", "component_format": "float32", "rfilter": {"type": "gaussian"}},
    }
    d["integrator"] = {"type": "volpathmis", "max_depth": int(view.get("max_depth", 64)), "rr_depth": 6}
    room_scene = None
    if room_shapes and lux > 0:  # (in a dark room nothing lights the finish of the receivers: only their glow shows)
        # the second picture (see _exit_emitters): the model and the finish of the receivers under the room light, without the glowing surfaces
        room_scene = {k: v for k, v in d.items() if k in ("type", "sensor", "integrator") or (k.startswith("b") and k[1:2].isdigit())}
        room_scene.update(room_shapes)
        room_scene["environment"] = env
    info = {"warnings": warnings, "camera": {k: cam[k] for k in ("origin", "target", "up", "fov", "distance", "projection")}, "radianceScale": radiance_scale, "state": state["id"], "size": [width, height], "roomLux": lux, "whiteCard": card, "exitInfo": exit_info, "roomScene": room_scene}
    return d, info


_STUDIO: np.ndarray | None = None


def studio_bitmap() -> "object":
    """A soft studio environment (equirectangular, grey): a large key light above and behind the camera on its left, a
    weaker fill on the right, a rim light from behind and a dim floor. Scaled so that a surface facing the camera is lit
    to a radiance of 1 (cosine-weighted mean over its hemisphere). In the frame of :func:`environment_matrix`:
    +y is up and +z points at the camera."""
    global _STUDIO
    if _STUDIO is None:
        import mitsuba as mi  # noqa: F401

        H, W = 64, 128
        v = (np.arange(H) + 0.5) / H
        u = (np.arange(W) + 0.5) / W
        th, ph = np.meshgrid(np.pi * v, 2 * np.pi * u, indexing="ij")
        dirs = np.stack([np.sin(th) * np.sin(ph), np.cos(th), -np.sin(th) * np.cos(ph)], axis=-1)  # Mitsuba's envmap layout

        def blob(c, sigma_deg, k):
            c = np.array(c, float) / np.linalg.norm(c)
            ang = np.arccos(np.clip(dirs @ c, -1, 1))
            return k * np.exp(-0.5 * (ang / math.radians(sigma_deg)) ** 2)

        y = dirs[..., 1]
        L = 0.10 + 0.10 * np.clip(y, 0, 1) + 0.04 * np.clip(-y, 0, 1)  # dim sky, dimmer floor
        L = L + blob([-0.55, 0.65, 0.55], 22, 3.0) + blob([0.9, 0.25, 0.45], 25, 1.0) + blob([0.0, 0.4, -0.9], 18, 1.5)
        dOm = np.sin(th) * (np.pi / H) * (2 * np.pi / W)
        cosw = np.clip(dirs[..., 2], 0, None)  # surfaces facing the camera
        E = float((L * cosw * dOm).sum() / math.pi)
        _STUDIO = np.repeat((L / E)[..., None], 3, axis=-1).astype(np.float32)
    import mitsuba as mi

    return mi.Bitmap(_STUDIO)


def environment_matrix(origin, target, up) -> np.ndarray:
    """Local frame of the studio environment for a camera: +y is the camera's up, +z points back at the camera."""
    o, t, u = (np.asarray(x, float) for x in (origin, target, up))
    f = t - o
    u = u / max(np.linalg.norm(u), 1e-12)
    f = f - u * float(np.dot(f, u))  # the view direction without its up component
    if np.linalg.norm(f) < 1e-9:  # looking straight up/down: any horizontal direction will do
        f = np.cross(u, [1.0, 0.0, 0.0]) if abs(u[0]) < 0.9 else np.cross(u, [0.0, 1.0, 0.0])
    z = -f / np.linalg.norm(f)
    x = np.cross(u, z)
    x /= max(np.linalg.norm(x), 1e-12)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2] = x, u, z
    return m


def _is_lambertian(src: sources.ChannelSource) -> bool:
    ref = sources.pattern_cdf(sources.pattern_intensity({"type": "lambertian", "exponent": 1.0}))
    return bool(len(ref) == len(src.pat_cdf) and np.max(np.abs(ref - src.pat_cdf)) < 0.02)


def _camera(scene: Scene, view: dict) -> dict:
    det = scene.detectors[int(np.clip(int(view.get("detector", 0)), 0, len(scene.detectors) - 1))]
    center = det.corners.mean(axis=0)
    u_len = float(np.linalg.norm(det.corners[1] - det.corners[0]))
    v_len = float(np.linalg.norm(det.corners[3] - det.corners[0]))
    right, up_axis = (det.u, det.v) if u_len >= v_len else (det.v, det.u)
    n = detector_normal(scene, det)  # (look at the surface from outside)
    az = math.radians(float(view.get("azimuth", 0.0)))
    el = math.radians(float(view.get("elevation", 0.0)))
    direction = math.cos(el) * (math.cos(az) * n + math.sin(az) * right) + math.sin(el) * up_axis
    direction /= np.linalg.norm(direction)
    fov = float(view.get("fov", 35.0))
    width = max(u_len, v_len, 1e-6)
    dist = 0.55 * width * 1.15 / math.tan(math.radians(fov) / 2.0) / float(view.get("zoom", 1.0))
    origin = center + direction * dist
    up = up_axis - direction * float(np.dot(up_axis, direction))
    if np.linalg.norm(up) < 1e-6:
        up = right
    up = up / np.linalg.norm(up)
    projection = "orthographic" if view.get("projection") == "orthographic" else "perspective"
    return {"origin": origin.tolist(), "target": center.tolist(), "up": up.tolist(), "fov": fov, "distance": dist, "projection": projection}


def render_to_png(rid: str, runid: str, state_id: str | None, view: dict, outdir: Path, progress) -> dict:
    """Render ``runid``'s snapshot setup and write ``image.png`` (+ ``image.exr``-free float data) into ``outdir``."""
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    import mitsuba as mi

    variant = select_variant()
    rdir = store.run_dir(rid, runid)
    setup = store.read_json(rdir / "setup.json")
    library = store.read_json(rdir / "library.json")
    if not setup or not library:
        raise ValueError("The run has no setup snapshot")
    progress("Building the Mitsuba scene", 0.02)
    desc, info = build_mitsuba_scene(rid, setup, library, state_id, view, outdir / "meshes", runid)
    room_desc = info.pop("roomScene", None)  # (a receiver drawn with another surface: its finish under the room light is a second picture)
    progress("Rendering", 0.1)
    scene = mi.load_dict(desc)
    room = mi.load_dict(room_desc) if room_desc else None
    spp = max(int(view.get("spp", 64)), 1)
    gpu = variant.startswith("cuda")
    # the GPU renders all samples of a pass at once: keep a pass to a few million so memory stays small
    w, h = info["size"]
    per_pass = max(1, min(spp, (1 << 22) // max(w * h, 1))) if gpu else spp
    passes = -(-spp // per_pass)
    acc = None
    for i in range(passes):
        part = np.array(mi.render(scene, spp=per_pass, seed=i)).astype(np.float64)
        if room is not None:
            part = part + np.array(mi.render(room, spp=per_pass, seed=i + 7919)).astype(np.float64)
        acc = part if acc is None else acc + part
        progress("Rendering", 0.1 + 0.85 * (i + 1) / passes)
    img = acc / passes
    info["variant"] = variant
    progress("Saving the image", 0.97)
    rgb = img[..., :3].astype(np.float64) * float(info.get("radianceScale", 1.0))
    np.save(outdir / "image.npy", rgb.astype(np.float32))
    # exposure like a camera with fixed settings (the same whatever the room light), or, when asked, set from the lit surfaces
    unit = 1.0 if view.get("mode", "map") == "map" else TRANSPORT_UNITS
    mode = str(view.get("exposure_mode", "fixed"))
    if mode == "surfaces":
        ref = float(info.get("exitInfo", {}).get("reference") or 0.0) / unit or None  # (a transport picture has none: its own level)
    else:
        card = float(info.get("whiteCard") or 0.0)  # "room": a white card in the room light is mid-grey (a dark room keeps the fixed exposure)
        ref = (card if (mode == "room" and card > 0) else FIXED_EXPOSURE_LUMINANCE) / unit
    png = tonemap(rgb, float(view.get("exposure", 0.0)), 99.5 if view.get("mode", "map") == "map" else 90.0, bool(view.get("white_balance", True)), reference=ref)
    from PIL import Image

    Image.fromarray(png).save(outdir / "image.png")
    info["peak"] = float(np.percentile(rgb.max(axis=2), 99.5)) if rgb.size else 0.0
    return info


def tonemap(rgb: np.ndarray, exposure_stops: float = 0.0, percentile: float = 90.0, white_balance: bool = False, reference: float | None = None) -> np.ndarray:
    """Auto exposure (90th percentile of the lit pixels -> 0.6), then a soft shoulder and the sRGB curve.
    ``reference`` fixes the exposure (the radiance that becomes 0.6) so that it does not change with the view."""
    if white_balance and rgb.size:  # divide by the energy-weighted mean colour so a warm or cool white looks neutral
        w = rgb.max(axis=2, keepdims=True)
        mean = (rgb * w).sum(axis=(0, 1)) / max(float(w.sum()), 1e-30)
        if np.all(mean > 0):
            rgb = rgb / (mean / mean.max())
    lum = rgb.max(axis=2)
    lit = lum[lum > 1e-3 * float(lum.max())] if lum.size and lum.max() > 0 else lum
    ref = float(reference) if reference else (float(np.percentile(lit, percentile)) if lit.size else 1.0)
    if not np.isfinite(ref) or ref <= 0:
        ref = 1.0
    x = np.clip(rgb / ref * 0.6 * (2.0**exposure_stops), 0.0, None)
    peak = x.max(axis=2, keepdims=True)
    # soft shoulder on the brightest channel, applied to all three so that hue is kept. It starts at the white
    # card (0.6) and rolls off slowly (never reaching 1), so a light that is several times brighter than the
    # card (an LED exit) keeps some gradation instead of clipping to flat white.
    knee, head = 0.6, 0.4
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.maximum(peak - knee, 0.0) / head
        shoulder = np.where(peak <= knee, peak, knee + head * t / (1.0 + t))
        x = np.where(peak > 0, x * (shoulder / np.maximum(peak, 1e-30)), 0.0)
    x = np.clip(x, 0.0, 1.0)
    srgb = np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    return (np.clip(srgb, 0, 1) * 255 + 0.5).astype(np.uint8)