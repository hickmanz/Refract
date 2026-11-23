"""GPU solver: the Monte Carlo tracer of ``tracer.py`` / ``kernels.py`` as one NVIDIA Warp (CUDA) kernel.

Same physics, same ``Tally``. One GPU thread follows one ray from the emitter to the end of its life (a "megakernel"), asking
Warp's mesh BVH for the next surface each time. Rays are emitted on the CPU by ``sources.emit`` (so ray k is the very ray the CPU
solver would start) and then uploaded; the random stream of each ray continues on the GPU from the state the emitter left.

The light path analysis is done in the same kernel: every ray carries the tags of the analysis (the groups it touched, its route,
its event counts), and the face tables and the exit maps split by group take every ray. The logs of the exit deposits and of how
each ray ended are kept for the rays below ``log_rays``. ``replay_arrays`` traces chosen rays again with the very same kernel, so
a ray that a log describes is drawn on exactly the path it took (a CPU replay of a GPU ray would take another path).

Differences from the CPU solver that matter:

* The ray state and the optics are float32 (the CPU solver keeps float64 and hands float32 to Embree). The tallies are summed in
  float64. Results therefore agree with the CPU solver within noise, not digit for digit.
* The random numbers use the same splitmix64 stream, but the top 24 bits make the float (not 53), so a given ray does not follow
  exactly the same path as on the CPU. Rays of a GPU run must therefore be replayed on the GPU (``analysis.json`` records the solver).

Warp is an optional dependency (``pip install warp-lang``); nothing here is imported unless the GPU solver is selected.
"""

import math

import numpy as np
import warp as wp

from . import kernels as K
from . import sources
from .scene import Scene
from .tracer import MAX_SCATTERS, Analysis, Tally

wp.config.quiet = True

TWO_PI = wp.constant(2.0 * math.pi)
HALF_PI = wp.constant(0.5 * math.pi)
SD = K.SD
NLAM = K.NL
ESC, LEAK, ROUL, MAXB, ANOM, BACK = K.ESC, K.LEAK, K.ROUL, K.MAXB, K.ANOM, K.BACK
M_ESC, M_LEAK, M_ROUL, M_MAXB, M_ANOM, M_BACK = (wp.constant(v) for v in (ESC, LEAK, ROUL, MAXB, ANOM, BACK))
M_SD = wp.constant(SD)
NRING = K.NRING
M_NRING = wp.constant(NRING)
M_NL = wp.constant(NLAM)
# analysis: outcomes of a ray, slots of the from/to tables, events of a recorded path
O_ESC, O_LEAK, O_ABS, O_ROUL, O_MAXB, O_ANOM = (wp.constant(v) for v in (K.OUT_ESC, K.OUT_LEAK, K.OUT_ABS, K.OUT_ROUL, K.OUT_MAXB, K.OUT_ANOM))
S_LOST, S_LEAK = wp.constant(K.SL_LOST), wp.constant(K.SL_LEAK)
E_SCAT, E_SPEC, E_DIFF, E_FRES, E_TIR, E_TRANS, E_ABS, E_EXIT, E_END = (
    wp.constant(v) for v in (K.EV_SCAT, K.EV_SPEC, K.EV_DIFF, K.EV_FRES, K.EV_TIR, K.EV_TRANS, K.EV_ABS, K.EV_EXIT, K.EV_END)
)
NSLOT = K.NSLOT

BATCH = 500_000
LOG_ROWS_PER_RAY = 4  # first guess of the deposit records per logged ray (a batch is traced again with more room when it is not enough)


# -- device data ---------------------------------------------------------------------------------------------
@wp.struct
class SceneData:
    V: wp.array(dtype=wp.vec3)
    N: wp.array(dtype=wp.vec3)
    T: wp.array2d(dtype=wp.int32)
    tri_n: wp.array(dtype=wp.vec3)
    tri_body: wp.array(dtype=wp.int32)
    tri_fin: wp.array(dtype=wp.int32)
    tri_det: wp.array(dtype=wp.int32)
    tri_group: wp.array(dtype=wp.int32)
    tri_gface: wp.array(dtype=wp.int32)
    tri_tface: wp.array(dtype=wp.int32)
    cmap: wp.array(dtype=wp.int32)
    body_mat: wp.array(dtype=wp.int32)
    mat_n: wp.array2d(dtype=wp.float32)
    mat_ka: wp.array2d(dtype=wp.float32)
    mat_mus: wp.array2d(dtype=wp.float32)
    mat_g: wp.array2d(dtype=wp.float32)
    fin_type: wp.array(dtype=wp.int32)
    fin_p: wp.array2d(dtype=wp.float32)
    fin_spec: wp.array3d(dtype=wp.float32)
    det_o: wp.array(dtype=wp.vec3)
    det_u: wp.array(dtype=wp.vec3)
    det_v: wp.array(dtype=wp.vec3)
    det_n: wp.array(dtype=wp.vec3)
    det_ext: wp.array2d(dtype=wp.float32)
    det_nuv: wp.array2d(dtype=wp.int32)
    det_off: wp.array(dtype=wp.int32)
    det_cos: wp.array(dtype=wp.float32)
    det_ring: wp.array2d(dtype=wp.float32)
    det_mode: wp.array(dtype=wp.int32)
    det_kind: wp.array(dtype=wp.int32)  # 0 exit surface, 1 receiver
    det_view: wp.array(dtype=wp.vec3)  # receivers: direction from the surface towards the viewer
    tri_uv: wp.array3d(dtype=wp.float32)
    cmf: wp.array2d(dtype=wp.float32)


@wp.struct
class TallyData:
    abs_vol: wp.array(dtype=wp.float64)
    abs_surf: wp.array(dtype=wp.float64)
    special: wp.array(dtype=wp.float64)
    det_pow: wp.array(dtype=wp.float64)
    rcv_pow: wp.array(dtype=wp.float64)
    ex: wp.array2d(dtype=wp.float64)
    cone: wp.array2d(dtype=wp.float64)
    ex2: wp.array(dtype=wp.float64)
    cone2: wp.array(dtype=wp.float64)
    ring: wp.array2d(dtype=wp.float64)
    ring2: wp.array2d(dtype=wp.float64)
    dirh: wp.array3d(dtype=wp.float64)


@wp.struct
class AnaData:
    """What the light path analysis collects (the arrays are one element long when the analysis is off)."""

    face_t: wp.array2d(dtype=wp.float64)  # (nface, 4)
    face_from: wp.array2d(dtype=wp.float64)  # (ntface, NSLOT)
    face_to: wp.array2d(dtype=wp.float64)
    grp_map: wp.array3d(dtype=wp.float64)  # (2, ngroup + 1, ncoarse)
    touch_map: wp.array3d(dtype=wp.float64)  # (2, max(ngroup, 1), ncoarse)
    tot_map: wp.array2d(dtype=wp.float64)  # (2, ncoarse)
    # log of the exit deposits (one record per deposit; dep_n counts them, only the first dep_cap are stored)
    dep_n: wp.array(dtype=wp.int32)
    dep_rid: wp.array(dtype=wp.int32)
    dep_pix: wp.array(dtype=wp.int32)
    dep_w: wp.array2d(dtype=wp.float32)  # (cap, 3): tw, luminous weight, ... inside the viewing cone
    dep_lastg: wp.array(dtype=wp.int32)
    dep_nb: wp.array(dtype=wp.int32)
    dep_tag: wp.array2d(dtype=wp.int64)  # (cap, 4)
    # how each ray ended, by its index in the batch
    end_out: wp.array(dtype=wp.int32)
    end_nb: wp.array(dtype=wp.int32)
    end_tag: wp.array2d(dtype=wp.int64)  # (n, 4)


