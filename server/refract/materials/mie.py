"""Mie scattering of TiO2 filler particles in a polymer host -> scattering coefficient and anisotropy.

The filler is modelled as spheres (rutile TiO2, orientation-averaged refractive index) with an optional lognormal
size distribution, embedded in a non-absorbing host. Returns the Henyey-Greenstein parameters the tracer uses:

    mu_s(lambda) [1/mm]   scattering coefficient
    g(lambda)             anisotropy (mean cosine of the scattering angle)
"""

from __future__ import annotations

import numpy as np

from ..engine import spectral

RHO_TIO2 = 4.23  # g/cm^3 (rutile)
RHO_HOSTS = {"pc": 1.20, "pmma": 1.18, "silicone": 1.05, "epoxy": 1.15}

# Defaults for a realistic dispersion, shared by the library presets, sweeps, the editor and the calculator. Ideal
# monodisperse 0.25 um spheres sit at the peak of scattering per gram and overestimate a real compound; a median size
# of 0.3 um with a geometric spread of 1.3 is closer to a dispersed pigment (still an ideal-dispersion estimate:
# agglomerates scatter less, which the ``efficiency`` factor of the scattering block can account for).
DEFAULT_DIAMETER_UM = 0.3
DEFAULT_SIGMA_G = 1.3
DEFAULT_EFFICIENCY = 1.0


def rutile_index(lam_nm: np.ndarray) -> np.ndarray:
    """Orientation averaged refractive index of rutile (DeVore 1951 ordinary and extraordinary dispersion)."""
    l2 = (np.asarray(lam_nm, dtype=np.float64) / 1000.0) ** 2
    no = np.sqrt(5.913 + 0.2441 / (l2 - 0.0803))
    ne = np.sqrt(7.197 + 0.3322 / (l2 - 0.0843))
    return (2.0 * no + ne) / 3.0


def host_index(host: str | dict | float, lam_nm: np.ndarray) -> np.ndarray:
    from .. import library as lib

    lam = np.asarray(lam_nm, dtype=np.float64)
    if isinstance(host, (int, float)):
        return np.full_like(lam, float(host))
    if isinstance(host, dict):
        curve = lib.eval_curve(host)
        return np.interp(lam, spectral.LAM, curve)
    doc = lib.BUILTIN_MATERIALS.get({"pc": "pc_clear", "pmma": "pmma_clear", "silicone": "silicone", "epoxy": "epoxy"}.get(host, host))
    if doc is None:
        raise ValueError(f"unknown host material {host!r}")
    return np.interp(lam, spectral.LAM, lib.eval_curve(doc["n"]))


def volume_fraction(wt_pct: float, host: str | dict | float = "pc") -> float:
    """TiO2 volume fraction from the weight percent of filler in the compound."""
    rho_h = RHO_HOSTS.get(host, 1.2) if isinstance(host, str) else 1.2
    w = float(wt_pct) / 100.0
    if not 0.0 <= w < 0.9:
        raise ValueError("TiO2 weight percent must be between 0 and 90")
    vp = w / RHO_TIO2
    vh = (1.0 - w) / rho_h
    return vp / (vp + vh)


def _size_distribution(d_um: float, sigma_g: float, n: int = 9) -> tuple[np.ndarray, np.ndarray]:
    """Diameters and number weights of a lognormal distribution with median d_um and geometric std sigma_g."""
    if sigma_g <= 1.0001 or n <= 1:
        return np.array([d_um]), np.array([1.0])
    s = np.log(sigma_g)
    z = np.linspace(-2.5, 2.5, n)
    w = np.exp(-0.5 * z**2)
    return d_um * np.exp(s * z), w / w.sum()


def scattering_curves(
    wt_pct: float,
    diameter_um: float,
    host: str | dict | float = "pc",
    sigma_g: float = 1.0,
    dependent_scattering: bool = True,
    efficiency: float = DEFAULT_EFFICIENCY,
) -> dict[str, np.ndarray]:
    """mu_s, g, mu_s' (reduced) and Qsca on the 5 nm grid.

    ``efficiency`` scales mu_s (not g): 1 is an ideal dispersion of spheres, less than 1 stands for agglomeration and
    other losses of scattering power per gram of filler, found by calibrating against one real measurement.
    """
    import miepython

    if diameter_um <= 0:
        raise ValueError("particle diameter must be > 0")
    if not 0.0 <= float(efficiency) <= 10.0:
        raise ValueError("dispersion efficiency must be between 0 and 10")
    lam = spectral.LAM
    nh = host_index(host, lam)
    npart = rutile_index(lam)
    phi = volume_fraction(wt_pct, host)
    diam, wgt = _size_distribution(float(diameter_um), float(sigma_g))
    vol_mean = float((wgt * np.pi * diam**3 / 6.0).sum())  # um^3
    n_dens = phi / vol_mean if vol_mean > 0 else 0.0  # particles / um^3
    mus = np.zeros(len(lam))
    g = np.zeros(len(lam))
    qs = np.zeros(len(lam))
    for k, l_nm in enumerate(lam):
        sig_tot = 0.0
        g_acc = 0.0
        q_acc = 0.0
        for dk, wk in zip(diam, wgt):
            qext, qsca, qback, gk = miepython.efficiencies(complex(npart[k], 0.0), float(dk), float(l_nm) / 1000.0, float(nh[k]))
            area = np.pi * (dk / 2.0) ** 2
            sig = float(qsca) * area
            sig_tot += wk * sig
            g_acc += wk * sig * float(gk)
            q_acc += wk * float(qsca)
        mus[k] = n_dens * sig_tot * 1000.0  # 1/um -> 1/mm
        g[k] = g_acc / sig_tot if sig_tot > 0 else 0.0
        qs[k] = q_acc
    if dependent_scattering:
        # crowding reduces the scattering of each particle; Percus-Yevick-like factor, small below 1 vol%
        mus = mus * (1.0 - phi) ** 2 / (1.0 + 2.0 * phi) ** 1
    mus = mus * float(efficiency)
    return {"lambda": lam.copy(), "mus": mus, "g": g, "mus_reduced": mus * (1.0 - g), "qsca": qs, "volume_fraction": np.array([phi])}


def scattering_from_params(sc: dict) -> tuple[np.ndarray, np.ndarray]:
    """Resolve a ``{"type": "mie", ...}`` scattering block of a library material to (mu_s, g)."""
    eff = sc.get("efficiency")
    cur = scattering_curves(
        wt_pct=float(sc["tio2_wt_pct"]),
        diameter_um=float(sc.get("diameter_um", DEFAULT_DIAMETER_UM)),
        host=sc.get("host", "pc"),
        sigma_g=float(sc.get("sigma_g", DEFAULT_SIGMA_G)),
        efficiency=DEFAULT_EFFICIENCY if eff in (None, "") else float(eff),
    )
    return cur["mus"], cur["g"]
