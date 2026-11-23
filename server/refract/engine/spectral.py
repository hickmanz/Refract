"""Spectral helpers: wavelength grid, CIE 1931 colour matching functions, LED spectrum templates, colour maths."""

from __future__ import annotations

from functools import lru_cache

import numpy as np

LAM0 = 380.0
DLAM = 5.0
NL = 81  # 380 .. 780 nm in 5 nm steps
LAM = LAM0 + DLAM * np.arange(NL)
KM = 683.0  # lm/W at 555 nm


def _g(lam: np.ndarray, mu: float, s1: float, s2: float) -> np.ndarray:
    s = np.where(lam < mu, s1, s2)
    return np.exp(-0.5 * ((lam - mu) / s) ** 2)


def cie_xyz_bar(lam: np.ndarray) -> np.ndarray:
    """CIE 1931 2-degree colour matching functions (Wyman, Sloan, Shirley 2013 multi-lobe fit), shape (..., 3)."""
    lam = np.asarray(lam, dtype=np.float64)
    x = 1.056 * _g(lam, 599.8, 37.9, 31.0) + 0.362 * _g(lam, 442.0, 16.0, 26.7) - 0.065 * _g(lam, 501.1, 20.4, 26.2)
    y = 0.821 * _g(lam, 568.8, 46.9, 40.5) + 0.286 * _g(lam, 530.9, 16.3, 31.1)
    z = 1.217 * _g(lam, 437.0, 11.8, 36.0) + 0.681 * _g(lam, 459.0, 26.0, 13.8)
    return np.stack([x, y, z], axis=-1)


CMF = cie_xyz_bar(LAM)  # (NL, 3), used by the kernels (linear interpolation on the grid)


def v_lambda(lam: np.ndarray) -> np.ndarray:
    return cie_xyz_bar(lam)[..., 1]


def spectrum_on_grid(spec: dict | None) -> np.ndarray:
    """Relative spectral power (NL,) normalised to unit sum, from a spectrum description.

    Supported ``type``: ``gaussian`` (peak, fwhm), ``phosphor_white`` (cct), ``table`` (lambda, values).
    """
    if spec is None:
        spec = {"type": "phosphor_white", "cct": 5000}
    kind = spec.get("type", "gaussian")
    if kind == "gaussian":
        peak = float(spec.get("peak", 525.0))
        fwhm = max(float(spec.get("fwhm", 30.0)), 1.0)
        s = np.exp(-0.5 * ((LAM - peak) / (fwhm / 2.3548)) ** 2)
    elif kind == "phosphor_white":
        s = white_led_spectrum(float(spec.get("cct", 5000.0)))
    elif kind == "table":
        lam = np.asarray(spec["lambda"], dtype=np.float64)
        val = np.asarray(spec["values"], dtype=np.float64)
        order = np.argsort(lam)
        s = np.interp(LAM, lam[order], val[order], left=0.0, right=0.0)
    else:
        raise ValueError(f"unknown spectrum type {kind!r}")
    s = np.clip(s, 0.0, None)
    total = s.sum()
    if total <= 0:
        raise ValueError("spectrum has no power inside 380-780 nm")
    return s / total


def xyz_of_spectrum(s: np.ndarray) -> np.ndarray:
    return (s[:, None] * CMF).sum(axis=0)


def xy_of_xyz(xyz: np.ndarray) -> np.ndarray:
    xyz = np.asarray(xyz, dtype=np.float64)
    tot = xyz.sum(axis=-1, keepdims=True)
    tot = np.where(tot <= 0, 1.0, tot)
    return xyz[..., :2] / tot


def uv_of_xyz(xyz: np.ndarray) -> np.ndarray:
    """CIE 1976 u', v'."""
    xyz = np.asarray(xyz, dtype=np.float64)
    d = xyz[..., 0] + 15.0 * xyz[..., 1] + 3.0 * xyz[..., 2]
    d = np.where(d <= 0, 1.0, d)
    return np.stack([4.0 * xyz[..., 0] / d, 9.0 * xyz[..., 1] / d], axis=-1)


def cct_mccamy(x: float, y: float) -> float:
    n = (x - 0.3320) / (0.1858 - y)
    return 449.0 * n**3 + 3525.0 * n**2 + 6823.3 * n + 5520.33


def white_led_spectrum(cct: float) -> np.ndarray:
    """Blue pump + broad phosphor emission, tuned (bisection on the blue fraction) to a correlated colour temperature."""
    cct = float(np.clip(cct, 2200.0, 12000.0))
    peak = float(np.clip(555.0 + (6500.0 - cct) / 3800.0 * 45.0, 545.0, 610.0))
    blue = np.exp(-0.5 * ((LAM - 450.0) / (20.0 / 2.3548)) ** 2)
    phos = np.exp(-0.5 * ((LAM - peak) / (115.0 / 2.3548)) ** 2) * np.where(LAM < peak, 0.85, 1.25)
    blue /= blue.sum()
    phos /= phos.sum()

    def cct_of(f: float) -> float:
        xyz = xyz_of_spectrum(f * blue + (1 - f) * phos)
        x, y = xy_of_xyz(xyz)
        return cct_mccamy(float(x), float(y))

    lo, hi = 0.005, 0.95  # more blue = higher CCT
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        if cct_of(mid) < cct:
            lo = mid
        else:
            hi = mid
    f = 0.5 * (lo + hi)
    return f * blue + (1 - f) * phos


