"""Numba kernels of the Monte Carlo tracer (one wavefront step, and the emitters).

Rays are processed in ``nchunks`` independent chunks (one per thread); every chunk owns its own tally buffers
so nothing needs atomics. Random numbers come from a per-ray splitmix64 stream, so a run is reproducible for a
given seed whatever the thread count.

Medium tracking: every ray carries a small stack of the bodies it is inside (``stack``, ``depth``). The medium is
the body on top of the stack (air when empty). Entering a body pushes it, leaving removes it. This handles
parts nested inside other parts and parts that touch.
"""

from __future__ import annotations

import math

import numpy as np
from numba import njit, prange

NL = 81
LAM0 = 380.0
DLAM = 5.0
SD = 6  # medium stack depth

# special tally slots
ESC, LEAK, ROUL, MAXB, ANOM, BACK = 0, 1, 2, 3, 4, 5
NSPECIAL = 8

# angle rings around the viewing cone of an exit surface (see scene.ring_edges_deg): the luminous weight (and its squares) that
# leaves between two edge angles, per pixel, so the Results panel can show another cone angle without tracing again
NRING = 6

# params slots
P_EPS, P_RR, P_MAXB, P_ESCLEN, P_NREC, P_MAXPTS, P_NTH = 0, 1, 2, 3, 4, 5, 6
P_ANA = 7  # 1 = per-ray analysis tags, face tallies and deposit records
P_MAXSC = 8  # cap on volume-scattering events of one ray (surface hits are limited by P_MAXB on their own)
NPARAMS = 9

# -- analysis (see engine/tags.py) -------------------------------------------------------------------------
# A tagged group g (0..62) is column g + 1 of the from/to tables; column 0 is "the LED" (from) or "the exit" (to).
SL_LOST, SL_LEAK = 64, 65
NSLOT = 66
# per-ray tag words (rtag[:, k])
#   0: bit g set when the ray hit tagged group g
#   1: the first 8 groups of its route, 6 bits each (group + 1), route length in bits 48..54
#   2: hash of the whole route (consecutive hits of one group count once)
#   3: event counters, 8 bits each: 0 total internal reflection, 1 Fresnel reflection, 2 transmission, 3 specular,
#      4 diffuse, 5 volume scattering, 6 deposits on the exit surface
# outcomes of a ray (outc)
OUT_ESC, OUT_LEAK, OUT_ABS, OUT_ROUL, OUT_MAXB, OUT_ANOM = 1, 2, 3, 4, 5, 6
# vertex events of a recorded path (path_aux[:, :, 1])
EV_START, EV_SCAT, EV_SPEC, EV_DIFF, EV_FRES, EV_TIR, EV_TRANS, EV_ABS, EV_EXIT, EV_END = 0, 1, 2, 3, 4, 5, 6, 7, 8, 9
_M48 = 0xFFFFFFFFFFFF
_M63 = 0x7FFFFFFFFFFFFFFF

_C0 = np.uint64(0x9E3779B97F4A7C15)
_C1 = np.uint64(0xBF58476D1CE4E5B9)
_C2 = np.uint64(0x94D049BB133111EB)
_S30 = np.uint64(30)
_S27 = np.uint64(27)
_S31 = np.uint64(31)
_S11 = np.uint64(11)
TWO_PI = 2.0 * math.pi


@njit(cache=True, inline="always")
def _rnd(st, i):
    z = st[i] + _C0
    st[i] = z
    z = (z ^ (z >> _S30)) * _C1
    z = (z ^ (z >> _S27)) * _C2
    z = z ^ (z >> _S31)
    return (z >> _S11) * 1.1102230246251565e-16


@njit(cache=True, inline="always")
def _basis(dx, dy, dz):
    if abs(dx) < 0.9:
        ax, ay, az = 1.0, 0.0, 0.0
    else:
        ax, ay, az = 0.0, 1.0, 0.0
    ux = dy * az - dz * ay
    uy = dz * ax - dx * az
    uz = dx * ay - dy * ax
    l = math.sqrt(ux * ux + uy * uy + uz * uz)
    ux /= l
    uy /= l
    uz /= l
    vx = dy * uz - dz * uy
    vy = dz * ux - dx * uz
    vz = dx * uy - dy * ux
    return ux, uy, uz, vx, vy, vz


@njit(cache=True, inline="always")
def _hg_dir(dx, dy, dz, g, r1, r2):
    if abs(g) < 1e-3:
        mu = 1.0 - 2.0 * r1
    else:
        s = (1.0 - g * g) / (1.0 - g + 2.0 * g * r1)
        mu = (1.0 + g * g - s * s) / (2.0 * g)
    if mu > 1.0:
        mu = 1.0
    if mu < -1.0:
        mu = -1.0
    st = math.sqrt(max(0.0, 1.0 - mu * mu))
    phi = TWO_PI * r2
    ux, uy, uz, vx, vy, vz = _basis(dx, dy, dz)
    cp, sp = math.cos(phi), math.sin(phi)
    nx = st * cp * ux + st * sp * vx + mu * dx
    ny = st * cp * uy + st * sp * vy + mu * dy
    nz = st * cp * uz + st * sp * vz + mu * dz
    l = math.sqrt(nx * nx + ny * ny + nz * nz)
    return nx / l, ny / l, nz / l


