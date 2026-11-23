"""Exact replay of single rays.

A ray's random stream depends only on the run seed and the ray's number (``sources.emit_ids``), so any ray of a finished
run can be traced again, alone, and gives the same path. The replay records every vertex with its position, the CAD face
that was hit, the event at that vertex and the weight the ray carries after it.
"""

from __future__ import annotations

import numpy as np
from numba import get_num_threads

from . import kernels, sources, spectral
from .scene import Scene
from .tracer import default_params

EVENT_NAMES = {
    kernels.EV_START: "start", kernels.EV_SCAT: "scatter", kernels.EV_SPEC: "specular", kernels.EV_DIFF: "diffuse",
    kernels.EV_FRES: "fresnel", kernels.EV_TIR: "tir", kernels.EV_TRANS: "transmit", kernels.EV_ABS: "absorbed",
    kernels.EV_EXIT: "exit", kernels.EV_END: "end",
}


def replay_arrays(
    scene: Scene,
    src: sources.ChannelSource,
    seed: int,
    ids,
    rr_threshold: float = 0.01,
    max_bounces: int = 500,
    max_points: int = 256,
    solver: str = "cpu",
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Trace the rays ``ids`` again and return the raw buffers ``(ids, path_buf, path_n, path_aux)``.

    ``solver`` is the one that traced the run: a ray of a GPU run only follows its path again on the GPU.
    """
    if solver == "gpu":
        from . import solvers

        return solvers.get_solver("gpu").replay_arrays(scene, src, seed, ids, rr_threshold, max_bounces, max_points)
    ids = np.ascontiguousarray(ids, dtype=np.int64).reshape(-1)
    m = len(ids)
    if m == 0:
        return ids, np.zeros((0, max_points, 3)), np.zeros(0, dtype=np.int32), np.zeros((0, max_points, 3))
    params = default_params(scene, rr_threshold, max_bounces, nrec=m, maxpts=max_points)
    params[kernels.P_NTH] = 4
    path_buf = np.zeros((m, max_points, 3))
    path_n = np.ones(m, dtype=np.int32)
    path_aux = np.zeros((m, max_points, 3))
    path_aux[:, :, 0] = -1
    path_aux[:, 0, 2] = 1.0

    o, d, lam, rng = sources.emit_ids(src, ids, seed, scene.eps)
    path_buf[:, 0] = o
    wt = np.ones(m)
    rid = np.arange(m, dtype=np.int32)  # index into the path buffers
    nbn = np.zeros(m, dtype=np.int32)
    nsc = np.zeros(m, dtype=np.int32)
    stack = np.zeros((m, kernels.SD), dtype=np.int32)
    depth = np.zeros(m, dtype=np.int32)
    if src.start_body is not None:
        stack[:, 0] = src.start_body
        depth[:] = 1

    nbody = len(scene.bodies)
    ndet = max(len(scene.detectors), 1)
    nchunks = max(1, min(get_num_threads(), m // 64))
    abs_vol = np.zeros((nchunks, nbody))
    abs_surf = np.zeros((nchunks, nbody))
    special = np.zeros((nchunks, kernels.NSPECIAL))
    ex_buf = np.zeros((nchunks, scene.total_pix, 3))
    cone_buf = np.zeros((nchunks, scene.total_pix, 3))
    dir_buf = np.zeros((nchunks, ndet, 4, 3))
    det_pow = np.zeros((nchunks, ndet))
    rcv_pow = np.zeros((nchunks, ndet))
    ex2_buf = np.zeros((nchunks, scene.total_pix))
    cone2_buf = np.zeros((nchunks, scene.total_pix))
    ring_buf = np.zeros((nchunks, scene.total_pix, kernels.NRING))  # (the kernel writes them like the other maps; unused here)
    ring2_buf = np.zeros((nchunks, scene.total_pix, kernels.NRING))
    det_ring = scene.det_ring if scene.det_ring is not None else np.ones((ndet, 7))
    face_t = np.zeros((1, 1, 4))
    face_from = np.zeros((1, 1, kernels.NSLOT))
    face_to = np.zeros((1, 1, kernels.NSLOT))
    rtag = np.zeros((1, 4), dtype=np.int64)
    lastg = np.zeros(1, dtype=np.int32)
    lastf = np.zeros(1, dtype=np.int32)
    outc = np.zeros(1, dtype=np.int8)
    dep_pix = np.full(1, -1, dtype=np.int32)
    dep_w = np.zeros((1, 3))
    cmf = np.ascontiguousarray(spectral.CMF)

    while len(o):
        prim, tfar = scene.intersect(o.astype(np.float32), d.astype(np.float32))
        n = len(o)
        alive = np.empty(n, dtype=np.int8)
        nch = max(1, min(nchunks, n // 64))
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
        keep = alive.astype(bool)
        if not keep.all():
            o, d, wt, lam, rid, nbn, nsc, stack, depth, rng = (
                o[keep], d[keep], wt[keep], lam[keep], rid[keep], nbn[keep], nsc[keep], stack[keep], depth[keep], rng[keep],
            )

    return ids, path_buf, path_n, path_aux


def replay(
    scene: Scene,
    src: sources.ChannelSource,
    seed: int,
    ids,
    rr_threshold: float = 0.01,
    max_bounces: int = 500,
    max_points: int = 256,
    solver: str = "cpu",
) -> list[dict]:
    """Trace the rays ``ids`` of a channel again. Returns one dict per ray with ``id``, ``points``, ``gface``, ``event``, ``w``."""
    ids, path_buf, path_n, path_aux = replay_arrays(scene, src, seed, ids, rr_threshold, max_bounces, max_points, solver)
    out = []
    for k in range(len(ids)):
        n = int(path_n[k])
        out.append(
            {
                "id": int(ids[k]),
                "points": np.round(path_buf[k, :n], 3).tolist(),
                "gface": path_aux[k, :n, 0].astype(int).tolist(),
                "event": path_aux[k, :n, 1].astype(int).tolist(),
                "w": np.round(path_aux[k, :n, 2], 6).tolist(),
            }
        )
    return out
