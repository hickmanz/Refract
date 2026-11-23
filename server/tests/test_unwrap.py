"""Unwrapping of curved exit surfaces."""

import numpy as np
import pytest

from refract.engine import unwrap


def _cylinder_patch(radius=10.0, arc_deg=120.0, height=20.0, nu=24, nv=10):
    th = np.radians(np.linspace(-arc_deg / 2, arc_deg / 2, nu + 1))
    z = np.linspace(0, height, nv + 1)
    P = np.array([[radius * np.sin(t), -radius * np.cos(t) + radius, zz] for zz in z for t in th])
    idx = lambda i, j: j * (nu + 1) + i
    tris = []
    for j in range(nv):
        for i in range(nu):
            a, b, c, d = idx(i, j), idx(i + 1, j), idx(i + 1, j + 1), idx(i, j + 1)
            tris += [(a, b, c), (a, c, d)]
    return P, np.array(tris)


def test_cylinder_unwraps_without_stretching():
    P, T = _cylinder_patch(height=40.0, nv=16)
    uv, rep = unwrap.unwrap_faces(P, T, np.arange(len(T)))
    assert rep["patches"] == 1
    pts = uv.reshape(-1, 2)
    arc = 10.0 * np.radians(120.0)
    lu, lv = np.ptp(pts[:, 0]), np.ptp(pts[:, 1])
    assert abs(lv - arc) / arc < 0.02 and abs(lu - 40.0) < 0.4  # the long direction is u
    # triangle areas are preserved
    a_uv = 0.5 * np.abs((uv[:, 1, 0] - uv[:, 0, 0]) * (uv[:, 2, 1] - uv[:, 0, 1]) - (uv[:, 1, 1] - uv[:, 0, 1]) * (uv[:, 2, 0] - uv[:, 0, 0]))
    a3 = 0.5 * np.linalg.norm(np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]]), axis=1)
    assert abs(a_uv.sum() - a3.sum()) / a3.sum() < 1e-6


def test_curvature_measure_and_rejection():
    P, T = _cylinder_patch(arc_deg=120.0)
    nrm = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
    area = np.linalg.norm(nrm, axis=1) / 2
    nrm /= 2 * area[:, None]
    ids = np.arange(len(T))
    assert unwrap.surface_curvature(P, T, ids, nrm, area) > 0.05
    flat, tf = _cylinder_patch(arc_deg=2.0)
    nrm = np.cross(flat[tf[:, 1]] - flat[tf[:, 0]], flat[tf[:, 2]] - flat[tf[:, 0]])
    area = np.linalg.norm(nrm, axis=1) / 2
    nrm /= 2 * area[:, None]
    assert unwrap.surface_curvature(flat, tf, ids, nrm, area) < 0.01
    # a closed tube (seam) is not a disc
    th = np.linspace(0, 2 * np.pi, 13)[:-1]
    ring = np.array([[np.cos(t), np.sin(t), z] for z in (0.0, 1.0) for t in th])
    tube = []
    for i in range(12):
        a, b = i, (i + 1) % 12
        tube += [(a, b, 12 + b), (a, 12 + b, 12 + a)]
    with pytest.raises(ValueError):
        unwrap.unwrap_faces(ring, np.array(tube), np.arange(len(tube)))