def luminous_efficacy(s: np.ndarray) -> float:
    """lm per radiant watt of a normalised spectrum."""
    return KM * float((s * CMF[:, 1]).sum())


_M_XYZ_TO_SRGB = np.array(
    [[3.2406, -1.5372, -0.4986], [-0.9689, 1.8758, 0.0415], [0.0557, -0.2040, 1.0570]]
)


def xyz_to_srgb(xyz: np.ndarray, white_y: float | None = None) -> np.ndarray:
    """XYZ -> display sRGB in 0..1. ``white_y`` scales so that this luminance maps to 1 (exposure)."""
    xyz = np.asarray(xyz, dtype=np.float64)
    if white_y:
        xyz = xyz / white_y
    lin = xyz @ _M_XYZ_TO_SRGB.T
    # desaturate negative components instead of clipping hard, then clip highlights
    mn = np.minimum(lin.min(axis=-1, keepdims=True), 0.0)
    lin = lin - mn
    lin = np.clip(lin, 0.0, 1.0)
    return np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.power(lin, 1 / 2.4) - 0.055)


def interp_table(curve_lam: np.ndarray, curve_val: np.ndarray) -> np.ndarray:
    """Interpolate a user table onto the 5 nm grid (clamped at the ends)."""
    order = np.argsort(curve_lam)
    return np.interp(LAM, np.asarray(curve_lam, float)[order], np.asarray(curve_val, float)[order])


# -- surface colour -> spectral reflectance -------------------------------------------------------
def _illuminant() -> np.ndarray:
    """Daylight-like white (6504 K blackbody) used to turn a colour into a reflectance spectrum and back."""
    lam = LAM * 1e-9
    b = 1.0 / lam**5 / np.expm1(1.4388e-2 / (lam * 6504.0))
    return b / b.max()


_ILL = _illuminant()
_WHITE_RGB = _M_XYZ_TO_SRGB @ (_ILL[:, None] * CMF).sum(axis=0)
_ILL_Y = float((_ILL * CMF[:, 1]).sum())


def reflectance_to_linear_rgb(r: np.ndarray) -> np.ndarray:
    """Linear sRGB seen under daylight white for a reflectance spectrum on the grid (a flat 1.0 gives (1, 1, 1))."""
    xyz = ((np.asarray(r, float) * _ILL)[:, None] * CMF).sum(axis=0)
    return (_M_XYZ_TO_SRGB @ xyz) / _WHITE_RGB


def reflectance_luminance(r: np.ndarray) -> float:
    return float((np.asarray(r, float) * _ILL * CMF[:, 1]).sum() / _ILL_Y)


def srgb_hex_to_linear(color: str) -> np.ndarray:
    s = str(color).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ValueError(f"colour {color!r} must look like #rrggbb")
    try:
        v = np.array([int(s[i : i + 2], 16) for i in (0, 2, 4)], float) / 255.0
    except ValueError as exc:
        raise ValueError(f"colour {color!r} must look like #rrggbb") from exc
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


@lru_cache(maxsize=512)
def _sigmoid_fit(rgb: tuple[float, float, float]) -> tuple[float, ...]:
    from scipy.optimize import least_squares

    target = np.clip(np.array(rgb), 0.004, 0.996)
    u = (LAM - 580.0) / 200.0

    def refl(c: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-(c[0] * u * u + c[1] * u + c[2])))

    best, best_err = None, 1e9
    mean = float(np.clip(target.mean(), 0.02, 0.98))
    base = float(np.log(mean / (1 - mean)))
    for c0 in (0.0, 8.0, -8.0):
        for c1 in (0.0, 8.0, -8.0):
            sol = least_squares(lambda c: reflectance_to_linear_rgb(refl(c)) - target, [c0, c1, base], method="lm")
            err = float(np.abs(sol.fun).max())
            if err < best_err:
                best, best_err = sol.x, err
            if best_err < 2e-3:
                break
        if best_err < 2e-3:
            break
    return tuple(float(x) for x in best)


def reflectance_from_srgb(color: str) -> np.ndarray:
    """A smooth reflectance spectrum (0..1) on the grid whose colour under daylight white matches the sRGB colour."""
    lin = srgb_hex_to_linear(color)
    if float(lin.max() - lin.min()) < 1e-3:  # grey, white or black
        return np.full(NL, float(lin.mean()))
    c = np.array(_sigmoid_fit(tuple(round(float(x), 5) for x in lin)))
    u = (LAM - 580.0) / 200.0
    return 1.0 / (1.0 + np.exp(-(c[0] * u * u + c[1] * u + c[2])))