@wp.struct
class PathData:
    """Recorded vertices of the first ``nrec`` rays of a launch (replay); one element long when nothing is recorded."""

    pb: wp.array2d(dtype=wp.vec3)
    pn: wp.array(dtype=wp.int32)
    pgf: wp.array2d(dtype=wp.int32)
    pev: wp.array2d(dtype=wp.int32)
    pw: wp.array2d(dtype=wp.float32)


# -- device functions (the same formulas as kernels.py) ----------------------------------------------------------
@wp.func
def rnd(rng: wp.array(dtype=wp.uint64), i: int) -> float:
    z = rng[i] + wp.uint64(11400714819323198485)
    rng[i] = z
    z = (z ^ (z >> wp.uint64(30))) * wp.uint64(13787848793156543929)
    z = (z ^ (z >> wp.uint64(27))) * wp.uint64(10723151780598845931)
    z = z ^ (z >> wp.uint64(31))
    return float(z >> wp.uint64(40)) * 5.9604645e-8  # 24 bits: in [0, 1), never 1.0


@wp.func
def basis(d: wp.vec3):
    a = wp.vec3(1.0, 0.0, 0.0)
    if wp.abs(d[0]) >= 0.9:
        a = wp.vec3(0.0, 1.0, 0.0)
    u = wp.normalize(wp.cross(d, a))
    v = wp.cross(d, u)
    return u, v


@wp.func
def hg_dir(d: wp.vec3, g: float, r1: float, r2: float) -> wp.vec3:
    mu = float(0.0)
    if wp.abs(g) < 1.0e-3:
        mu = 1.0 - 2.0 * r1
    else:
        s = (1.0 - g * g) / (1.0 - g + 2.0 * g * r1)
        mu = (1.0 + g * g - s * s) / (2.0 * g)
    mu = wp.clamp(mu, -1.0, 1.0)
    st = wp.sqrt(wp.max(0.0, 1.0 - mu * mu))
    phi = TWO_PI * r2
    u, v = basis(d)
    return wp.normalize(st * wp.cos(phi) * u + st * wp.sin(phi) * v + mu * d)


@wp.func
def microfacet(n: wp.vec3, sigma: float, r1: float, r2: float) -> wp.vec3:
    """Beckmann slope-distributed microfacet normal around n (sigma = RMS slope; 0 returns n)."""
    m = n
    if sigma > 1.0e-6:
        tan2 = -sigma * sigma * wp.log(wp.max(1.0 - r1, 1.0e-7))
        cz = 1.0 / wp.sqrt(1.0 + tan2)
        sz = wp.sqrt(wp.max(0.0, 1.0 - cz * cz))
        phi = TWO_PI * r2
        u, v = basis(n)
        m = sz * wp.cos(phi) * u + sz * wp.sin(phi) * v + cz * n
    return m


@wp.func
def fresnel_r(cosi: float, n1: float, n2: float):
    """Unpolarised Fresnel reflectance and the refracted cosine (R = 1 on total internal reflection)."""
    eta = n1 / n2
    s2 = eta * eta * (1.0 - cosi * cosi)
    r = float(1.0)
    cost = float(0.0)
    if s2 < 1.0:
        cost = wp.sqrt(1.0 - s2)
        a = n1 * cosi
        b = n2 * cost
        rs = (a - b) / (a + b)
        c = n1 * cost
        e = n2 * cosi
        rp = (c - e) / (c + e)
        r = 0.5 * (rs * rs + rp * rp)
    return r, cost


@wp.func
def lam_index(lam: float):
    x = wp.min(wp.max((lam - 380.0) / 5.0, 0.0), float(M_NL) - 1.001)
    li = int(x)
    return li, x - float(li)


@wp.func
def lerp_spec(a: wp.array2d(dtype=wp.float32), row: int, li: int, lf: float) -> float:
    return a[row, li] * (1.0 - lf) + a[row, li + 1] * lf


@wp.func
def lerp_spec_3(a: wp.array3d(dtype=wp.float32), k: int, j: int, li: int, lf: float) -> float:
    return a[k, j, li] * (1.0 - lf) + a[k, j, li + 1] * lf


@wp.func
def lerp_cmf(a: wp.array2d(dtype=wp.float32), li: int, k: int, lf: float) -> float:
    return a[li, k] * (1.0 - lf) + a[li + 1, k] * lf


@wp.func
def fate(face_to: wp.array2d(dtype=wp.float64), lf: int, col: int, w: float):
    """Weight that leaves the last tagged face lf towards column col (next tagged group, exit, lost, leaked)."""
    if lf >= 0:
        wp.atomic_add(face_to, lf, col, wp.float64(w))


@wp.func
def det_pixel(sc: SceneData, di: int, tri: int, p: wp.vec3, bu1: float, bu2: float) -> int:
    """Map pixel (index into the maps of all detectors) of the hit point p on triangle tri of detector di (bu1, bu2: barycentric weights of corners 1 and 2)."""
    qd = p - sc.det_o[di]
    pu = wp.dot(qd, sc.det_u[di])
    pv = wp.dot(qd, sc.det_v[di])
    if sc.det_mode[di] == 1:  # unwrapped surface: barycentric position in the hit triangle -> its UV
        pu = sc.tri_uv[tri, 0, 0] + bu1 * (sc.tri_uv[tri, 1, 0] - sc.tri_uv[tri, 0, 0]) + bu2 * (sc.tri_uv[tri, 2, 0] - sc.tri_uv[tri, 0, 0])
        pv = sc.tri_uv[tri, 0, 1] + bu1 * (sc.tri_uv[tri, 1, 1] - sc.tri_uv[tri, 0, 1]) + bu2 * (sc.tri_uv[tri, 2, 1] - sc.tri_uv[tri, 0, 1])
    nu = sc.det_nuv[di, 0]
    nv = sc.det_nuv[di, 1]
    fx = (pu - sc.det_ext[di, 0]) / (sc.det_ext[di, 1] - sc.det_ext[di, 0])
    fy = (pv - sc.det_ext[di, 2]) / (sc.det_ext[di, 3] - sc.det_ext[di, 2])
    ix = wp.min(wp.max(int(fx * float(nu)), 0), nu - 1)
    iy = wp.min(wp.max(int(fy * float(nv)), 0), nv - 1)
    return sc.det_off[di] + iy * nu + ix


@wp.func
def rcv_deposit(
    sc: SceneData, an: AnaData, pix: int, tw: float, wy: float, wy_cone: float,
    rid: int, log_end: int, dep_cap: int, ngrp: int, lastg: int, nbn: int,
    t0: wp.int64, t1: wp.int64, t2: wp.int64, t3: wp.int64,
):
    """Analysis record of light that arrived on a receiver: the maps split by the group touched last / by every group touched
    (all directions, and the part that the finish sends into the viewing cone), and one log row."""
    cp = sc.cmap[pix]
    wy_all = wp.float64(wy)
    wy_c = wp.float64(wy_cone)
    wp.atomic_add(an.tot_map, 0, cp, wy_all)
    wp.atomic_add(an.tot_map, 1, cp, wy_c)
    wp.atomic_add(an.grp_map, 0, lastg + 1, cp, wy_all)
    wp.atomic_add(an.grp_map, 1, lastg + 1, cp, wy_c)
    for gi in range(ngrp):
        if ((t0 >> wp.int64(gi)) & wp.int64(1)) != wp.int64(0):
            wp.atomic_add(an.touch_map, 0, gi, cp, wy_all)
            wp.atomic_add(an.touch_map, 1, gi, cp, wy_c)
    if rid < log_end:
        k = wp.atomic_add(an.dep_n, 0, 1)
        if k < dep_cap:
            an.dep_rid[k] = rid
            an.dep_pix[k] = pix
            an.dep_w[k, 0] = tw
            an.dep_w[k, 1] = wy
            an.dep_w[k, 2] = wy_cone
            an.dep_lastg[k] = lastg
            an.dep_nb[k] = nbn
            an.dep_tag[k, 0] = t0
            an.dep_tag[k, 1] = t1
            an.dep_tag[k, 2] = t2
            an.dep_tag[k, 3] = t3


