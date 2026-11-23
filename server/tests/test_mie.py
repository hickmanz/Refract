"""TiO2 / Mie diffuser model: shared defaults, the dispersion efficiency factor and the low-loading presets."""

import numpy as np
import pytest

from refract import library as lib
from refract.engine import spectral
from refract.materials import mie


def _i550() -> int:
    return int(np.argmin(np.abs(spectral.LAM - 550.0)))


def test_efficiency_scales_mus_but_not_g():
    ref = mie.scattering_curves(0.01, 0.3, "pc", 1.3)
    half = mie.scattering_curves(0.01, 0.3, "pc", 1.3, efficiency=0.5)
    assert np.allclose(half["mus"], 0.5 * ref["mus"])
    assert np.allclose(half["g"], ref["g"])
    assert np.allclose(half["mus_reduced"], 0.5 * ref["mus_reduced"])
    with pytest.raises(ValueError):
        mie.scattering_curves(0.01, 0.3, "pc", 1.3, efficiency=-1.0)


def test_scattering_block_defaults_and_efficiency():
    base = {"type": "mie", "tio2_wt_pct": 0.03}
    mus, g = mie.scattering_from_params(base)
    ref = mie.scattering_curves(0.03, mie.DEFAULT_DIAMETER_UM, "pc", mie.DEFAULT_SIGMA_G)
    assert np.allclose(mus, ref["mus"]) and np.allclose(g, ref["g"])
    for blank in (None, ""):  # an emptied form field means "ideal"
        assert np.allclose(mie.scattering_from_params({**base, "efficiency": blank})[0], mus)
    assert np.allclose(mie.scattering_from_params({**base, "efficiency": 0.4})[0], 0.4 * mus)


def test_presets_share_the_realistic_defaults_and_cover_low_loadings():
    mats = lib.BUILTIN_MATERIALS
    loads = {}
    for id_, m in mats.items():
        sc = m.get("scattering") or {"type": "none"}  # opaque surface materials have no volume scattering
        if sc["type"] != "mie":
            continue
        assert sc["diameter_um"] == mie.DEFAULT_DIAMETER_UM
        assert sc["sigma_g"] == mie.DEFAULT_SIGMA_G
        loads[id_] = sc["tio2_wt_pct"]
    assert loads["pc_tio2_0p001"] == 0.001 and loads["pc_tio2_0p003"] == 0.003
    # scattering grows with loading, and the new defaults are weaker than the old ideal monodisperse 0.25 um spheres
    mus = {k: lib.resolve_material(mats[k]).mus[_i550()] for k in loads}
    order = sorted(loads, key=loads.get)
    assert all(mus[a] < mus[b] for a, b in zip(order, order[1:]))
    ideal = mie.scattering_curves(0.01, 0.25, "pc", 1.0)["mus"][_i550()]
    assert mus["pc_tio2_0p01"] < ideal
