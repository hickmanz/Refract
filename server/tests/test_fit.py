"""Datasheet fit of a diffuser (slab model): analytic limits and recovery of known parameters."""

import pytest

from refract.materials import fit


def test_clear_slab_transmission_matches_fresnel():
    n = 1.58
    r = ((n - 1) / (n + 1)) ** 2
    total, haze = fit.simulate_slab(2.0, 0.0, 0.0, n, 0.0, 2000)
    assert total == pytest.approx((1 - r) / (1 + r), abs=1e-6)
    assert haze == pytest.approx(0.0, abs=1e-9)


def test_slab_absorption_beer_lambert():
    n = 1.58
    total, _ = fit.simulate_slab(2.0, 0.0, 0.0, n, 0.1, 2000)
    r = ((n - 1) / (n + 1)) ** 2
    t = (1 - r) ** 2 * 0.8187307531 / (1 - (r * 0.8187307531) ** 2)  # exp(-0.2) per pass
    assert total == pytest.approx(t, rel=1e-3)


def test_fit_recovers_scattering_coefficient():
    truth = [(1.0, 1.2, 0.6), (3.0, 1.2, 0.6)]
    samples = []
    for th, mus, g in truth:
        total, haze = fit.simulate_slab(th, mus, g, 1.58, 0.0, 150000, 5)
        samples.append({"thickness_mm": th, "total_transmission": total})
    res = fit.fit_slab(samples, "pc", 20000)
    assert res["g"] == pytest.approx(0.6)
    assert res["mus"] == pytest.approx(1.2, rel=0.12)
    assert res["material"]["type"] == "constant"
    assert all(abs(p["total_transmission"] - p["measured_total"]) < 0.02 for p in res["predictions"])


def test_fit_rejects_bad_input():
    with pytest.raises(ValueError):
        fit.fit_slab([], "pc")
    with pytest.raises(ValueError):
        fit.fit_slab([{"thickness_mm": 1.0, "total_transmission": 1.5}], "pc")