@wp.func
def bump(t3: wp.int64, idx: int) -> wp.int64:
    """Count one event of kind idx in the packed 8-bit counters of a ray's tag word 3 (saturating at 255)."""
    sh = wp.int64(8 * idx)
    out = t3
    if ((t3 >> sh) & wp.int64(255)) < wp.int64(255):
        out = t3 + (wp.int64(1) << sh)
    return out


@wp.func
def pt_add(pa: PathData, r: int, maxpts: int, p: wp.vec3, gf: int, ev: int, w: float) -> int:
    """Append a vertex to the path of recorded ray r; returns its index (-1 when the path buffer is full)."""
    k = pa.pn[r]
    out = int(-1)
    if k < maxpts:
        pa.pb[r, k] = p
        pa.pgf[r, k] = gf
        pa.pev[r, k] = ev
        pa.pw[r, k] = w
        pa.pn[r] = k + 1
        out = k
    return out


@wp.func
def pt_set(pa: PathData, r: int, k: int, gf: int, ev: int, w: float):
    if k >= 0:
        pa.pgf[r, k] = gf
        pa.pev[r, k] = ev
        pa.pw[r, k] = w


def _make_kernel(ANA: bool, REC: bool):
    """The tracing kernel with the analysis and the path recording compiled in or out.

    One kernel with both switched on is about 2.7x slower than the plain one even when they are off at run time (the
    extra state costs registers), so every combination used gets its own kernel and the unused code is not there at all."""

    @wp.kernel(module="unique")
    def trace_kernel(
        mesh: wp.uint64,
        sc: SceneData,
        ty: TallyData,
        an: AnaData,
        pa: PathData,
        o_in: wp.array(dtype=wp.vec3),
        d_in: wp.array(dtype=wp.vec3),
        lam_in: wp.array(dtype=wp.float32),
        rng: wp.array(dtype=wp.uint64),
        stack: wp.array2d(dtype=wp.int32),
        depth0: int,
        eps: float,
        rr_thr: float,
        max_b: int,
        max_sc: int,
        nth: int,
        esc_len: float,
        ana: int,  # 1: tags, face tables and exit maps by group
        rid0: int,  # number of the first ray of the launch in the run
        log_end: int,  # rays with a number below this are logged (only with ana)
        dep_cap: int,
        ngrp: int,
        nrec: int,  # the first nrec rays of the launch record their path
        maxpts: int,
    ):
        i = wp.tid()
        o = o_in[i]
        d = d_in[i]
        lm = lam_in[i]
        w = float(1.0)
        dep = int(depth0)
        nbn = int(0)
        nsc = int(0)
        li, lf = lam_index(lm)
        rid = rid0 + i
        rec = i < nrec
        # analysis state of the ray
        t0 = wp.int64(0)  # bit g: the ray hit tagged group g
        t1 = wp.int64(0)  # the first 8 groups of its route (6 bits each), route length from bit 48
        t2 = wp.int64(0)  # hash of the whole route
        t3 = wp.int64(0)  # event counters, 8 bits each
        lastg = int(-1)
        lastf = int(-1)
        outc = int(0)

        while True:
            q = wp.mesh_query_ray(mesh, o, d, 1.0e30)
            top = int(-1)
            mat = int(0)
            if dep > 0:
                top = stack[i, dep - 1]
                mat = sc.body_mat[top]

            # ---- no hit ----
            if not q.result:
                if dep == 0:
                    wp.atomic_add(ty.special, M_ESC, wp.float64(w))
                    outc = O_ESC
                else:
                    wp.atomic_add(ty.special, M_LEAK, wp.float64(w))
                    outc = O_LEAK
                if ANA:
                    fate(an.face_to, lastf, S_LEAK, w)
                if REC and rec:
                    pt_add(pa, i, maxpts, o + d * esc_len, -1, E_END, w)
                break

            tri = q.face
            n_g = sc.tri_n[tri]
            ia = sc.T[tri, 0]
            ib = sc.T[tri, 1]
            ic = sc.T[tri, 2]
            cosg = wp.dot(d, n_g)
            t = q.t
            if wp.abs(cosg) > 1.0e-12:  # exact plane distance, the mesh query's value as fallback
                tp = wp.dot(sc.V[ia] - o, n_g) / cosg
                if tp > 0.0 and wp.abs(tp - t) < 1.0e-3 * wp.max(1.0, t):
                    t = tp

            ka = lerp_spec(sc.mat_ka, mat, li, lf)
            mus = lerp_spec(sc.mat_mus, mat, li, lf)

            # ---- volume scattering before the surface ----
            if mus > 0.0:
                sdist = -wp.log(wp.max(rnd(rng, i), 1.0e-30)) / mus
                if sdist < t:
                    att = wp.exp(-ka * sdist)
                    if top >= 0:
                        wp.atomic_add(ty.abs_vol, top, wp.float64(w * (1.0 - att)))
                    if ANA:
                        fate(an.face_to, lastf, S_LOST, w * (1.0 - att))
                        t3 = bump(t3, 5)
                    w = w * att
                    o = o + d * sdist
                    g = lerp_spec(sc.mat_g, mat, li, lf)
                    r1 = rnd(rng, i)
                    r2 = rnd(rng, i)
                    d = hg_dir(d, g, r1, r2)
                    nbn += 1  # nbn counts every event (analysis); nsc only the volume-scattering ones
                    nsc += 1
                    if REC and rec:
                        pt_add(pa, i, maxpts, o, -1, E_SCAT, w)
                    if nsc > max_sc:
                        wp.atomic_add(ty.special, M_MAXB, wp.float64(w))
                        outc = O_MAXB
                        if ANA:
                            fate(an.face_to, lastf, S_LOST, w)
                        break
                    dead = bool(False)
                    if w < rr_thr:
                        if rnd(rng, i) < w / rr_thr:
                            wp.atomic_add(ty.special, M_ROUL, wp.float64(w - rr_thr))
                            if ANA:
                                fate(an.face_to, lastf, S_LOST, w - rr_thr)
                            w = rr_thr
                        else:
                            wp.atomic_add(ty.special, M_ROUL, wp.float64(w))
                            if ANA:
                                fate(an.face_to, lastf, S_LOST, w)
                            outc = O_ROUL
                            dead = True
                    if dead:
                        break
                    continue

            # ---- travel to the surface ----
            if ka > 0.0 and top >= 0:
                att = wp.exp(-ka * t)
                wp.atomic_add(ty.abs_vol, top, wp.float64(w * (1.0 - att)))
                if ANA:
                    fate(an.face_to, lastf, S_LOST, w * (1.0 - att))
                w = w * att
            p = o + d * t
            gf = sc.tri_gface[tri]
            pk = int(-1)  # index of this hit in the recorded path
            if REC and rec:
                pk = pt_add(pa, i, maxpts, p, gf, E_TRANS, w)

            nbn += 1
            if nbn - nsc > max_b:  # surface hits only: volume scattering has its own cap
                wp.atomic_add(ty.special, M_MAXB, wp.float64(w))
                outc = O_MAXB
                if ANA:
                    fate(an.face_to, lastf, S_LOST, w)
                break

            if ANA:
                # which tagged face is this, where did the light come from, where did it go since the last tagged face
                wp.atomic_add(an.face_t, gf, 0, wp.float64(w))
                tfc = sc.tri_tface[tri]
                if tfc >= 0:
                    grp = sc.tri_group[tri]
                    wp.atomic_add(an.face_from, tfc, lastg + 1, wp.float64(w))
                    fate(an.face_to, lastf, grp + 1, w)
                    t0 = t0 | (wp.int64(1) << wp.int64(grp))
                    if lastg != grp:
                        rl = t1 >> wp.int64(48)
                        route = t1 & wp.int64(281474976710655)  # the low 48 bits
                        if rl < wp.int64(8):
                            route = route | (wp.int64(grp + 1) << (wp.int64(6) * rl))
                        rl1 = rl + wp.int64(1)
                        if rl1 > wp.int64(127):
                            rl1 = wp.int64(127)
                        t1 = route | (rl1 << wp.int64(48))
                        t2 = wp.int64((wp.uint64(t2) * wp.uint64(1000003) + wp.uint64(grp + 1)) & wp.uint64(9223372036854775807))
                    lastg = grp
                    lastf = tfc

            body = sc.tri_body[tri]
            fin = sc.tri_fin[tri]
            ftype = sc.fin_type[fin]
            entering = cosg < 0.0

            # geometric normal on the incident side
            gn = n_g
            if not entering:
                gn = -n_g

            # interpolated (smooth) normal on the incident side, from the barycentric coordinates of the hit
            bu = 1.0 - q.u - q.v
            sn = gn
            mn = bu * sc.N[ia] + q.u * sc.N[ib] + q.v * sc.N[ic]
            ml = wp.length(mn)
            if ml > 1.0e-9:
                mn = mn / ml
                if not entering:
                    mn = -mn
                if wp.dot(mn, gn) > 0.2:
                    sn = mn

            # =================== receiver detector: light arriving from outside ===================
            # Counted before the surface acts on the ray, and the ray carries on: the incident light (all directions) of the
            # pixel. The part that an opaque finish reflects towards the viewer is counted where its direction is known.
            rdi = sc.tri_det[tri]
            rcv = bool(False)
            if rdi >= 0:
                if sc.det_kind[rdi] == 1:
                    if entering:
                        rcv = True
            rpix = int(-1)
            rcx = float(0.0)
            rcy = float(0.0)
            rcz = float(0.0)
            rw = float(0.0)
            if rcv:
                rcx = lerp_cmf(sc.cmf, li, 0, lf)
                rcy = lerp_cmf(sc.cmf, li, 1, lf)
                rcz = lerp_cmf(sc.cmf, li, 2, lf)
                rpix = det_pixel(sc, rdi, tri, p, q.u, q.v)
                rw = w
                rcly = wp.float64(rw * rcy)
                wp.atomic_add(ty.ex, rpix, 0, wp.float64(rw * rcx))
                wp.atomic_add(ty.ex, rpix, 1, rcly)
                wp.atomic_add(ty.ex, rpix, 2, wp.float64(rw * rcz))
                wp.atomic_add(ty.ex2, rpix, rcly * rcly)
                wp.atomic_add(ty.rcv_pow, rdi, wp.float64(rw))
                if ANA:
                    t3 = bump(t3, 6)
                    if ftype != 1:  # (an opaque finish records its deposit once it knows where the light goes)
                        rcv_deposit(sc, an, rpix, rw, rw * rcy, 0.0, rid, log_end, dep_cap, ngrp, lastg, nbn, t0, t1, t2, t3)

            # =================== opaque surface ===================
            if ftype == 1:
                rd = sc.fin_p[fin, 0] * lerp_spec_3(sc.fin_spec, fin, 0, li, lf)
                rs = sc.fin_p[fin, 1] * lerp_spec_3(sc.fin_spec, fin, 1, li, lf)
                sig = sc.fin_p[fin, 2]
                rt = rd + rs
                if rt > 1.0:  # a colour peak cannot reflect more than all of the light
                    rd = rd / rt
                    rs = rs / rt
                    rt = 1.0
                wp.atomic_add(ty.abs_surf, body, wp.float64(w * (1.0 - rt)))
                if ANA:
                    wp.atomic_add(an.face_t, gf, 1, wp.float64(w * (1.0 - rt)))
                    fate(an.face_to, lastf, S_LOST, w * (1.0 - rt))
                if rt <= 1.0e-9:
                    outc = O_ABS
                    if REC and rec:
                        if rcv:  # (a receiver hit is the path's "exit" event)
                            pt_set(pa, i, pk, gf, E_EXIT, 0.0)
                        else:
                            pt_set(pa, i, pk, gf, E_ABS, 0.0)
                    if ANA:
                        if rcv:  # a receiver with nothing reflected: its light is only incident
                            rcv_deposit(sc, an, rpix, rw, rw * rcy, 0.0, rid, log_end, dep_cap, ngrp, lastg, nbn, t0, t1, t2, t3)
                    break
                w = w * rt
                if ANA:
                    wp.atomic_add(an.face_t, gf, 2, wp.float64(w))
                specular = rnd(rng, i) * rt < rs
                if ANA:
                    if specular:
                        t3 = bump(t3, 3)
                    else:
                        t3 = bump(t3, 4)
                if REC and rec:
                    if rcv:
                        pt_set(pa, i, pk, gf, E_EXIT, w)
                    elif specular:
                        pt_set(pa, i, pk, gf, E_SPEC, w)
                    else:
                        pt_set(pa, i, pk, gf, E_DIFF, w)
                rdir = wp.vec3(0.0, 0.0, 0.0)
                if specular:
                    # specular lobe (optionally rough)
                    m = microfacet(sn, sig, rnd(rng, i), rnd(rng, i))
                    ci = -wp.dot(d, m)
                    if ci <= 1.0e-4:
                        m = gn
                        ci = -wp.dot(d, m)
                    rdir = d + 2.0 * ci * m
                    if wp.dot(rdir, gn) <= 0.0:
                        cg = -wp.dot(d, gn)
                        rdir = d + 2.0 * cg * gn
                else:
                    # Lambertian lobe
                    r1 = rnd(rng, i)
                    r2 = rnd(rng, i)
                    st = wp.sqrt(r1)
                    ct = wp.sqrt(wp.max(0.0, 1.0 - r1))
                    phi = TWO_PI * r2
                    u, v = basis(gn)
                    rdir = st * wp.cos(phi) * u + st * wp.sin(phi) * v + ct * gn
                d = wp.normalize(rdir)
                if rcv:
                    # receiver: the light the finish sends on, tallied by its direction relative to the viewer (cone and rings around
                    # the view direction); the angular histogram is by angle from the surface normal
                    rctv = wp.dot(d, sc.det_view[rdi])
                    rcl = wp.float64(w * rcy)
                    rcone = float(0.0)
                    if rctv >= sc.det_cos[rdi]:
                        wp.atomic_add(ty.cone, rpix, 0, wp.float64(w * rcx))
                        wp.atomic_add(ty.cone, rpix, 1, rcl)
                        wp.atomic_add(ty.cone, rpix, 2, wp.float64(w * rcz))
                        wp.atomic_add(ty.cone2, rpix, rcl * rcl)
                        rcone = w * rcy
                    for rg in range(M_NRING):
                        if rctv <= sc.det_ring[rdi, rg] and rctv > sc.det_ring[rdi, rg + 1]:
                            wp.atomic_add(ty.ring, rpix, rg, rcl)
                            wp.atomic_add(ty.ring2, rpix, rg, rcl * rcl)
                    rctn = wp.dot(d, gn)
                    if rctn > 0.0:
                        rthr = wp.min(int(wp.acos(wp.min(rctn, 1.0)) / HALF_PI * float(nth)), nth - 1)
                        wp.atomic_add(ty.dirh, rdi, rthr, 0, wp.float64(w * rcx))
                        wp.atomic_add(ty.dirh, rdi, rthr, 1, rcl)
                        wp.atomic_add(ty.dirh, rdi, rthr, 2, wp.float64(w * rcz))
                    if ANA:
                        rcv_deposit(sc, an, rpix, rw, rw * rcy, rcone, rid, log_end, dep_cap, ngrp, lastg, nbn, t0, t1, t2, t3)
                o = p + gn * eps

            # =================== dielectric interface ===================
            else:
                sig = sc.fin_p[fin, 0]
                # media on both sides
                n1 = lerp_spec(sc.mat_n, mat, li, lf)
                pos = int(-1)
                for kk in range(dep):
                    if stack[i, kk] == body:
                        pos = kk
                m2 = int(0)
                bad = bool(False)
                if entering:
                    if pos >= 0 or dep >= M_SD:
                        bad = True
                    m2 = sc.body_mat[body]
                else:
                    if pos < 0:
                        bad = True
                    # medium after leaving: the next body down the stack (skipping this one)
                    for kk in range(dep):
                        if kk != pos:
                            m2 = sc.body_mat[stack[i, kk]]
                if bad:
                    wp.atomic_add(ty.special, M_ANOM, wp.float64(w))
                    outc = O_ANOM
                    if ANA:
                        fate(an.face_to, lastf, S_LOST, w)
                    break
                n2 = lerp_spec(sc.mat_n, m2, li, lf)

                m = microfacet(sn, sig, rnd(rng, i), rnd(rng, i))
                ci = -wp.dot(d, m)
                if ci <= 1.0e-4:
                    m = sn
                    ci = -wp.dot(d, m)
                if ci < 0.0:
                    m = gn
                    ci = -wp.dot(d, m)
                ci = wp.clamp(ci, 0.0, 1.0)
                R, cost = fresnel_r(ci, n1, n2)
                rdir = d + 2.0 * ci * m
                if wp.dot(rdir, gn) <= 0.0:
                    cg = -wp.dot(d, gn)
                    rdir = d + 2.0 * cg * gn
                eta = n1 / n2
                tdir = wp.normalize(eta * d + (eta * ci - cost) * m)
                if wp.dot(tdir, gn) >= 0.0:  # refracted ray on the wrong side: cannot transmit
                    R = 1.0

                di = sc.tri_det[tri]
                # an exit surface (kind 0) takes the transmitted part out of the ray; a pass-through exit (kind 2) only measures it
                tally = bool(False)
                thru = bool(False)
                if di >= 0:
                    if sc.det_kind[di] == 0 or sc.det_kind[di] == 2:  # (a receiver face only counts the light arriving on it, see above)
                        tally = (not entering) and m2 == 0 and R < 1.0
                        thru = tally and sc.det_kind[di] == 2
                forced = tally and (not thru)
                reflect = bool(False)
                if tally:
                    # forced detection: the transmitted part is tallied (consuming exit: the reflected part carries on, below)
                    tw = w * (1.0 - R)
                    nu = sc.det_nuv[di, 0]
                    nv = sc.det_nuv[di, 1]
                    pix = det_pixel(sc, di, tri, p, q.u, q.v)
                    ct = wp.dot(tdir, sc.det_n[di])
                    if sc.det_mode[di] == 1:  # curved surface: angle to the local outward normal
                        ct = -wp.dot(tdir, gn)
                    cx0 = lerp_cmf(sc.cmf, li, 0, lf)
                    cy0 = lerp_cmf(sc.cmf, li, 1, lf)
                    cz0 = lerp_cmf(sc.cmf, li, 2, lf)
                    wp.atomic_add(ty.ex, pix, 0, wp.float64(tw * cx0))
                    wp.atomic_add(ty.ex, pix, 1, wp.float64(tw * cy0))
                    wp.atomic_add(ty.ex, pix, 2, wp.float64(tw * cz0))
                    ly = wp.float64(tw * cy0)
                    wp.atomic_add(ty.ex2, pix, ly * ly)  # squares of the luminous deposits: the noise of the maps
                    if thru:
                        wp.atomic_add(ty.rcv_pow, di, wp.float64(tw))  # (not an outcome of the ray: the budget does not count it)
                    else:
                        wp.atomic_add(ty.det_pow, di, wp.float64(tw))
                    in_cone = ct > 0.0 and ct >= sc.det_cos[di]
                    if ct > 0.0:
                        if ct >= sc.det_cos[di]:
                            wp.atomic_add(ty.cone, pix, 0, wp.float64(tw * cx0))
                            wp.atomic_add(ty.cone, pix, 1, wp.float64(tw * cy0))
                            wp.atomic_add(ty.cone, pix, 2, wp.float64(tw * cz0))
                            wp.atomic_add(ty.cone2, pix, ly * ly)
                        # the rings around the cone: ring r holds the rays with cos(e_r) >= ct > cos(e_r+1) (det_ring: the falling cos of the edges)
                        for rr in range(M_NRING):
                            if ct <= sc.det_ring[di, rr] and ct > sc.det_ring[di, rr + 1]:
                                wp.atomic_add(ty.ring, pix, rr, ly)
                                wp.atomic_add(ty.ring2, pix, rr, ly * ly)
                        thb = wp.min(int(wp.acos(wp.min(ct, 1.0)) / HALF_PI * float(nth)), nth - 1)
                        wp.atomic_add(ty.dirh, di, thb, 0, wp.float64(tw * cx0))
                        wp.atomic_add(ty.dirh, di, thb, 1, wp.float64(tw * cy0))
                        wp.atomic_add(ty.dirh, di, thb, 2, wp.float64(tw * cz0))
                    else:
                        wp.atomic_add(ty.special, M_BACK, wp.float64(tw))
                    if ANA:
                        if forced:  # (a pass-through exit leaves the face tallies to the ordinary Fresnel step below)
                            fate(an.face_to, lastf, 0, tw)
                            wp.atomic_add(an.face_t, gf, 3, wp.float64(tw))
                        t3 = bump(t3, 6)
                        # the exit maps split by the group the light touched last, and by every group it touched
                        cp = sc.cmap[pix]
                        wy_all = wp.float64(tw * cy0)
                        wy_cone = wp.float64(0.0)
                        if in_cone:
                            wy_cone = wy_all
                        wp.atomic_add(an.tot_map, 0, cp, wy_all)
                        wp.atomic_add(an.tot_map, 1, cp, wy_cone)
                        wp.atomic_add(an.grp_map, 0, lastg + 1, cp, wy_all)
                        wp.atomic_add(an.grp_map, 1, lastg + 1, cp, wy_cone)
                        for gi in range(ngrp):
                            if ((t0 >> wp.int64(gi)) & wp.int64(1)) != wp.int64(0):
                                wp.atomic_add(an.touch_map, 0, gi, cp, wy_all)
                                wp.atomic_add(an.touch_map, 1, gi, cp, wy_cone)
                        if rid < log_end:
                            k = wp.atomic_add(an.dep_n, 0, 1)
                            if k < dep_cap:
                                an.dep_rid[k] = rid
                                an.dep_pix[k] = pix
                                an.dep_w[k, 0] = tw
                                an.dep_w[k, 1] = tw * cy0
                                an.dep_w[k, 2] = float(wy_cone)
                                an.dep_lastg[k] = lastg
                                an.dep_nb[k] = nbn
                                an.dep_tag[k, 0] = t0
                                an.dep_tag[k, 1] = t1
                                an.dep_tag[k, 2] = t2
                                an.dep_tag[k, 3] = t3
                if forced:
                    w = w * R
                    reflect = True
                    if ANA:
                        wp.atomic_add(an.face_t, gf, 2, wp.float64(w))
                    if REC and rec:
                        pt_set(pa, i, pk, gf, E_EXIT, w)
                else:
                    reflect = rnd(rng, i) < R
                    if ANA:
                        if reflect:
                            wp.atomic_add(an.face_t, gf, 2, wp.float64(w))
                            if R >= 1.0:
                                t3 = bump(t3, 0)
                            else:
                                t3 = bump(t3, 1)
                        else:
                            wp.atomic_add(an.face_t, gf, 3, wp.float64(w))
                            t3 = bump(t3, 2)
                    if REC and rec:
                        if rcv or thru:
                            pt_set(pa, i, pk, gf, E_EXIT, w)
                        elif reflect:
                            if R >= 1.0:
                                pt_set(pa, i, pk, gf, E_TIR, w)
                            else:
                                pt_set(pa, i, pk, gf, E_FRES, w)
                        else:
                            pt_set(pa, i, pk, gf, E_TRANS, w)

                if reflect:
                    d = wp.normalize(rdir)
                    o = p + gn * eps
                    if w < 1.0e-30:
                        outc = O_ABS
                        break
                else:
                    d = tdir
                    o = p - gn * eps
                    if entering:
                        stack[i, dep] = body
                        dep = dep + 1
                    else:
                        for kk in range(pos, dep - 1):
                            stack[i, kk] = stack[i, kk + 1]
                        dep = dep - 1

            # ---- roulette ----
            if w < rr_thr:
                if rnd(rng, i) < w / rr_thr:
                    wp.atomic_add(ty.special, M_ROUL, wp.float64(w - rr_thr))
                    if ANA:
                        fate(an.face_to, lastf, S_LOST, w - rr_thr)
                    w = rr_thr
                else:
                    wp.atomic_add(ty.special, M_ROUL, wp.float64(w))
                    if ANA:
                        fate(an.face_to, lastf, S_LOST, w)
                    outc = O_ROUL
                    break

        # how the ray ended
        if ANA:
            if rid < log_end:
                an.end_out[i] = outc
                an.end_nb[i] = nbn
                an.end_tag[i, 0] = t0
                an.end_tag[i, 1] = t1
                an.end_tag[i, 2] = t2
                an.end_tag[i, 3] = t3

    return trace_kernel