@njit(cache=True, inline="always")
def _microfacet(nx, ny, nz, sigma, r1, r2):
    """Beckmann slope-distributed microfacet normal around n (sigma = RMS slope; 0 returns n)."""
    if sigma <= 1e-6:
        return nx, ny, nz
    tan2 = -sigma * sigma * math.log(max(1.0 - r1, 1e-12))
    cz = 1.0 / math.sqrt(1.0 + tan2)
    sz = math.sqrt(max(0.0, 1.0 - cz * cz))
    phi = TWO_PI * r2
    ux, uy, uz, vx, vy, vz = _basis(nx, ny, nz)
    mx = sz * math.cos(phi) * ux + sz * math.sin(phi) * vx + cz * nx
    my = sz * math.cos(phi) * uy + sz * math.sin(phi) * vy + cz * ny
    mz = sz * math.cos(phi) * uz + sz * math.sin(phi) * vz + cz * nz
    return mx, my, mz


@njit(cache=True, inline="always")
def _fresnel(cosi, n1, n2):
    """Unpolarised Fresnel reflectance and the refracted cosine (R = 1 on total internal reflection)."""
    eta = n1 / n2
    s2 = eta * eta * (1.0 - cosi * cosi)
    if s2 >= 1.0:
        return 1.0, 0.0
    cost = math.sqrt(1.0 - s2)
    a = n1 * cosi
    b = n2 * cost
    rs = (a - b) / (a + b)
    c = n1 * cost
    e = n2 * cosi
    rp = (c - e) / (c + e)
    return 0.5 * (rs * rs + rp * rp), cost


@njit(cache=True, inline="always")
def _lam_index(lam):
    x = (lam - LAM0) / DLAM
    if x < 0.0:
        x = 0.0
    if x > NL - 1.001:
        x = NL - 1.001
    li = int(x)
    return li, x - li


@njit(cache=True, inline="always")
def _bump(rtag, i, idx):
    """Count one event of kind idx in the packed 8-bit counters of ray i (saturating at 255)."""
    sh = np.int64(8 * idx)
    if ((rtag[i, 3] >> sh) & 255) < 255:
        rtag[i, 3] += np.int64(1) << sh


@njit(cache=True, inline="always")
def _fate(face_to, c, lf, col, w):
    """Weight that leaves the last tagged face lf towards column col (next tagged group, exit, lost, leaked)."""
    if lf >= 0:
        face_to[c, lf, col] += w


@njit(cache=True, inline="always")
def _aux(path_aux, r_id, k, gf, ev, w):
    if k >= 0:
        path_aux[r_id, k, 0] = gf
        path_aux[r_id, k, 1] = ev
        path_aux[r_id, k, 2] = w


@njit(cache=True, inline="always")
def _det_pixel(di, tri, px, py, pz, V, ia, ib, ic, det_o, det_u, det_v, det_ext, det_nuv, det_off, det_mode, tri_uv):
    """Map pixel (index into the maps of all detectors) of the point p on triangle ``tri`` of detector ``di``."""
    qx = px - det_o[di, 0]
    qy = py - det_o[di, 1]
    qz = pz - det_o[di, 2]
    pu = qx * det_u[di, 0] + qy * det_u[di, 1] + qz * det_u[di, 2]
    pv = qx * det_v[di, 0] + qy * det_v[di, 1] + qz * det_v[di, 2]
    if det_mode[di] == 1:  # unwrapped surface: barycentric position in the hit triangle -> its UV
        ax_ = V[ia, 0]
        ay_ = V[ia, 1]
        az_ = V[ia, 2]
        w0x = V[ib, 0] - ax_
        w0y = V[ib, 1] - ay_
        w0z = V[ib, 2] - az_
        w1x = V[ic, 0] - ax_
        w1y = V[ic, 1] - ay_
        w1z = V[ic, 2] - az_
        w2x = px - ax_
        w2y = py - ay_
        w2z = pz - az_
        e00 = w0x * w0x + w0y * w0y + w0z * w0z
        e01 = w0x * w1x + w0y * w1y + w0z * w1z
        e11 = w1x * w1x + w1y * w1y + w1z * w1z
        e20 = w2x * w0x + w2y * w0y + w2z * w0z
        e21 = w2x * w1x + w2y * w1y + w2z * w1z
        dn = e00 * e11 - e01 * e01
        b1 = 0.0
        b2 = 0.0
        if abs(dn) > 1e-24:
            b1 = min(max((e11 * e20 - e01 * e21) / dn, 0.0), 1.0)
            b2 = min(max((e00 * e21 - e01 * e20) / dn, 0.0), 1.0 - b1)
        pu = tri_uv[tri, 0, 0] + b1 * (tri_uv[tri, 1, 0] - tri_uv[tri, 0, 0]) + b2 * (tri_uv[tri, 2, 0] - tri_uv[tri, 0, 0])
        pv = tri_uv[tri, 0, 1] + b1 * (tri_uv[tri, 1, 1] - tri_uv[tri, 0, 1]) + b2 * (tri_uv[tri, 2, 1] - tri_uv[tri, 0, 1])
    nu = det_nuv[di, 0]
    nv = det_nuv[di, 1]
    fx = (pu - det_ext[di, 0]) / (det_ext[di, 1] - det_ext[di, 0])
    fy = (pv - det_ext[di, 2]) / (det_ext[di, 3] - det_ext[di, 2])
    ix = min(max(int(fx * nu), 0), nu - 1)
    iy = min(max(int(fy * nv), 0), nv - 1)
    return det_off[di] + iy * nu + ix


