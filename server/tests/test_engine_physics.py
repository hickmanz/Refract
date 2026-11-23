"""Physics validation of the ray tracer against analytic results and an independent reference Monte Carlo.

Synthetic geometry is built directly as an optical mesh (no CAD needed), so every number below has a closed form
(or an independent implementation) to compare with.
"""

import numpy as np
import pytest

from refract import library as lib
from refract.engine import kernels, sources, spectral, tracer
from refract.engine.scene import build_scene


# -- synthetic optical meshes --------------------------------------------------------------------------
class Builder:
    def __init__(self):
        self.verts, self.tris, self.tri_face, self.tri_inst, self.instances = [], [], [], [], []

    def add_solid(self, name, faces):
        """``faces``: list of convex polygons (each a list of 3D points). Triangles are oriented outward."""
        pts = np.concatenate([np.asarray(f, float) for f in faces])
        centre = pts.mean(axis=0)
        ordinal = len(self.instances)
        tri_off = len(self.tris)
        count = 0
        for fi, poly in enumerate(faces):
            poly = np.asarray(poly, float)
            for k in range(1, len(poly) - 1):
                a, b, c = poly[0], poly[k], poly[k + 1]
                n = np.cross(b - a, c - a)
                if np.dot(n, (a + b + c) / 3.0 - centre) < 0:
                    b, c = c, b
                base = len(self.verts)
                self.verts += [a, b, c]
                self.tris.append([base, base + 1, base + 2])
                self.tri_face.append(fi)
                self.tri_inst.append(ordinal)
                count += 1
        self.instances.append(
            {
                "ordinal": ordinal, "id": f"i{ordinal}", "path": f"Root/{name}", "name": name, "partName": name,
                "triOffset": tri_off, "triCount": count, "faceCount": len(faces), "closed": True,
                "volume": 1.0, "area": 1.0, "bbox": [pts.min(axis=0).tolist(), pts.max(axis=0).tolist()],
            }
        )

    def add_box(self, name, lo, hi):
        (x0, y0, z0), (x1, y1, z1) = lo, hi
        faces = [
            [(x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1)],  # 0: x-
            [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)],  # 1: x+
            [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)],  # 2: y-
            [(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)],  # 3: y+
            [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)],  # 4: z-
            [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],  # 5: z+
        ]
        self.add_solid(name, faces)

    def finish(self):
        verts = np.array(self.verts, dtype=np.float32)
        tris = np.array(self.tris, dtype=np.int32)
        tri_face = np.array(self.tri_face, dtype=np.int32)
        tri_inst = np.array(self.tri_inst, dtype=np.int32)
        normals = np.zeros_like(verts)
        for t in tris:
            a, b, c = verts[t].astype(float)
            n = np.cross(b - a, c - a)
            n /= np.linalg.norm(n)
            normals[t] = n
        opt = {"verts": verts, "normals": normals, "tris": tris, "tri_face": tri_face, "tri_inst": tri_inst}
        diag = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0)))
        return opt, {"instances": self.instances, "diagonal": diag, "triangles": len(tris), "warnings": []}


def library_with(**materials):
    library = lib.get_library()
    for k, v in materials.items():
        library["materials"][k] = {"id": k, "name": k, **v}
    library["leds"]["beam"] = {
        "id": "beam",
        "name": "beam",
        "channels": [
            {
                "id": "c",
                "flux_lm": 100.0,
                "spectrum": {"type": "gaussian", "peak": 550, "fwhm": 20},
                "pattern": {"type": "half_angle", "half_angle_deg": 1.0},
            }
        ],
        "emitter": {"shape": "rect", "size": [0.01, 0.01]},
    }
    library["leds"]["lamb"] = {
        "id": "lamb",
        "name": "lamb",
        "channels": [
            {"id": "c", "flux_lm": 100.0, "spectrum": {"type": "gaussian", "peak": 550, "fwhm": 20}, "pattern": {"type": "lambertian", "exponent": 1.0}}
        ],
        "emitter": {"shape": "rect", "size": [0.01, 0.01]},
    }
    return library


def run(builder, setup, library, n=200_000, seed=3):
    opt, doc = builder.finish()
    scene = build_scene(opt, doc, setup, library)
    srcs = sources.build_channel_sources(setup, doc, scene, opt, library)
    tally = tracer.trace_channel(scene, srcs[0], n, seed=seed)
    assert abs(tally.total_accounted() - n) / n < 1e-6, "energy must be conserved exactly"
    return scene, srcs[0], tally


def led(origin, model="beam", axis=(0, 0, 1)):
    return {"id": "s", "name": "s", "model": model, "placement": {"type": "frame", "origin": list(origin), "axis": list(axis), "size": [0.01, 0.01]}}


def detector(part, face, name="d", res=40, cone=90):
    return {"id": name, "name": name, "faces": [{"part": part, "face": face}], "cone_deg": cone, "resolution": res}