_KERNELS: dict = {}


def _kernel(ana: bool, rec: bool):
    key = (bool(ana), bool(rec))
    if key not in _KERNELS:
        _KERNELS[key] = _make_kernel(*key)
    return _KERNELS[key]


# -- host side --------------------------------------------------------------------------------------------------
def available() -> tuple[bool, str]:
    """(usable, description): whether Warp is installed and sees a CUDA device."""
    try:
        wp.init()
        n = wp.get_cuda_device_count()
        if n < 1:
            return False, "No CUDA device found"
        return True, str(wp.get_cuda_device(0).name)
    except Exception as exc:  # a broken driver or CUDA install must not take the app down
        return False, f"{type(exc).__name__}: {exc}"


class _GpuScene:
    """The scene's arrays on the device, built once per ``Scene``."""

    def __init__(self, scene: Scene, device) -> None:
        f32, i32 = np.float32, np.int32
        self.device = device

        def a1(x, dt=f32):
            return wp.array(np.ascontiguousarray(x, dtype=dt), dtype=wp.float32 if dt is f32 else wp.int32, device=device)

        def v3(x):
            return wp.array(np.ascontiguousarray(x, dtype=f32).reshape(-1, 3), dtype=wp.vec3, device=device)

        V = np.ascontiguousarray(scene.V, dtype=f32).reshape(-1, 3)
        T = np.ascontiguousarray(scene.T, dtype=i32).reshape(-1, 3)
        ntri = len(T)
        self.points = wp.array(V, dtype=wp.vec3, device=device)
        self.indices = wp.array(T.reshape(-1), dtype=wp.int32, device=device)
        self.mesh = wp.Mesh(points=self.points, indices=self.indices)
        d = SceneData()
        d.V = self.points
        d.N = v3(scene.N)
        d.T = wp.array(T, dtype=wp.int32, device=device)
        d.tri_n = v3(scene.tri_n)
        d.tri_body = a1(scene.tri_body, i32)
        d.tri_fin = a1(scene.tri_fin, i32)
        d.tri_det = a1(scene.tri_det, i32)
        neg = np.full(ntri, -1, dtype=i32)
        d.tri_group = a1(scene.tri_group if scene.tri_group is not None else neg, i32)
        d.tri_gface = a1(scene.tri_gface if scene.tri_gface is not None else np.zeros(ntri, dtype=i32), i32)
        d.tri_tface = a1(scene.tri_tface if scene.tri_tface is not None else neg, i32)
        d.cmap = a1(scene.coarse["cmap"] if scene.coarse else np.zeros(1, dtype=i32), i32)
        d.body_mat = a1(scene.body_mat, i32)
        d.mat_n = wp.array(np.ascontiguousarray(scene.mat_n, dtype=f32), dtype=wp.float32, device=device)
        d.mat_ka = wp.array(np.ascontiguousarray(scene.mat_ka, dtype=f32), dtype=wp.float32, device=device)
        d.mat_mus = wp.array(np.ascontiguousarray(scene.mat_mus, dtype=f32), dtype=wp.float32, device=device)
        d.mat_g = wp.array(np.ascontiguousarray(scene.mat_g, dtype=f32), dtype=wp.float32, device=device)
        d.fin_type = a1(scene.fin_type, i32)
        d.fin_p = wp.array(np.ascontiguousarray(scene.fin_p, dtype=f32), dtype=wp.float32, device=device)
        d.fin_spec = wp.array(np.ascontiguousarray(scene.fin_spec, dtype=f32), dtype=wp.float32, device=device)
        d.det_o = v3(scene.det_o)
        d.det_u = v3(scene.det_u)
        d.det_v = v3(scene.det_v)
        d.det_n = v3(scene.det_n)
        d.det_ext = wp.array(np.ascontiguousarray(scene.det_ext, dtype=f32).reshape(-1, 4), dtype=wp.float32, device=device)
        d.det_nuv = wp.array(np.ascontiguousarray(scene.det_nuv, dtype=i32).reshape(-1, 2), dtype=wp.int32, device=device)
        d.det_off = a1(scene.det_off, i32)
        d.det_cos = a1(scene.det_cos, f32)
        det_ring = scene.det_ring if scene.det_ring is not None else np.ones((len(scene.det_cos), 7))
        d.det_ring = wp.array(np.ascontiguousarray(det_ring, dtype=f32).reshape(-1, 7), dtype=wp.float32, device=device)
        d.det_mode = a1(scene.det_mode, i32)
        d.det_kind = a1(scene.det_kind, i32)
        d.det_view = v3(scene.det_view)
        d.tri_uv = wp.array(np.ascontiguousarray(scene.tri_uv, dtype=f32), dtype=wp.float32, device=device)
        from . import spectral

        d.cmf = wp.array(np.ascontiguousarray(spectral.CMF, dtype=f32), dtype=wp.float32, device=device)
        self.data = d
        # sizes of the analysis tables
        self.nface = len(scene.gface_body) if scene.gface_body is not None else 1
        self.ntface = len(scene.tface_gface) if scene.tface_gface is not None else 1
        self.ngrp = len(scene.groups)
        self.ncoarse = int(scene.coarse["total"]) if scene.coarse else 1
        self.scratch: tuple | None = None  # tallies that a replay writes and nobody reads