@njit(cache=True, parallel=True)
def step_kernel(
    # ray state (modified in place)
    o, d, wt, lam, rid, nbn, nsc, stack, depth, rng, alive,
    # intersection results
    prim, tfar,
    # geometry
    V, N, T, tri_n, tri_body, tri_fin, tri_det,
    # media and surface finishes
    body_mat, mat_n, mat_ka, mat_mus, mat_g, fin_type, fin_p, fin_spec,
    # detectors
    det_o, det_u, det_v, det_n, det_ext, det_nuv, det_off, det_cos, det_mode, tri_uv,
    # receiver detectors: kind per detector (0 exit surface, 1 receiver) and the direction from the surface towards the viewer
    det_kind, det_view,
    cmf, params, nchunks,
    # tallies (first axis = chunk)
    abs_vol, abs_surf, special, ex_buf, cone_buf, dir_buf, det_pow, ex2_buf, cone2_buf, ring_buf, ring2_buf, det_ring, rcv_pow,
    # path recording
    path_buf, path_n, path_aux,
    # analysis: per-ray tags, outcomes, exit deposits of this step, face tables and tallies
    rtag, lastg, lastf, outc, dep_pix, dep_w,
    tri_group, tri_gface, tri_tface,
    face_t, face_from, face_to,
):
    n = o.shape[0]
    ana = params[P_ANA] > 0.5
    eps = params[P_EPS]
    rr_thr = params[P_RR]
    max_b = params[P_MAXB]
    max_sc = params[P_MAXSC]
    esc_len = params[P_ESCLEN]
    nrec = int(params[P_NREC])
    maxpts = int(params[P_MAXPTS])
    nth = int(params[P_NTH])

    for c in prange(nchunks):
        s0 = c * n // nchunks
        s1 = (c + 1) * n // nchunks
        for i in range(s0, s1):
            alive[i] = 1
            w = wt[i]
            ox, oy, oz = o[i, 0], o[i, 1], o[i, 2]
            dx, dy, dz = d[i, 0], d[i, 1], d[i, 2]
            lm = lam[i]
            dep = depth[i]
            li, lf = _lam_index(lm)
            r_id = rid[i]

            tri = prim[i]
            top = -1
            mat = 0
            if dep > 0:
                top = stack[i, dep - 1]
                mat = body_mat[top]

            # ---- no hit ----
            if tri < 0:
                if dep == 0:
                    special[c, ESC] += w
                else:
                    special[c, LEAK] += w
                if ana:
                    outc[i] = OUT_ESC if dep == 0 else OUT_LEAK
                    _fate(face_to, c, lastf[i], SL_LEAK, w)
                if r_id < nrec:
                    k = path_n[r_id]
                    if k < maxpts:
                        path_buf[r_id, k, 0] = ox + dx * esc_len
                        path_buf[r_id, k, 1] = oy + dy * esc_len
                        path_buf[r_id, k, 2] = oz + dz * esc_len
                        path_n[r_id] = k + 1
                        _aux(path_aux, r_id, k, -1, EV_END, w)
                alive[i] = 0
                continue

            # ---- distance to the hit (exact plane distance, Embree's float32 value as fallback) ----
            nx, ny, nz = tri_n[tri, 0], tri_n[tri, 1], tri_n[tri, 2]
            ia, ib, ic = T[tri, 0], T[tri, 1], T[tri, 2]
            cosg = dx * nx + dy * ny + dz * nz
            t = tfar[i]
            if abs(cosg) > 1e-12:
                tp = ((V[ia, 0] - ox) * nx + (V[ia, 1] - oy) * ny + (V[ia, 2] - oz) * nz) / cosg
                if tp > 0.0 and abs(tp - t) < 1e-3 * max(1.0, t):
                    t = tp

            ka = mat_ka[mat, li] * (1.0 - lf) + mat_ka[mat, li + 1] * lf
            mus = mat_mus[mat, li] * (1.0 - lf) + mat_mus[mat, li + 1] * lf

            # ---- volume scattering before the surface ----
            if mus > 0.0:
                sdist = -math.log(max(_rnd(rng, i), 1e-300)) / mus
                if sdist < t:
                    att = math.exp(-ka * sdist)
                    if top >= 0:
                        abs_vol[c, top] += w * (1.0 - att)
                    if ana:
                        _fate(face_to, c, lastf[i], SL_LOST, w * (1.0 - att))
                        _bump(rtag, i, 5)
                    w *= att
                    ox += dx * sdist
                    oy += dy * sdist
                    oz += dz * sdist
                    g = mat_g[mat, li] * (1.0 - lf) + mat_g[mat, li + 1] * lf
                    dx, dy, dz = _hg_dir(dx, dy, dz, g, _rnd(rng, i), _rnd(rng, i))
                    nbn[i] += 1  # nbn counts every event (analysis); nsc only the volume-scattering ones
                    nsc[i] += 1
                    if r_id < nrec:
                        k = path_n[r_id]
                        if k < maxpts:
                            path_buf[r_id, k, 0] = ox
                            path_buf[r_id, k, 1] = oy
                            path_buf[r_id, k, 2] = oz
                            path_n[r_id] = k + 1
                            _aux(path_aux, r_id, k, -1, EV_SCAT, w)
                    if nsc[i] > max_sc:
                        special[c, MAXB] += w
                        if ana:
                            outc[i] = OUT_MAXB
                            _fate(face_to, c, lastf[i], SL_LOST, w)
                        alive[i] = 0
                        continue
                    # roulette
                    if w < rr_thr:
                        q = w / rr_thr
                        if _rnd(rng, i) < q:
                            special[c, ROUL] += w - rr_thr
                            if ana:
                                _fate(face_to, c, lastf[i], SL_LOST, w - rr_thr)
                            w = rr_thr
                        else:
                            special[c, ROUL] += w
                            if ana:
                                outc[i] = OUT_ROUL
                                _fate(face_to, c, lastf[i], SL_LOST, w)
                            alive[i] = 0
                            continue
                    o[i, 0], o[i, 1], o[i, 2] = ox, oy, oz
                    d[i, 0], d[i, 1], d[i, 2] = dx, dy, dz
                    wt[i] = w
                    continue

            # ---- travel to the surface ----
            if ka > 0.0 and top >= 0:
                att = math.exp(-ka * t)
                abs_vol[c, top] += w * (1.0 - att)
                if ana:
                    _fate(face_to, c, lastf[i], SL_LOST, w * (1.0 - att))
                w *= att
            px = ox + dx * t
            py = oy + dy * t
            pz = oz + dz * t
            pk = -1  # index of this hit in the recorded path
            if r_id < nrec:
                k = path_n[r_id]
                if k < maxpts:
                    path_buf[r_id, k, 0] = px
                    path_buf[r_id, k, 1] = py
                    path_buf[r_id, k, 2] = pz
                    path_n[r_id] = k + 1
                    pk = k
                    _aux(path_aux, r_id, k, tri_gface[tri], EV_TRANS, w)

            nbn[i] += 1
            if nbn[i] - nsc[i] > max_b:  # surface hits only: volume scattering has its own cap
                special[c, MAXB] += w
                if ana:
                    outc[i] = OUT_MAXB
                    _fate(face_to, c, lastf[i], SL_LOST, w)
                alive[i] = 0
                continue

            gf = tri_gface[tri]
            if ana:
                # which tagged face is this, where did the light come from, where did it go since the last tagged face
                face_t[c, gf, 0] += w
                tfc = tri_tface[tri]
                if tfc >= 0:
                    grp = tri_group[tri]
                    face_from[c, tfc, lastg[i] + 1] += w
                    _fate(face_to, c, lastf[i], grp + 1, w)
                    rtag[i, 0] |= np.int64(1) << np.int64(grp)
                    if lastg[i] != grp:
                        rl_ = rtag[i, 1] >> 48
                        route_ = rtag[i, 1] & _M48
                        if rl_ < 8:
                            route_ |= np.int64(grp + 1) << np.int64(6 * rl_)
                        rtag[i, 1] = route_ | (min(rl_ + 1, 127) << 48)
                        rtag[i, 2] = (rtag[i, 2] * 1000003 + (grp + 1)) & _M63
                    lastg[i] = grp
                    lastf[i] = tfc

            body = tri_body[tri]
            fin = tri_fin[tri]
            ftype = fin_type[fin]
            entering = cosg < 0.0

            # geometric normal on the incident side
            if entering:
                gx, gy, gz = nx, ny, nz
            else:
                gx, gy, gz = -nx, -ny, -nz

            # interpolated (smooth) normal on the incident side
            v0x = V[ib, 0] - V[ia, 0]
            v0y = V[ib, 1] - V[ia, 1]
            v0z = V[ib, 2] - V[ia, 2]
            v1x = V[ic, 0] - V[ia, 0]
            v1y = V[ic, 1] - V[ia, 1]
            v1z = V[ic, 2] - V[ia, 2]
            v2x = px - V[ia, 0]
            v2y = py - V[ia, 1]
            v2z = pz - V[ia, 2]
            d00 = v0x * v0x + v0y * v0y + v0z * v0z
            d01 = v0x * v1x + v0y * v1y + v0z * v1z
            d11 = v1x * v1x + v1y * v1y + v1z * v1z
            d20 = v2x * v0x + v2y * v0y + v2z * v0z
            d21 = v2x * v1x + v2y * v1y + v2z * v1z
            den = d00 * d11 - d01 * d01
            sx, sy, sz = gx, gy, gz
            if abs(den) > 1e-24:
                bv = (d11 * d20 - d01 * d21) / den
                bw = (d00 * d21 - d01 * d20) / den
                bu = 1.0 - bv - bw
                bu = min(max(bu, 0.0), 1.0)
                bv = min(max(bv, 0.0), 1.0)
                bw = min(max(bw, 0.0), 1.0)
                bs = bu + bv + bw
                bu /= bs
                bv /= bs
                bw /= bs
                mx = bu * N[ia, 0] + bv * N[ib, 0] + bw * N[ic, 0]
                my = bu * N[ia, 1] + bv * N[ib, 1] + bw * N[ic, 1]
                mz = bu * N[ia, 2] + bv * N[ib, 2] + bw * N[ic, 2]
                ml = math.sqrt(mx * mx + my * my + mz * mz)
                if ml > 1e-9:
                    mx /= ml
                    my /= ml
                    mz /= ml
                    if not entering:
                        mx, my, mz = -mx, -my, -mz
                    if mx * gx + my * gy + mz * gz > 0.2:
                        sx, sy, sz = mx, my, mz

            # =================== receiver detector: light arriving from outside ===================
            # Counted before the surface acts on the ray, and the ray carries on: the incident light (all directions) of the
            # pixel. The part that the finish reflects towards the viewer is counted below (opaque finishes).
            rdi = tri_det[tri]
            rcv = rdi >= 0 and det_kind[rdi] == 1 and entering
            rpix = -1
            rcy = 0.0
            rcx = 0.0
            rcz = 0.0
            if rcv:
                rcx = cmf[li, 0] * (1.0 - lf) + cmf[li + 1, 0] * lf
                rcy = cmf[li, 1] * (1.0 - lf) + cmf[li + 1, 1] * lf
                rcz = cmf[li, 2] * (1.0 - lf) + cmf[li + 1, 2] * lf
                rpix = _det_pixel(rdi, tri, px, py, pz, V, ia, ib, ic, det_o, det_u, det_v, det_ext, det_nuv, det_off, det_mode, tri_uv)
                ex_buf[c, rpix, 0] += w * rcx
                ex_buf[c, rpix, 1] += w * rcy
                ex_buf[c, rpix, 2] += w * rcz
                ex2_buf[c, rpix] += (w * rcy) * (w * rcy)
                rcv_pow[c, rdi] += w
                if ana:
                    dep_pix[i] = rpix
                    dep_w[i, 0] = w
                    dep_w[i, 1] = w * rcy
                    dep_w[i, 2] = 0.0
                    _bump(rtag, i, 6)

            # =================== opaque surface ===================
            if ftype == 1:
                rd = fin_p[fin, 0] * (fin_spec[fin, 0, li] * (1.0 - lf) + fin_spec[fin, 0, li + 1] * lf)
                rs = fin_p[fin, 1] * (fin_spec[fin, 1, li] * (1.0 - lf) + fin_spec[fin, 1, li + 1] * lf)
                sig = fin_p[fin, 2]
                rt = rd + rs
                if rt > 1.0:  # a colour peak cannot reflect more than all of the light
                    rd /= rt
                    rs /= rt
                    rt = 1.0
                abs_surf[c, body] += w * (1.0 - rt)
                if ana:
                    face_t[c, gf, 1] += w * (1.0 - rt)
                    _fate(face_to, c, lastf[i], SL_LOST, w * (1.0 - rt))
                if rt <= 1e-9:
                    if ana:
                        outc[i] = OUT_ABS
                    _aux(path_aux, r_id, pk, gf, EV_EXIT if rcv else EV_ABS, 0.0)  # (a receiver hit is the path's "exit" event)
                    alive[i] = 0
                    continue
                w *= rt
                if ana:
                    face_t[c, gf, 2] += w
                specular = _rnd(rng, i) * rt < rs
                if ana:
                    _bump(rtag, i, 3 if specular else 4)
                _aux(path_aux, r_id, pk, gf, EV_EXIT if rcv else (EV_SPEC if specular else EV_DIFF), w)
                if specular:
                    # specular lobe (optionally rough)
                    mx, my, mz = _microfacet(sx, sy, sz, sig, _rnd(rng, i), _rnd(rng, i))
                    ci = -(dx * mx + dy * my + dz * mz)
                    if ci <= 1e-4:
                        mx, my, mz = gx, gy, gz
                        ci = -(dx * mx + dy * my + dz * mz)
                    rx = dx + 2.0 * ci * mx
                    ry = dy + 2.0 * ci * my
                    rz = dz + 2.0 * ci * mz
                    if rx * gx + ry * gy + rz * gz <= 0.0:
                        cg = -(dx * gx + dy * gy + dz * gz)
                        rx = dx + 2.0 * cg * gx
                        ry = dy + 2.0 * cg * gy
                        rz = dz + 2.0 * cg * gz
                else:
                    # Lambertian lobe
                    r1 = _rnd(rng, i)
                    r2 = _rnd(rng, i)
                    st = math.sqrt(r1)
                    ct = math.sqrt(max(0.0, 1.0 - r1))
                    phi = TWO_PI * r2
                    ux, uy, uz, vx, vy, vz = _basis(gx, gy, gz)
                    rx = st * math.cos(phi) * ux + st * math.sin(phi) * vx + ct * gx
                    ry = st * math.cos(phi) * uy + st * math.sin(phi) * vy + ct * gy
                    rz = st * math.cos(phi) * uz + st * math.sin(phi) * vz + ct * gz
                rl = math.sqrt(rx * rx + ry * ry + rz * rz)
                dx, dy, dz = rx / rl, ry / rl, rz / rl
                if rcv:
                    # receiver: the light the finish sends on, tallied by its direction relative to the viewer (cone and rings around
                    # the view direction); the angular histogram is by angle from the surface normal
                    ctv = dx * det_view[rdi, 0] + dy * det_view[rdi, 1] + dz * det_view[rdi, 2]
                    if ctv >= det_cos[rdi]:
                        cone_buf[c, rpix, 0] += w * rcx
                        cone_buf[c, rpix, 1] += w * rcy
                        cone_buf[c, rpix, 2] += w * rcz
                        cone2_buf[c, rpix] += (w * rcy) * (w * rcy)
                        if ana:
                            dep_w[i, 2] = w * rcy
                    for rr in range(NRING):
                        if ctv <= det_ring[rdi, rr] and ctv > det_ring[rdi, rr + 1]:
                            ring_buf[c, rpix, rr] += w * rcy
                            ring2_buf[c, rpix, rr] += (w * rcy) * (w * rcy)
                            break
                    ctn = dx * gx + dy * gy + dz * gz
                    if ctn > 0.0:
                        thr = min(int(math.acos(min(ctn, 1.0)) / (0.5 * math.pi) * nth), nth - 1)
                        dir_buf[c, rdi, thr, 0] += w * rcx
                        dir_buf[c, rdi, thr, 1] += w * rcy
                        dir_buf[c, rdi, thr, 2] += w * rcz
                ox = px + gx * eps
                oy = py + gy * eps
                oz = pz + gz * eps

            # =================== dielectric interface ===================
            else:
                sig = fin_p[fin, 0]
                # media on both sides
                n1 = mat_n[mat, li] * (1.0 - lf) + mat_n[mat, li + 1] * lf
                pos = -1
                for kk in range(dep):
                    if stack[i, kk] == body:
                        pos = kk
                if entering:
                    if pos >= 0 or dep >= SD:
                        special[c, ANOM] += w
                        if ana:
                            outc[i] = OUT_ANOM
                            _fate(face_to, c, lastf[i], SL_LOST, w)
                        alive[i] = 0
                        continue
                    m2 = body_mat[body]
                else:
                    if pos < 0:
                        special[c, ANOM] += w
                        if ana:
                            outc[i] = OUT_ANOM
                            _fate(face_to, c, lastf[i], SL_LOST, w)
                        alive[i] = 0
                        continue
                    # medium after leaving: the next body down the stack (skipping this one)
                    m2 = 0
                    for kk in range(dep - 1, -1, -1):
                        if kk != pos:
                            m2 = body_mat[stack[i, kk]]
                            break
                n2 = mat_n[m2, li] * (1.0 - lf) + mat_n[m2, li + 1] * lf

                mx, my, mz = _microfacet(sx, sy, sz, sig, _rnd(rng, i), _rnd(rng, i))
                ci = -(dx * mx + dy * my + dz * mz)
                if ci <= 1e-4:
                    mx, my, mz = sx, sy, sz
                    ci = -(dx * mx + dy * my + dz * mz)
                if ci < 0.0:
                    mx, my, mz = gx, gy, gz
                    ci = -(dx * mx + dy * my + dz * mz)
                ci = min(max(ci, 0.0), 1.0)
                R, cost = _fresnel(ci, n1, n2)
                rx = dx + 2.0 * ci * mx
                ry = dy + 2.0 * ci * my
                rz = dz + 2.0 * ci * mz
                if rx * gx + ry * gy + rz * gz <= 0.0:
                    cg = -(dx * gx + dy * gy + dz * gz)
                    rx = dx + 2.0 * cg * gx
                    ry = dy + 2.0 * cg * gy
                    rz = dz + 2.0 * cg * gz
                eta = n1 / n2
                tx = eta * dx + (eta * ci - cost) * mx
                ty = eta * dy + (eta * ci - cost) * my
                tz = eta * dz + (eta * ci - cost) * mz
                tl = math.sqrt(tx * tx + ty * ty + tz * tz)
                tx /= tl
                ty /= tl
                tz /= tl
                if tx * gx + ty * gy + tz * gz >= 0.0:  # refracted ray on the wrong side: cannot transmit
                    R = 1.0

                di = tri_det[tri]
                # an exit surface (kind 0) takes the transmitted part out of the ray; a pass-through exit (kind 2) only measures it
                tally = di >= 0 and (not entering) and m2 == 0 and R < 1.0 and (det_kind[di] == 0 or det_kind[di] == 2)
                thru = tally and det_kind[di] == 2
                forced = tally and not thru
                reflect = False
                if tally:
                    # forced detection: the transmitted part is tallied (consuming exit: the reflected part carries on, below)
                    tw = w * (1.0 - R)
                    pix = _det_pixel(di, tri, px, py, pz, V, ia, ib, ic, det_o, det_u, det_v, det_ext, det_nuv, det_off, det_mode, tri_uv)
                    ct = tx * det_n[di, 0] + ty * det_n[di, 1] + tz * det_n[di, 2]
                    if det_mode[di] == 1:  # curved surface: angle to the local outward normal
                        ct = -(tx * gx + ty * gy + tz * gz)
                    cx0 = cmf[li, 0] * (1.0 - lf) + cmf[li + 1, 0] * lf
                    cy0 = cmf[li, 1] * (1.0 - lf) + cmf[li + 1, 1] * lf
                    cz0 = cmf[li, 2] * (1.0 - lf) + cmf[li + 1, 2] * lf
                    ex_buf[c, pix, 0] += tw * cx0
                    ex_buf[c, pix, 1] += tw * cy0
                    ex_buf[c, pix, 2] += tw * cz0
                    ex2_buf[c, pix] += (tw * cy0) * (tw * cy0)  # squares of the luminous deposits: the noise of the map
                    if thru:
                        rcv_pow[c, di] += tw  # (not an outcome of the ray: the budget does not count it)
                    else:
                        det_pow[c, di] += tw
                    if ana:
                        dep_pix[i] = pix
                        dep_w[i, 0] = tw
                        dep_w[i, 1] = tw * cy0  # luminous weight, all directions
                        dep_w[i, 2] = tw * cy0 if (ct > 0.0 and ct >= det_cos[di]) else 0.0  # ... and inside the viewing cone
                        if forced:  # (a pass-through exit leaves the face tallies to the ordinary Fresnel step below)
                            _fate(face_to, c, lastf[i], 0, tw)
                            face_t[c, gf, 3] += tw
                        _bump(rtag, i, 6)
                    if ct > 0.0:
                        if ct >= det_cos[di]:
                            cone_buf[c, pix, 0] += tw * cx0
                            cone_buf[c, pix, 1] += tw * cy0
                            cone_buf[c, pix, 2] += tw * cz0
                            cone2_buf[c, pix] += (tw * cy0) * (tw * cy0)
                        # the rings around the cone: ring r holds the rays with cos(e_r) >= ct > cos(e_r+1) (det_ring: cos of the edges, falling)
                        for rr in range(NRING):
                            if ct <= det_ring[di, rr] and ct > det_ring[di, rr + 1]:
                                ring_buf[c, pix, rr] += tw * cy0
                                ring2_buf[c, pix, rr] += (tw * cy0) * (tw * cy0)
                                break
                        thb = min(int(math.acos(min(ct, 1.0)) / (0.5 * math.pi) * nth), nth - 1)
                        dir_buf[c, di, thb, 0] += tw * cx0
                        dir_buf[c, di, thb, 1] += tw * cy0
                        dir_buf[c, di, thb, 2] += tw * cz0
                    else:
                        special[c, BACK] += tw
                if forced:
                    w *= R
                    reflect = True
                    if ana:
                        face_t[c, gf, 2] += w
                    _aux(path_aux, r_id, pk, gf, EV_EXIT, w)
                else:
                    reflect = _rnd(rng, i) < R
                    if ana:
                        if reflect:
                            face_t[c, gf, 2] += w
                            _bump(rtag, i, 0 if R >= 1.0 else 1)
                        else:
                            face_t[c, gf, 3] += w
                            _bump(rtag, i, 2)
                    if reflect:
                        _aux(path_aux, r_id, pk, gf, EV_EXIT if (rcv or thru) else (EV_TIR if R >= 1.0 else EV_FRES), w)
                    else:
                        _aux(path_aux, r_id, pk, gf, EV_EXIT if (rcv or thru) else EV_TRANS, w)

                if reflect:
                    dx, dy, dz = rx, ry, rz
                    rl = math.sqrt(dx * dx + dy * dy + dz * dz)
                    dx /= rl
                    dy /= rl
                    dz /= rl
                    ox = px + gx * eps
                    oy = py + gy * eps
                    oz = pz + gz * eps
                    if w < 1e-30:
                        if ana:
                            outc[i] = OUT_ABS
                        alive[i] = 0
                        continue
                else:
                    dx, dy, dz = tx, ty, tz
                    ox = px - gx * eps
                    oy = py - gy * eps
                    oz = pz - gz * eps
                    if entering:
                        stack[i, dep] = body
                        depth[i] = dep + 1
                    else:
                        for kk in range(pos, dep - 1):
                            stack[i, kk] = stack[i, kk + 1]
                        depth[i] = dep - 1

            # ---- roulette ----
            if w < rr_thr:
                q = w / rr_thr
                if _rnd(rng, i) < q:
                    special[c, ROUL] += w - rr_thr
                    if ana:
                        _fate(face_to, c, lastf[i], SL_LOST, w - rr_thr)
                    w = rr_thr
                else:
                    special[c, ROUL] += w
                    if ana:
                        outc[i] = OUT_ROUL
                        _fate(face_to, c, lastf[i], SL_LOST, w)
                    alive[i] = 0
                    continue
            o[i, 0], o[i, 1], o[i, 2] = ox, oy, oz
            d[i, 0], d[i, 1], d[i, 2] = dx, dy, dz
            wt[i] = w


