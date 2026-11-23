"""The studio light of the photoreal view: its frame follows the camera and its strength is normalised."""

import numpy as np
import pytest

mitsuba = pytest.importorskip("mitsuba")

from refract.render import mitsuba_export as mx  # noqa: E402


def test_environment_frame_follows_the_camera():
    m = mx.environment_matrix([0, -100, 30], [0, 0, 0], [0, 0, 1])
    R = m[:3, :3]
    assert np.allclose(R.T @ R, np.eye(3), atol=1e-9)  # a rotation
    assert np.allclose(m[:3, 1], [0, 0, 1])  # +y is the camera's up
    to_camera = np.array([0, -100, 30.0]) - np.zeros(3)
    to_camera[2] = 0
    to_camera /= np.linalg.norm(to_camera)
    assert np.allclose(m[:3, 2], to_camera)  # +z points back at the camera
    # looking straight down must still give a valid frame
    m2 = mx.environment_matrix([0, 0, 100], [0, 0, 0], [0, 0, 1])
    assert np.allclose(m2[:3, :3].T @ m2[:3, :3], np.eye(3), atol=1e-9)


def test_studio_light_is_normalised_for_a_surface_facing_the_camera():
    mx.select_variant()  # sets the Mitsuba variant the app would use
    bm = mx.studio_bitmap()
    L = np.array(bm)[..., 0]
    H, W = L.shape
    th = np.pi * (np.arange(H) + 0.5) / H
    ph = 2 * np.pi * (np.arange(W) + 0.5) / W
    T, P = np.meshgrid(th, ph, indexing="ij")
    z = -np.sin(T) * np.cos(P)  # the +z axis of the frame in Mitsuba's envmap layout
    E = (L * np.clip(z, 0, None) * np.sin(T) * (np.pi / H) * (2 * np.pi / W)).sum() / np.pi
    assert abs(E - 1.0) < 1e-3
    assert L.max() > 2 * L.mean()  # there is a key light, not a flat sky