def _gpu_scene(scene: Scene, device) -> _GpuScene:
    g = getattr(scene, "_gpu", None)
    if g is None:
        g = _GpuScene(scene, device)
        scene._gpu = g
    return g


def _new_tally(scene: Scene, nth: int) -> TallyData:
    nbody = len(scene.bodies)
    ndet = max(len(scene.detectors), 1)
    total_pix = int(scene.total_pix)
    ty = TallyData()
    ty.abs_vol = wp.zeros(nbody, dtype=wp.float64)
    ty.abs_surf = wp.zeros(nbody, dtype=wp.float64)
    ty.special = wp.zeros(K.NSPECIAL, dtype=wp.float64)
    ty.det_pow = wp.zeros(ndet, dtype=wp.float64)
    ty.rcv_pow = wp.zeros(ndet, dtype=wp.float64)
    ty.ex = wp.zeros((total_pix, 3), dtype=wp.float64)
    ty.cone = wp.zeros((total_pix, 3), dtype=wp.float64)
    ty.ex2 = wp.zeros(total_pix, dtype=wp.float64)
    ty.cone2 = wp.zeros(total_pix, dtype=wp.float64)
    ty.ring = wp.zeros((total_pix, NRING), dtype=wp.float64)
    ty.ring2 = wp.zeros((total_pix, NRING), dtype=wp.float64)
    ty.dirh = wp.zeros((ndet, nth, 3), dtype=wp.float64)
    return ty