# -- tests ----------------------------------------------------------------------------------------------
def test_fresnel_slab_transmission():
    """Normal incidence on a clear n=1.5 plate: total transmission with inner reflections = (1-R)/(1+R)."""
    b = Builder()
    b.add_box("plate", (-60, -60, 10), (60, 60, 13))
    setup = {
        "parts": [{"match": "*", "finish": "black"}, {"match": "plate", "material": "glass", "finish": "polished"}],
        "leds": [led((0, 0, 0))],
        "detectors": [detector("plate", 5)],
    }
    _, _, t = run(b, setup, library_with(glass={"n": 1.5, "absorption": 0.0}))
    r = ((1.5 - 1.0) / (1.5 + 1.0)) ** 2
    assert t.det_pow[0] / t.n_rays == pytest.approx((1 - r) / (1 + r), abs=0.003)


def test_beer_lambert_absorption():
    b = Builder()
    b.add_box("plate", (-60, -60, 10), (60, 60, 15))
    setup = {
        "parts": [{"match": "*", "finish": "black"}, {"match": "plate", "material": "dye", "finish": "polished"}],
        "leds": [led((0, 0, 0))],
        "detectors": [detector("plate", 5)],
    }
    _, _, t = run(b, setup, library_with(dye={"n": 1.0, "absorption": 0.1}))
    assert t.det_pow[0] / t.n_rays == pytest.approx(np.exp(-0.5), abs=0.003)
    assert t.abs_vol[0] / t.n_rays == pytest.approx(1 - np.exp(-0.5), abs=0.003)


def test_lambertian_point_source_irradiance():
    """Fraction of a Lambertian point source's flux inside radius r on a plane at distance H is r^2/(r^2+H^2)."""
    b = Builder()
    b.add_box("plate", (-60, -60, 20), (60, 60, 21))  # n = 1: the plate is optically invisible
    setup = {
        "parts": [{"match": "*", "finish": "black"}, {"match": "plate", "material": "air", "finish": "polished"}],
        "leds": [led((0, 0, 0), "lamb")],
        "detectors": [detector("plate", 5, res=400, cone=90)],
    }
    scene, src, t = run(b, setup, library_with(air={"n": 1.0, "absorption": 0.0}), n=400_000)
    det = scene.detectors[0]
    nu, nv = det.nu, det.nv
    yv = t.ex[det.offset : det.offset + nu * nv, 1].reshape(nv, nu)
    total_y = t.n_rays * float(src.spec @ spectral.CMF[:, 1])
    uc = det.ext[0] + (np.arange(nu) + 0.5) / nu * (det.ext[1] - det.ext[0])
    vc = det.ext[2] + (np.arange(nv) + 0.5) / nv * (det.ext[3] - det.ext[2])
    rr = np.hypot(*np.meshgrid(uc, vc))
    h = 21.0
    for r in (6.0, 12.0, 25.0):
        expect = r**2 / (r**2 + h**2)
        got = yv[rr < r].sum() / total_y
        assert got == pytest.approx(expect, rel=0.03), f"r={r}"


def _prism(n_index, mat_name):
    b = Builder()
    L, W = 20.0, 20.0
    A, B, C = (0.0, 0.0), (L, 0.0), (0.0, L)
    p = lambda xz, y: (xz[0], y, xz[1])  # noqa: E731
    faces = [
        [p(A, -W / 2), p(B, -W / 2), p(B, W / 2), p(A, W / 2)],  # 0 bottom leg (z=0)
        [p(A, -W / 2), p(C, -W / 2), p(C, W / 2), p(A, W / 2)],  # 1 side leg (x=0)
        [p(B, -W / 2), p(C, -W / 2), p(C, W / 2), p(B, W / 2)],  # 2 hypotenuse
        [p(A, -W / 2), p(B, -W / 2), p(C, -W / 2)],  # 3 cap
        [p(A, W / 2), p(B, W / 2), p(C, W / 2)],  # 4 cap
    ]
    b.add_solid("prism", faces)
    setup = {
        "parts": [{"match": "*", "finish": "black"}, {"match": "prism", "material": mat_name, "finish": "polished"}],
        "leds": [led((5.0, 0.0, -10.0))],  # beam enters the bottom leg and hits the hypotenuse at 45 degrees
        "detectors": [detector("prism", 1, "leg", res=10), detector("prism", 2, "hyp", res=10)],
    }
    return b, setup


def test_total_internal_reflection_prism():
    b, setup = _prism(1.5, "glass")
    _, _, t = run(b, setup, library_with(glass={"n": 1.5, "absorption": 0.0}), n=100_000)
    r = 0.04
    assert t.det_pow[1] == pytest.approx(0.0, abs=1e-9), "45 degrees is beyond the critical angle of n=1.5 (41.8 deg)"
    assert t.det_pow[0] / t.n_rays == pytest.approx((1 - r) / (1 + r), abs=0.003)


def test_no_total_internal_reflection_below_critical_index():
    b, setup = _prism(1.3, "lown")
    _, _, t = run(b, setup, library_with(lown={"n": 1.3, "absorption": 0.0}), n=100_000)
    assert t.det_pow[1] / t.n_rays > 0.85, "n=1.3 has a 50.3 deg critical angle: light leaves through the hypotenuse"