@njit(cache=True, parallel=True)
def emit_kernel(
    n, seed, ids,
    # emitter geometry: triangles (area CDF) or rectangle/disc (flag)
    mode, tri_v0, tri_e1, tri_e2, tri_cdf, rect_o, rect_u, rect_v,
    axis, ex, ey, eps,
    # direction pattern (theta grid 0..pi) and spectrum CDFs
    pat_theta, pat_cdf, spec_cdf,
    out_o, out_d, out_lam, out_rng,
):
    for i in prange(n):
        st = np.empty(1, dtype=np.uint64)
        gid = np.uint64(ids[i])  # the number of the ray in the run: its random stream depends on nothing else
        st[0] = (np.uint64(seed) * _C0) ^ (gid * _C1 + _C2)
        # warm up
        _rnd(st, 0)
        _rnd(st, 0)
        # position
        if mode == 0:
            r = _rnd(st, 0)
            lo, hi = 0, tri_cdf.shape[0] - 1
            while lo < hi:
                mid = (lo + hi) // 2
                if tri_cdf[mid] < r:
                    lo = mid + 1
                else:
                    hi = mid
            k = lo
            a = _rnd(st, 0)
            b = _rnd(st, 0)
            if a + b > 1.0:
                a = 1.0 - a
                b = 1.0 - b
            px = tri_v0[k, 0] + a * tri_e1[k, 0] + b * tri_e2[k, 0]
            py = tri_v0[k, 1] + a * tri_e1[k, 1] + b * tri_e2[k, 1]
            pz = tri_v0[k, 2] + a * tri_e1[k, 2] + b * tri_e2[k, 2]
        elif mode == 1:  # rectangle
            a = _rnd(st, 0) - 0.5
            b = _rnd(st, 0) - 0.5
            px = rect_o[0] + a * rect_u[0] + b * rect_v[0]
            py = rect_o[1] + a * rect_u[1] + b * rect_v[1]
            pz = rect_o[2] + a * rect_u[2] + b * rect_v[2]
        else:  # ellipse / disc
            rr = math.sqrt(_rnd(st, 0))
            ph = TWO_PI * _rnd(st, 0)
            a = 0.5 * rr * math.cos(ph)
            b = 0.5 * rr * math.sin(ph)
            px = rect_o[0] + a * rect_u[0] + b * rect_v[0]
            py = rect_o[1] + a * rect_u[1] + b * rect_v[1]
            pz = rect_o[2] + a * rect_u[2] + b * rect_v[2]
        # direction from the pattern
        r = _rnd(st, 0)
        lo, hi = 0, pat_cdf.shape[0] - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if pat_cdf[mid] < r:
                lo = mid + 1
            else:
                hi = mid
        k = max(lo, 1)
        c0, c1 = pat_cdf[k - 1], pat_cdf[k]
        f = 0.0 if c1 <= c0 else (r - c0) / (c1 - c0)
        theta = pat_theta[k - 1] + f * (pat_theta[k] - pat_theta[k - 1])
        phi = TWO_PI * _rnd(st, 0)
        stt = math.sin(theta)
        ctt = math.cos(theta)
        dx = ctt * axis[0] + stt * (math.cos(phi) * ex[0] + math.sin(phi) * ey[0])
        dy = ctt * axis[1] + stt * (math.cos(phi) * ex[1] + math.sin(phi) * ey[1])
        dz = ctt * axis[2] + stt * (math.cos(phi) * ex[2] + math.sin(phi) * ey[2])
        # wavelength
        r = _rnd(st, 0)
        lo, hi = 0, spec_cdf.shape[0] - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if spec_cdf[mid] < r:
                lo = mid + 1
            else:
                hi = mid
        # bin lo covers [LAM0 + lo*DLAM - DLAM/2, +DLAM/2]
        lam = LAM0 + DLAM * (lo + _rnd(st, 0) - 0.5)
        lam = min(max(lam, LAM0), LAM0 + DLAM * (NL - 1))
        out_o[i, 0] = px + axis[0] * eps
        out_o[i, 1] = py + axis[1] * eps
        out_o[i, 2] = pz + axis[2] * eps
        out_d[i, 0] = dx
        out_d[i, 1] = dy
        out_d[i, 2] = dz
        out_lam[i] = lam
        out_rng[i] = st[0]