def _new_ana(gs: _GpuScene, on: bool, n_end: int = 0, dep_cap: int = 0) -> AnaData:
    """Analysis arrays: full size with ``on`` (end records for ``n_end`` rays, ``dep_cap`` deposit records), else one element."""
    an = AnaData()
    nf, ntf, ng, nc = (gs.nface, gs.ntface, gs.ngrp, gs.ncoarse) if on else (1, 1, 1, 1)
    an.face_t = wp.zeros((nf, 4), dtype=wp.float64)
    an.face_from = wp.zeros((ntf, NSLOT), dtype=wp.float64)
    an.face_to = wp.zeros((ntf, NSLOT), dtype=wp.float64)
    an.grp_map = wp.zeros((2, (ng + 1) if on else 1, nc), dtype=wp.float64)
    an.touch_map = wp.zeros((2, max(ng, 1), nc), dtype=wp.float64)
    an.tot_map = wp.zeros((2, nc), dtype=wp.float64)
    cap = max(int(dep_cap), 1)
    ne = max(int(n_end), 1)
    an.dep_n = wp.zeros(1, dtype=wp.int32)
    an.dep_rid = wp.zeros(cap, dtype=wp.int32)
    an.dep_pix = wp.zeros(cap, dtype=wp.int32)
    an.dep_w = wp.zeros((cap, 3), dtype=wp.float32)
    an.dep_lastg = wp.zeros(cap, dtype=wp.int32)
    an.dep_nb = wp.zeros(cap, dtype=wp.int32)
    an.dep_tag = wp.zeros((cap, 4), dtype=wp.int64)
    an.end_out = wp.zeros(ne, dtype=wp.int32)
    an.end_nb = wp.zeros(ne, dtype=wp.int32)
    an.end_tag = wp.zeros((ne, 4), dtype=wp.int64)
    return an


