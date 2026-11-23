"""Wavefront Monte Carlo tracer: Embree finds the next surface for a whole batch of rays, then a Numba kernel
applies volume and surface events to every ray. Repeats until every ray has ended."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from numba import get_num_threads

from . import kernels, sources, spectral
from .scene import Scene


@dataclass
class Tally:
    """Raw tallies of one source channel, in units of *emitted rays* (every ray starts with weight 1)."""

    n_rays: int
    ex: np.ndarray  # (total_pix, 3) XYZ weighted exitance, all directions
    cone: np.ndarray  # (total_pix, 3) XYZ weighted, only inside the viewing cone
    ex2: np.ndarray  # (total_pix,) sum of the squared luminous deposits (all directions): the variance of ex[:, 1]
    cone2: np.ndarray  # (total_pix,) the same for the viewing cone
    dirh: np.ndarray  # (ndet, nth, 3) XYZ weighted per polar angle bin
    det_pow: np.ndarray  # (ndet,) radiometric weight that reached each detector
    abs_vol: np.ndarray  # (nbody,)
    abs_surf: np.ndarray  # (nbody,)
    special: np.ndarray  # (NSPECIAL,)
    paths: np.ndarray | None = None  # (nrec, maxpts, 3)
    path_n: np.ndarray | None = None
    path_aux: np.ndarray | None = None  # (nrec, maxpts, 3): CAD face number, event, weight after the event
    steps: int = 0
    analysis: "Analysis | None" = None
    ring: np.ndarray | None = None  # (total_pix, NRING) luminous weight Y in the angle rings around the viewing cone (scene.ring_edges_deg)
    ring2: np.ndarray | None = None  # (total_pix, NRING) squares of the luminous deposits in the rings: their variance
    # (ndet,) radiometric weight that arrived on each receiver detector. The ray carries on after that, so this is not part of the
    # energy budget (total_accounted) and a ray that hits the receiver again counts again.
    rcv_pow: np.ndarray | None = None

    def total_accounted(self) -> float:
        s = self.special
        return float(
            self.abs_vol.sum() + self.abs_surf.sum() + self.det_pow.sum()
            + s[kernels.ESC] + s[kernels.LEAK] + s[kernels.ROUL] + s[kernels.MAXB] + s[kernels.ANOM]
        )


@dataclass
class Analysis:
    """What the tracer learns about *where* the light went (only filled when ``analysis=True``)."""

    face_t: np.ndarray  # (nface, 4): weight incident on, absorbed by, reflected by, transmitted through every CAD face
    face_from: np.ndarray  # (ntface, NSLOT): weight incident on a tagged face, by the group the ray came from (col 0 = the LED)
    face_to: np.ndarray  # (ntface, NSLOT): where the light of a tagged face went next (col 0 = the exit surface)
    grp_map: np.ndarray  # (2, ngroup + 1, ncoarse): luminance (Y) on the coarse exit map by the last group touched (row 0 = none); [0] all directions, [1] viewing cone
    touch_map: np.ndarray  # (2, ngroup, ncoarse): luminance of the rays that touched the group at any point
    tot_map: np.ndarray  # (2, ncoarse)
    log_end: int  # rays with a number below this have a row in the logs
    dep: dict = field(default_factory=dict)  # exit deposits: rid, pix, tw, wy, lastg, nb, tag
    end: dict = field(default_factory=dict)  # one row per ray: rid, out, nb, tag


MAX_SCATTERS = 20000  # volume-scattering events per ray; strongly scattering parts need far more of them than surface hits


def default_params(scene: Scene, rr_threshold: float = 0.01, max_bounces: int = 500, nrec: int = 0, maxpts: int = 0, analysis: bool = False) -> np.ndarray:
    p = np.zeros(kernels.NPARAMS)
    p[kernels.P_EPS] = scene.eps
    p[kernels.P_RR] = rr_threshold
    p[kernels.P_MAXB] = max_bounces
    p[kernels.P_MAXSC] = MAX_SCATTERS
    p[kernels.P_ESCLEN] = max(scene.diag * 0.05, 1.0)
    p[kernels.P_NREC] = nrec
    p[kernels.P_MAXPTS] = maxpts
    p[kernels.P_ANA] = 1.0 if analysis else 0.0
    return p


def _concat(parts: list[dict]) -> dict:
    if not parts:
        return {}
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


def trace_channel(
    scene: Scene,
    src: sources.ChannelSource,
    n_rays: int,
    seed: int = 1,
    batch: int = 250_000,
    rr_threshold: float = 0.01,
    max_bounces: int = 500,
    theta_bins: int = 12,
    record_paths: int = 0,
    max_path_points: int = 256,
    progress: Callable[[float], None] | None = None,
    start: int = 0,
    analysis: bool = False,
    log_rays: int = 2_000_000,
) -> Tally:
    """Trace rays `start` .. `start + n_rays - 1` of the channel's random stream (a later pass continues where the last stopped).

    With ``analysis`` every ray also carries tags (the analysis groups it hit, its route, event counts) and the tally gets an
    :class:`Analysis`: face tallies, exit maps split by group and logs of the exit deposits and of how every ray ended
    (for rays numbered below ``log_rays``).
    """
    nbody = len(scene.bodies)
    ndet = max(len(scene.detectors), 1)
    nth = int(theta_bins)
    nrec = int(record_paths)
    params = default_params(scene, rr_threshold, max_bounces, nrec, max_path_points, analysis)
    params[kernels.P_NTH] = nth
    tally = Tally(
        n_rays=n_rays,
        ex=np.zeros((scene.total_pix, 3)),
        cone=np.zeros((scene.total_pix, 3)),
        ex2=np.zeros(scene.total_pix),
        cone2=np.zeros(scene.total_pix),
        dirh=np.zeros((ndet, nth, 3)),
        det_pow=np.zeros(ndet),
        abs_vol=np.zeros(nbody),
        abs_surf=np.zeros(nbody),
        special=np.zeros(kernels.NSPECIAL),
        ring=np.zeros((scene.total_pix, kernels.NRING)),
        ring2=np.zeros((scene.total_pix, kernels.NRING)),
        rcv_pow=np.zeros(ndet),
    )
    if nrec:
        tally.paths = np.zeros((nrec, max_path_points, 3))
        tally.path_n = np.zeros(nrec, dtype=np.int32)
        tally.path_aux = np.zeros((nrec, max_path_points, 3))
        tally.path_aux[:, :, 0] = -1
    path_buf = tally.paths if nrec else np.zeros((1, 1, 3))
    path_n = tally.path_n if nrec else np.zeros(1, dtype=np.int32)
    path_aux = tally.path_aux if nrec else np.zeros((1, 1, 3))
    cmf = np.ascontiguousarray(spectral.CMF)
    threads = max(get_num_threads(), 1)
    # the angle rings are tallied per thread chunk like the maps, but over the whole call (a batch uses the first ``nchunks`` chunks):
    # 12 values per pixel and thread would be a lot to allocate again for every batch
    ring_buf = np.zeros((threads, scene.total_pix, kernels.NRING))
    ring2_buf = np.zeros((threads, scene.total_pix, kernels.NRING))
    det_ring = scene.det_ring if scene.det_ring is not None else np.ones((ndet, 7))

    nface = len(scene.gface_body)
    ntface = len(scene.tface_gface)
    ng = len(scene.groups)
    ncoarse = int(scene.coarse["total"]) if scene.coarse else 1
    cmap = scene.coarse["cmap"] if scene.coarse else np.zeros(1, dtype=np.int32)
    if analysis:
        ana = Analysis(
            face_t=np.zeros((nface, 4)),
            face_from=np.zeros((ntface, kernels.NSLOT)),
            face_to=np.zeros((ntface, kernels.NSLOT)),
            grp_map=np.zeros((2, ng + 1, ncoarse)),
            touch_map=np.zeros((2, max(ng, 1), ncoarse)),
            tot_map=np.zeros((2, ncoarse)),
            log_end=int(log_rays),
        )
        tally.analysis = ana
    dep_parts: list[dict] = []
    end_parts: list[dict] = []
    log_end = int(log_rays)

    done = int(start)
    end = int(start) + n_rays
    while done < end:
        nb = min(batch, end - done)
        o, d, lam, rng = sources.emit(src, nb, seed, done, scene.eps)
        wt = np.ones(nb)
        rid = np.arange(done, done + nb, dtype=np.int32)
        nbn = np.zeros(nb, dtype=np.int32)
        nsc = np.zeros(nb, dtype=np.int32)
        stack = np.zeros((nb, kernels.SD), dtype=np.int32)
        depth = np.zeros(nb, dtype=np.int32)
        if src.start_body is not None:
            stack[:, 0] = src.start_body
            depth[:] = 1
        if nrec and done < nrec:
            k = min(nrec - done, nb)
            path_buf[done : done + k, 0] = o[:k]
            path_n[done : done + k] = 1
            path_aux[done : done + k, 0, 0] = -1
        if analysis:
            rtag = np.zeros((nb, 4), dtype=np.int64)
            lastg = np.full(nb, -1, dtype=np.int32)
            lastf = np.full(nb, -1, dtype=np.int32)
            outc = np.zeros(nb, dtype=np.int8)

        nchunks = max(1, min(threads, nb // 512))
        abs_vol = np.zeros((nchunks, nbody))
        abs_surf = np.zeros((nchunks, nbody))
        special = np.zeros((nchunks, kernels.NSPECIAL))
        ex_buf = np.zeros((nchunks, scene.total_pix, 3))
        cone_buf = np.zeros((nchunks, scene.total_pix, 3))
        dir_buf = np.zeros((nchunks, ndet, nth, 3))
        det_pow = np.zeros((nchunks, ndet))
        rcv_pow = np.zeros((nchunks, ndet))
        ex2_buf = np.zeros((nchunks, scene.total_pix))
        cone2_buf = np.zeros((nchunks, scene.total_pix))
        if analysis:
            face_t = np.zeros((nchunks, nface, 4))
            face_from = np.zeros((nchunks, ntface, kernels.NSLOT))
            face_to = np.zeros((nchunks, ntface, kernels.NSLOT))
        else:
            face_t = np.zeros((1, 1, 4))
            face_from = np.zeros((1, 1, kernels.NSLOT))
            face_to = np.zeros((1, 1, kernels.NSLOT))
            rtag = np.zeros((1, 4), dtype=np.int64)
            lastg = np.zeros(1, dtype=np.int32)
            lastf = np.zeros(1, dtype=np.int32)
            outc = np.zeros(1, dtype=np.int8)

        while len(o):
            prim, tfar = scene.intersect(o.astype(np.float32), d.astype(np.float32))
            n = len(o)
            alive = np.empty(n, dtype=np.int8)
            nch = max(1, min(nchunks, n // 64))
            if analysis:
                dep_pix = np.full(n, -1, dtype=np.int32)
                dep_w = np.zeros((n, 3))
            else:
                dep_pix = np.full(1, -1, dtype=np.int32)
                dep_w = np.zeros((1, 3))
            kernels.step_kernel(
                o, d, wt, lam, rid, nbn, nsc, stack, depth, rng, alive,
                prim, tfar,
                scene.V, scene.N, scene.T, scene.tri_n, scene.tri_body, scene.tri_fin, scene.tri_det,
                scene.body_mat, scene.mat_n, scene.mat_ka, scene.mat_mus, scene.mat_g, scene.fin_type, scene.fin_p, scene.fin_spec,
                scene.det_o, scene.det_u, scene.det_v, scene.det_n, scene.det_ext, scene.det_nuv, scene.det_off, scene.det_cos, scene.det_mode, scene.tri_uv,
                scene.det_kind, scene.det_view,
                cmf, params, nch,
                abs_vol, abs_surf, special, ex_buf, cone_buf, dir_buf, det_pow, ex2_buf, cone2_buf, ring_buf, ring2_buf, det_ring, rcv_pow,
                path_buf, path_n, path_aux,
                rtag, lastg, lastf, outc, dep_pix, dep_w,
                scene.tri_group, scene.tri_gface, scene.tri_tface,
                face_t, face_from, face_to,
            )
            tally.steps += 1
            keep = alive.astype(bool)
            if analysis:
                _collect_step(ana, cmap, ng, ncoarse, log_end, rid, nbn, rtag, lastg, outc, dep_pix, dep_w, keep, dep_parts, end_parts)
            if not keep.all():
                o, d, wt, lam, rid, nbn, nsc, stack, depth, rng = (
                    o[keep], d[keep], wt[keep], lam[keep], rid[keep], nbn[keep], nsc[keep], stack[keep], depth[keep], rng[keep],
                )
                if analysis:
                    rtag, lastg, lastf, outc = rtag[keep], lastg[keep], lastf[keep], outc[keep]

        tally.abs_vol += abs_vol.sum(axis=0)
        tally.abs_surf += abs_surf.sum(axis=0)
        tally.special += special.sum(axis=0)
        tally.ex += ex_buf.sum(axis=0)
        tally.cone += cone_buf.sum(axis=0)
        tally.ex2 += ex2_buf.sum(axis=0)
        tally.cone2 += cone2_buf.sum(axis=0)
        tally.dirh += dir_buf.sum(axis=0)
        tally.det_pow += det_pow.sum(axis=0)
        tally.rcv_pow += rcv_pow.sum(axis=0)
        if analysis:
            ana.face_t += face_t.sum(axis=0)
            ana.face_from += face_from.sum(axis=0)
            ana.face_to += face_to.sum(axis=0)
            dep_parts = [_concat(dep_parts)] if dep_parts else []
            end_parts = [_concat(end_parts)] if end_parts else []
        done += nb
        if progress:
            progress((done - start) / n_rays)
    tally.ring += ring_buf.sum(axis=0)
    tally.ring2 += ring2_buf.sum(axis=0)
    if analysis:
        ana.dep = _concat(dep_parts)
        ana.end = _concat(end_parts)
    return tally


def _collect_step(ana, cmap, ng, ncoarse, log_end, rid, nbn, rtag, lastg, outc, dep_pix, dep_w, keep, dep_parts, end_parts) -> None:
    """Fold the exit deposits of one step into the group maps and the logs, and log the rays that have just ended."""
    m = dep_pix >= 0
    if m.any():
        pix = dep_pix[m]
        cp = cmap[pix]
        slot = lastg[m].astype(np.int64) + 1
        tag = rtag[m]
        present = int(np.bitwise_or.reduce(tag[:, 0]))
        for view in (0, 1):  # all directions, viewing cone
            wy = dep_w[m, 1 + view]
            ana.grp_map[view] += np.bincount(slot * ncoarse + cp, weights=wy, minlength=(ng + 1) * ncoarse).reshape(ng + 1, ncoarse)
            ana.tot_map[view] += np.bincount(cp, weights=wy, minlength=ncoarse)
            for g in range(ng):
                if (present >> g) & 1:
                    sel = ((tag[:, 0] >> g) & 1).astype(bool)
                    ana.touch_map[view, g] += np.bincount(cp[sel], weights=wy[sel], minlength=ncoarse)
        lg = m & (rid < log_end)
        if lg.any():
            dep_parts.append(
                {
                    "rid": rid[lg], "pix": dep_pix[lg], "tw": dep_w[lg, 0].astype(np.float32), "wy": dep_w[lg, 1].astype(np.float32),
                    "wyc": dep_w[lg, 2].astype(np.float32),
                    "lastg": lastg[lg].astype(np.int8), "nb": np.minimum(nbn[lg], 32000).astype(np.int16), "tag": rtag[lg].copy(),
                }
            )
    ended = (~keep) & (rid < log_end)
    if ended.any():
        end_parts.append({"rid": rid[ended], "out": outc[ended], "nb": np.minimum(nbn[ended], 32000).astype(np.int16), "tag": rtag[ended].copy()})
