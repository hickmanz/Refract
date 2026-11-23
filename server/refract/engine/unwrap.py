"""Flattening of a curved exit surface (least squares conformal maps, Levy et al. 2002).

The exit-surface map normally uses a flat projection along the average normal. That is exact for a flat face and
fine for a gentle curve, but a strongly curved face (a rounded light pipe top, a wrap around a cylinder) is
foreshortened where it turns away and may fold over itself. Unwrapping gives every point of the surface its own
(u, v) in millimetres so that pixels cover equal areas and distances along the surface are kept (angles exactly,
lengths approximately; exact for developable surfaces such as cylinders and cones).

Each connected patch is unwrapped separately and the patches are placed side by side. A patch has to be a disc
(no holes, no closed tube); otherwise ``unwrap_faces`` reports it so the caller can fall back to the flat map.
"""

from __future__ import annotations

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve

PATCH_GAP = 0.05  # gap between patches, as a fraction of the larger patch size


def _components(tris: np.ndarray) -> list[np.ndarray]:
    """Triangle index groups that share vertices through edges."""
    nt = len(tris)
    parent = np.arange(nt)

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    edge_owner: dict[tuple[int, int], int] = {}
    for t in range(nt):
        a, b, c = (int(x) for x in tris[t])
        for e in ((a, b), (b, c), (c, a)):
            key = (min(e), max(e))
            o = edge_owner.get(key)
            if o is None:
                edge_owner[key] = t
            else:
                ra, rb = find(o), find(t)
                if ra != rb:
                    parent[ra] = rb
    groups: dict[int, list[int]] = {}
    for t in range(nt):
        groups.setdefault(find(t), []).append(t)
    return [np.array(g) for g in groups.values()]


def _is_disc(tris: np.ndarray) -> bool:
    edges = {}
    for a, b, c in tris:
        for e in ((a, b), (b, c), (c, a)):
            key = (min(e), max(e))
            edges[key] = edges.get(key, 0) + 1
    if any(n > 2 for n in edges.values()):
        return False
    verts = len(np.unique(tris))
    chi = verts - len(edges) + len(tris)
    return chi == 1


def lscm(P: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """UV (n, 2) for the vertices ``P`` (n, 3) of a disc-like triangle patch (indices into ``P``)."""
    used, inv = np.unique(tris, return_inverse=True)
    X = P[used]
    T = inv.reshape(-1, 3)
    n = len(used)
    nt = len(T)
    p1, p2, p3 = X[T[:, 0]], X[T[:, 1]], X[T[:, 2]]
    e1 = p2 - p1
    e2 = p3 - p1
    l1 = np.linalg.norm(e1, axis=1)
    nrm = np.cross(e1, e2)
    area2 = np.linalg.norm(nrm, axis=1)
    ok = (l1 > 1e-12) & (area2 > 1e-12)
    if not ok.all():
        raise ValueError("The surface contains degenerate triangles")
    ex = e1 / l1[:, None]
    ey = np.cross(nrm / area2[:, None], ex)
    x2, y2 = l1, np.zeros(nt)
    x3 = np.einsum("ij,ij->i", e2, ex)
    y3 = np.einsum("ij,ij->i", e2, ey)
    # complex weights W_j = (x_{j+2} - x_{j+1}) + i (y_{j+2} - y_{j+1}), scaled by 1/sqrt(2 area)
    x1 = np.zeros(nt)
    y1 = np.zeros(nt)
    sc = 1.0 / np.sqrt(area2)
    W = [
        ((x3 - x2) * sc, (y3 - y2) * sc),
        ((x1 - x3) * sc, (y1 - y3) * sc),
        ((x2 - x1) * sc, (y2 - y1) * sc),
    ]
    rows, cols, vals = [], [], []
    for j in range(3):
        wr, wi = W[j]
        v = T[:, j]
        r = np.arange(nt)
        # real row: sum wr*u - wi*v ; imaginary row (offset nt): sum wi*u + wr*v ; unknowns u = [0, n), v = [n, 2n)
        rows += [r, r, r + nt, r + nt]
        cols += [v, v + n, v, v + n]
        vals += [wr, -wi, wi, wr]
    A = sparse.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(2 * nt, 2 * n))

    # pin the two vertices farthest apart (found from a cheap double sweep)
    a = int(np.argmax(np.linalg.norm(X - X.mean(axis=0), axis=1)))
    b = int(np.argmax(np.linalg.norm(X - X[a], axis=1)))
    a = int(np.argmax(np.linalg.norm(X - X[b], axis=1)))
    dist = float(np.linalg.norm(X[a] - X[b]))
    pinned = np.array([a, a + n, b, b + n])
    pvals = np.array([0.0, 0.0, dist, 0.0])
    free = np.setdiff1d(np.arange(2 * n), pinned)
    Af = A[:, free]
    rhs = -A[:, pinned] @ pvals
    M = (Af.T @ Af).tocsc()
    sol = spsolve(M + sparse.identity(M.shape[0], format="csc") * 1e-12, Af.T @ rhs)
    z = np.zeros(2 * n)
    z[free] = sol
    z[pinned] = pvals
    uv = np.stack([z[:n], z[n:]], axis=1)
    if not np.all(np.isfinite(uv)):
        raise ValueError("The surface could not be flattened")
    # orientation: triangles should keep their winding (u x v along the outward normal)
    a2 = (uv[T[:, 1], 0] - uv[T[:, 0], 0]) * (uv[T[:, 2], 1] - uv[T[:, 0], 1]) - (uv[T[:, 1], 1] - uv[T[:, 0], 1]) * (uv[T[:, 2], 0] - uv[T[:, 0], 0])
    if a2.sum() < 0:
        uv[:, 1] *= -1
    # overall scale so that the flat area equals the surface area
    flat = abs(a2).sum() / 2.0
    true = area2.sum() / 2.0
    if flat > 0:
        uv *= np.sqrt(true / flat)
    out = np.zeros((len(P), 2))
    out[used] = uv
    return out