def _no_paths() -> PathData:
    pa = PathData()
    pa.pb = wp.zeros((1, 1), dtype=wp.vec3)
    pa.pn = wp.zeros(1, dtype=wp.int32)
    pa.pgf = wp.zeros((1, 1), dtype=wp.int32)
    pa.pev = wp.zeros((1, 1), dtype=wp.int32)
    pa.pw = wp.zeros((1, 1), dtype=wp.float32)
    return pa


_TALLY_FIELDS = ("abs_vol", "abs_surf", "special", "det_pow", "rcv_pow", "ex", "cone", "ex2", "cone2", "ring", "ring2", "dirh")
_TABLE_FIELDS = ("face_t", "face_from", "face_to", "grp_map", "touch_map", "tot_map")


def _read(ty, an) -> dict:
    out = {k: getattr(ty, k).numpy() for k in _TALLY_FIELDS}
    if an is not None:
        out.update({k: getattr(an, k).numpy() for k in _TABLE_FIELDS})
    return out


def _add_into(acc: dict | None, new: dict) -> dict:
    if acc is None:
        return new
    for k, v in new.items():
        acc[k] = acc[k] + v
    return acc


def _launch(gs, ty, an, pa, o, d, lam, rng, stack, depth0, eps, rr, max_b, nth, esc_len, ana, rid0, log_end, dep_cap, nrec, maxpts):
    n = len(o)
    wp.launch(
        _kernel(bool(ana), int(nrec) > 0), dim=n,
        inputs=[
            gs.mesh.id, gs.data, ty, an, pa,
            wp.array(o.astype(np.float32), dtype=wp.vec3),
            wp.array(d.astype(np.float32), dtype=wp.vec3),
            wp.array(lam.astype(np.float32), dtype=wp.float32),
            wp.array(rng, dtype=wp.uint64),
            wp.array(stack, dtype=wp.int32),
            int(depth0), float(eps), float(rr), int(max_b), int(MAX_SCATTERS), int(nth), float(esc_len),
            int(ana), int(rid0), int(log_end), int(dep_cap), int(gs.ngrp), int(nrec), int(maxpts),
        ],
    )
    wp.synchronize_device(gs.device)