def _reference_slab(mus, g, mua, thickness, n=400_000, seed=5):
    """Independent analog Monte Carlo of an index-matched slab hit by a normal beam: -> (transmitted, absorbed)."""
    rng = np.random.default_rng(seed)
    z = np.zeros(n)
    dz = np.ones(n)
    mut = mus + mua
    trans = absorbed = 0
    alive = np.ones(n, dtype=bool)
    idx = np.arange(n)
    while idx.size:
        step = -np.log(rng.random(idx.size)) / mut
        z[idx] += dz[idx] * step
        out_t = z[idx] >= thickness
        out_r = z[idx] <= 0.0
        trans += int(out_t.sum())
        keep = ~(out_t | out_r)
        idx = idx[keep]
        absorb = rng.random(idx.size) < mua / mut
        absorbed += int(absorb.sum())
        idx = idx[~absorb]
        u = rng.random(idx.size)
        cos = (1 + g * g - ((1 - g * g) / (1 - g + 2 * g * u)) ** 2) / (2 * g) if abs(g) > 1e-6 else 1 - 2 * u
        phi = 2 * np.pi * rng.random(idx.size)
        sin = np.sqrt(np.clip(1 - cos**2, 0, None))
        # new z-direction after scattering by (theta, phi) from direction dz (only dz is tracked: slab is laterally infinite)
        old = dz[idx]
        so = np.sqrt(np.clip(1 - old**2, 0, None))
        dz[idx] = np.clip(old * cos - so * sin * np.cos(phi), -1, 1)
    return trans / n, absorbed / n


@pytest.mark.parametrize("mus,g,mua,thickness", [(1.0, 0.0, 0.05, 3.0), (2.0, 0.6, 0.1, 2.0), (0.5, 0.85, 0.02, 4.0)])
def test_volume_scattering_matches_reference_monte_carlo(mus, g, mua, thickness):
    b = Builder()
    b.add_box("plate", (-300, -300, 10), (300, 300, 10 + thickness))
    setup = {
        "parts": [{"match": "*", "finish": "black"}, {"match": "plate", "material": "turbid", "finish": "polished"}],
        "leds": [led((0, 0, 0))],
        "detectors": [detector("plate", 5, res=8)],
    }
    mat = {"n": 1.0, "absorption": mua, "scattering": {"type": "constant", "mus": mus, "g": g}}
    _, _, t = run(b, setup, library_with(turbid=mat), n=200_000)
    ref_t, ref_a = _reference_slab(mus, g, mua, thickness)
    assert t.det_pow[0] / t.n_rays == pytest.approx(ref_t, abs=0.012)
    assert t.abs_vol[0] / t.n_rays == pytest.approx(ref_a, abs=0.012)


def test_ballistic_fraction_of_scattering_slab():
    """With mu_s*t = 1 and no absorption, exp(-1) of a collimated beam passes unscattered (angular bin 0)."""
    b = Builder()
    b.add_box("plate", (-300, -300, 10), (300, 300, 12))
    setup = {
        "parts": [{"match": "*", "finish": "black"}, {"match": "plate", "material": "turbid", "finish": "polished"}],
        "leds": [led((0, 0, 0))],
        "detectors": [detector("plate", 5, res=8, cone=90)],
    }
    mat = {"n": 1.0, "absorption": 0.0, "scattering": {"type": "constant", "mus": 0.5, "g": 0.0}}
    _, src, t = run(b, setup, library_with(turbid=mat), n=200_000)
    nth = t.dirh.shape[1]
    y_per_ray = float(src.spec @ spectral.CMF[:, 1])
    first_bin = t.dirh[0, 0, 1] / (t.n_rays * y_per_ray)
    # the first angular bin (0 .. 90/nth deg) holds the ballistic light plus a little scattered light
    assert np.exp(-1.0) - 0.005 < first_bin < np.exp(-1.0) + 0.06
    assert nth >= 2


def test_many_volume_scatters_do_not_hit_the_surface_bounce_limit():
    """A thick turbid slab needs far more than max_bounces scattering events per ray: they have their own cap."""
    b = Builder()
    b.add_box("plate", (-300, -300, 10), (300, 300, 15))
    setup = {
        "parts": [{"match": "*", "finish": "black"}, {"match": "plate", "material": "turbid", "finish": "polished"}],
        "leds": [led((0, 0, 0))],
        "detectors": [detector("plate", 5, res=8)],
    }
    mat = {"n": 1.0, "absorption": 0.0, "scattering": {"type": "constant", "mus": 8.0, "g": 0.0}}  # optical depth 40
    _, _, t = run(b, setup, library_with(turbid=mat), n=4_000)
    assert t.special[kernels.MAXB] / t.n_rays < 0.001
    assert 0.01 < t.det_pow[0] / t.n_rays < 0.15  # diffusion: about 1 / (1 + 0.75 * 40) of the light gets through, the rest leaves backwards