def _min_box_angle(pts: np.ndarray) -> float:
    """Rotation (radians) that makes the axis-aligned bounding box of `pts` smallest, with the long side along u."""
    angles = np.radians(np.arange(0.0, 90.0, 0.25))
    best, best_a = None, 0.0
    for a in angles:
        c, s = np.cos(a), np.sin(a)
        x = pts[:, 0] * c + pts[:, 1] * s
        y = -pts[:, 0] * s + pts[:, 1] * c
        area = np.ptp(x) * np.ptp(y)
        if best is None or area < best - 1e-12:
            best, best_a = area, a
    c, s = np.cos(best_a), np.sin(best_a)
    x = pts[:, 0] * c + pts[:, 1] * s
    y = -pts[:, 0] * s + pts[:, 1] * c
    if np.ptp(x) < np.ptp(y):
        best_a += np.pi / 2
    return float(best_a)


def unwrap_faces(V: np.ndarray, T: np.ndarray, tri_ids: np.ndarray) -> tuple[np.ndarray, dict]:
    """UV per triangle corner, shape (len(tri_ids), 3, 2), in millimetres, plus a report.

    Raises ValueError (readable) when the faces cannot be flattened as discs.
    """
    tris = T[tri_ids]
    comps = _components(tris)
    parts: list[np.ndarray] = []
    for comp in comps:
        sub = tris[comp]
        if not _is_disc(sub):
            raise ValueError("the exit surface is not a simple open patch (it has a hole, wraps all the way around, or is non-manifold)")
        uv = lscm(V, sub)
        loc = uv[sub]  # (k, 3, 2)
        parts.append(loc)
    # principal axis alignment of the whole set (long direction along u) and patch packing
    packed: list[np.ndarray] = []
    cursor = 0.0
    sizes = []
    for loc in parts:
        pts = np.unique(loc.reshape(-1, 2), axis=0)
        ang = _min_box_angle(pts)
        R = np.array([[np.cos(ang), np.sin(ang)], [-np.sin(ang), np.cos(ang)]])  # proper rotation
        packed.append(loc @ R.T)
        sizes.append(np.ptp((pts @ R.T)[:, 0]))
    gap = PATCH_GAP * (max(sizes) if sizes else 1.0)
    result = np.zeros((len(tris), 3, 2))
    for comp, loc in zip(comps, packed):
        lo = loc.reshape(-1, 2).min(axis=0)
        shift = np.array([cursor - lo[0], -lo[1]])
        result[comp] = loc + shift
        cursor += np.ptp(loc.reshape(-1, 2)[:, 0]) + gap
    pts = result.reshape(-1, 2)
    # centre vertically on the middle row
    result[..., 1] -= 0.5 * (pts[:, 1].min() + pts[:, 1].max())
    return result, {"patches": len(comps)}


def surface_curvature(V: np.ndarray, T: np.ndarray, tri_ids: np.ndarray, tri_nrm: np.ndarray, tri_area: np.ndarray) -> float:
    """Area-weighted mean of 1 - cos(angle to the average normal): 0 for a flat face, about 0.13 at 30 degrees."""
    area = tri_area[tri_ids]
    nrm = tri_nrm[tri_ids]
    s = (nrm * area[:, None]).sum(axis=0)
    if np.linalg.norm(s) < 1e-12:
        return 1.0
    s /= np.linalg.norm(s)
    return float((area * (1.0 - nrm @ s)).sum() / max(area.sum(), 1e-18))