def trace_channel(
    scene: Scene,
    src: sources.ChannelSource,
    n_rays: int,
    seed: int = 1,
    batch: int = BATCH,
    rr_threshold: float = 0.01,
    max_bounces: int = 500,
    theta_bins: int = 12,
    record_paths: int = 0,
    max_path_points: int = 256,
    progress=None,
    start: int = 0,
    analysis: bool = False,
    log_rays: int = 2_000_000,
) -> Tally:
    """Same contract as ``tracer.trace_channel`` for rays ``start`` .. ``start + n_rays - 1``."""
    from . import tracer

    wp.init()
    device = wp.get_cuda_device(0)
    log_end = int(log_rays) if analysis else 0
    esc_len = max(scene.diag * 0.05, 1.0)
    hacc: dict | None = None  # sums of the batches that were traced into scratch arrays (the ones with logs)
    dep_parts: list[dict] = []
    end_parts: list[dict] = []
    with wp.ScopedDevice(device):
        gs = _gpu_scene(scene, device)
        nth = int(theta_bins)
        ty = _new_tally(scene, nth)
        an = _new_ana(gs, analysis)  # the batches without logs add into these
        pa = _no_paths()
        depth0 = 1 if src.start_body is not None else 0
        done = int(start)
        end = int(start) + int(n_rays)
        while done < end:
            nb = min(int(batch), end - done)
            o, d, lam, rng = sources.emit(src, nb, seed, done, scene.eps)
            stack = np.zeros((nb, SD), dtype=np.int32)
            if depth0:
                stack[:, 0] = src.start_body
            nlog = max(0, min(nb, log_end - done)) if analysis else 0
            if nlog:
                # a batch with logs goes into scratch arrays: when the deposit log is too small the batch is simply traced again
                cap = nlog * LOG_ROWS_PER_RAY + 1024
                while True:
                    ty_s, an_s = _new_tally(scene, nth), _new_ana(gs, True, nb, cap)
                    _launch(gs, ty_s, an_s, pa, o, d, lam, rng, stack, depth0, scene.eps, rr_threshold, max_bounces, nth, esc_len, 1, done, log_end, cap, 0, 0)
                    n_dep = int(an_s.dep_n.numpy()[0])
                    if n_dep <= cap:
                        break
                    cap = n_dep + n_dep // 4 + 1024
                hacc = _add_into(hacc, _read(ty_s, an_s))
                dep_parts.append(_dep_log(an_s, n_dep))
                end_parts.append(_end_log(an_s, done, nlog))
            else:
                _launch(gs, ty, an, pa, o, d, lam, rng, stack, depth0, scene.eps, rr_threshold, max_bounces, nth, esc_len, 1 if analysis else 0, done, 0, 0, 0, 0)
            done += nb
            if progress:
                progress((done - start) / n_rays)

        total = _read(ty, an if analysis else None)
        if hacc is not None:
            total = _add_into(total, hacc)
        tally = Tally(
            n_rays=n_rays,
            ex=total["ex"], cone=total["cone"], ex2=total["ex2"], cone2=total["cone2"], ring=total["ring"], ring2=total["ring2"],
            dirh=total["dirh"], det_pow=total["det_pow"], rcv_pow=total["rcv_pow"], abs_vol=total["abs_vol"], abs_surf=total["abs_surf"], special=total["special"],
            steps=1,
        )
        if analysis:
            tally.analysis = Analysis(
                face_t=total["face_t"], face_from=total["face_from"], face_to=total["face_to"],
                grp_map=total["grp_map"], touch_map=total["touch_map"], tot_map=total["tot_map"],
                log_end=log_end, dep=_concat(dep_parts), end=_concat(end_parts),
            )
    if record_paths:
        # the pictures of the first rays: the same kernel, so they are the paths these rays took in the run
        ids = np.arange(start, start + min(int(record_paths), int(n_rays)), dtype=np.int64)
        _, pb, pn, aux = replay_arrays(scene, src, seed, ids, rr_threshold, max_bounces, max_path_points)
        # (the path buffers are indexed by ray number as on the CPU: rows 0 .. record_paths - 1 of a run that starts at ray 0)
        tally.paths, tally.path_n, tally.path_aux = pb, pn, aux
    return tally


def _concat(parts: list[dict]) -> dict:
    if not parts:
        return {}
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


def _dep_log(an: AnaData, n: int) -> dict:
    """The deposit records of a batch in the format of ``tracer.Analysis.dep``."""
    if n <= 0:
        return {
            "rid": np.zeros(0, np.int32), "pix": np.zeros(0, np.int32), "tw": np.zeros(0, np.float32), "wy": np.zeros(0, np.float32),
            "wyc": np.zeros(0, np.float32), "lastg": np.zeros(0, np.int8), "nb": np.zeros(0, np.int16), "tag": np.zeros((0, 4), np.int64),
        }
    w = an.dep_w.numpy()[:n]
    return {
        "rid": an.dep_rid.numpy()[:n].astype(np.int32), "pix": an.dep_pix.numpy()[:n].astype(np.int32),
        "tw": w[:, 0].copy(), "wy": w[:, 1].copy(), "wyc": w[:, 2].copy(),
        "lastg": an.dep_lastg.numpy()[:n].astype(np.int8), "nb": np.minimum(an.dep_nb.numpy()[:n], 32000).astype(np.int16),
        "tag": an.dep_tag.numpy()[:n].copy(),
    }


def _end_log(an: AnaData, first: int, n: int) -> dict:
    """One record per logged ray of a batch, in the format of ``tracer.Analysis.end``."""
    return {
        "rid": np.arange(first, first + n, dtype=np.int32), "out": an.end_out.numpy()[:n].astype(np.int8),
        "nb": np.minimum(an.end_nb.numpy()[:n], 32000).astype(np.int16), "tag": an.end_tag.numpy()[:n].copy(),
    }


def replay_arrays(
    scene: Scene,
    src: sources.ChannelSource,
    seed: int,
    ids,
    rr_threshold: float = 0.01,
    max_bounces: int = 500,
    max_points: int = 256,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Trace the rays ``ids`` again with the kernel of the run and return ``(ids, path_buf, path_n, path_aux)`` like ``replay.replay_arrays``."""
    ids = np.ascontiguousarray(ids, dtype=np.int64).reshape(-1)
    m = len(ids)
    if m == 0:
        return ids, np.zeros((0, max_points, 3)), np.zeros(0, dtype=np.int32), np.zeros((0, max_points, 3))
    wp.init()
    device = wp.get_cuda_device(0)
    with wp.ScopedDevice(device):
        gs = _gpu_scene(scene, device)
        if gs.scratch is None:  # the kernel tallies as it goes; a replay has to give it somewhere to write
            gs.scratch = (_new_tally(scene, 4), _new_ana(gs, False))
        ty, an = gs.scratch
        o, d, lam, rng = sources.emit_ids(src, ids, seed, scene.eps)
        pb = np.zeros((m, max_points, 3), dtype=np.float32)
        pb[:, 0] = o.astype(np.float32)
        pgf = np.full((m, max_points), -1, dtype=np.int32)
        pev = np.zeros((m, max_points), dtype=np.int32)
        pw = np.zeros((m, max_points), dtype=np.float32)
        pw[:, 0] = 1.0
        pa = PathData()
        pa.pb = wp.array(pb, dtype=wp.vec3)
        pa.pn = wp.array(np.ones(m, dtype=np.int32), dtype=wp.int32)
        pa.pgf = wp.array(pgf, dtype=wp.int32)
        pa.pev = wp.array(pev, dtype=wp.int32)
        pa.pw = wp.array(pw, dtype=wp.float32)
        stack = np.zeros((m, SD), dtype=np.int32)
        depth0 = 1 if src.start_body is not None else 0
        if depth0:
            stack[:, 0] = src.start_body
        esc_len = max(scene.diag * 0.05, 1.0)
        _launch(gs, ty, an, pa, o, d, lam, rng, stack, depth0, scene.eps, rr_threshold, max_bounces, 4, esc_len, 0, 0, 0, 0, m, max_points)
        pb_out = pa.pb.numpy().astype(np.float64)
        pn_out = pa.pn.numpy().astype(np.int32)
        aux = np.zeros((m, max_points, 3))
        aux[:, :, 0] = pa.pgf.numpy()
        aux[:, :, 1] = pa.pev.numpy()
        aux[:, :, 2] = pa.pw.numpy()
    return ids, pb_out, pn_out, aux
