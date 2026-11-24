"""Material, finish and LED library: built-in entries (read-only) plus user entries stored in ``library.json``.

Everything is plain JSON so the UI can edit it. "Curves" describe a quantity that depends on wavelength::

    1.49                                                  constant
    {"type": "constant", "value": 0.002}
    {"type": "cauchy", "A": 1.56, "B": 0.0087, "C": 0}    n = A + B/l^2 + C/l^4   (l in micrometre)
    {"type": "sellmeier", "B": [..], "C": [..]}           n^2 = 1 + sum B_i l^2 / (l^2 - C_i)
    {"type": "table", "lambda": [..nm..], "values": [..]}
    {"type": "power", "value": 1.0, "lambda0": 550, "exponent": -1}
"""

from __future__ import annotations

import copy
from typing import Any

import numpy as np

from . import store
from .engine import spectral
from .materials import mie as _mie

Curve = Any


# -- curves --------------------------------------------------------------------------------
def eval_curve(c: Curve, default: float = 0.0) -> np.ndarray:
    """Evaluate a curve on the 5 nm wavelength grid -> (NL,) float64."""
    lam = spectral.LAM
    if c is None:
        return np.full(spectral.NL, float(default))
    if isinstance(c, (int, float)):
        return np.full(spectral.NL, float(c))
    kind = c.get("type", "constant")
    if kind == "constant":
        return np.full(spectral.NL, float(c.get("value", default)))
    if kind == "cauchy":
        l_um = lam / 1000.0
        return float(c["A"]) + float(c.get("B", 0.0)) / l_um**2 + float(c.get("C", 0.0)) / l_um**4
    if kind == "sellmeier":
        l2 = (lam / 1000.0) ** 2
        n2 = np.ones_like(l2)
        for b, cc in zip(c["B"], c["C"]):
            n2 = n2 + float(b) * l2 / (l2 - float(cc))
        return np.sqrt(np.clip(n2, 1.0, None))
    if kind == "table":
        return spectral.interp_table(np.asarray(c["lambda"], float), np.asarray(c["values"], float))
    if kind == "power":
        return float(c.get("value", 1.0)) * (lam / float(c.get("lambda0", 550.0))) ** float(c.get("exponent", -1.0))
    raise ValueError(f"unknown curve type {kind!r}")


# -- built-ins ---------------------------------------------------------------------------------
_PC_ABS = {
    "type": "table",
    "lambda": [380, 400, 420, 450, 500, 550, 600, 700, 780],
    "values": [0.12, 0.02, 0.008, 0.0035, 0.0015, 0.001, 0.0008, 0.0008, 0.001],
}
_PMMA_ABS = {
    "type": "table",
    "lambda": [380, 400, 450, 500, 550, 600, 700, 780],
    "values": [0.004, 0.0015, 0.0006, 0.0004, 0.0003, 0.0003, 0.0004, 0.0006],
}


def _diffuser(id_: str, name: str, wt: float, d_um: float = _mie.DEFAULT_DIAMETER_UM) -> dict:
    return {
        "id": id_,
        "name": name,
        "builtin": True,
        "n": {"type": "cauchy", "A": 1.5609, "B": 0.0087},
        "absorption": _PC_ABS,
        "scattering": {
            "type": "mie",
            "tio2_wt_pct": wt,
            "diameter_um": d_um,
            "sigma_g": _mie.DEFAULT_SIGMA_G,
            "host": "pc",
            "efficiency": _mie.DEFAULT_EFFICIENCY,
        },
        "notes": (
            "PC with rutile TiO2 filler: scattering from Mie theory for an ideally dispersed filler (spheres, size spread "
            f"{_mie.DEFAULT_SIGMA_G:g}). Real compounds usually scatter somewhat less (agglomeration): lower the "
            "dispersion efficiency after comparing with one measured part."
        ),
    }


def _surface(id_: str, name: str, rdiff: float, rspec: float, sigma: float, color: str | None = None, notes: str = "") -> dict:
    """An opaque *surface* material: the part reflects (diffuse + gloss) or absorbs and nothing enters it.

    ``sigma`` is the default roughness of the gloss lobe; a texture finish on the part overrides it. When a ``color`` is given,
    ``rdiff`` is the luminance of that colour (the same convention as the opaque finishes).
    """
    doc: dict = {"id": id_, "name": name, "builtin": True, "mode": "surface", "rdiff": rdiff, "rspec": rspec, "sigma": sigma}
    if color:
        doc["color"] = color
    if notes:
        doc["notes"] = notes
    return doc


_PLASTIC_NOTE = (
    "Opaque shortcut: light only reflects at the surface, nothing passes through the wall. Fine for walls that are thick enough "
    "(check the transmission of a translucent PC + TiO2 material at the wall thickness: below about 1 % this is safe). "
    "Gloss 0.04 is the Fresnel reflectance of a plastic (n about 1.5); a texture finish (VDI, MT) sets how sharp that gloss is."
)

BUILTIN_MATERIALS: dict[str, dict] = {
    m["id"]: m
    for m in [
        {
            "id": "pc_clear",
            "name": "Polycarbonate (clear)",
            "builtin": True,
            "n": {"type": "cauchy", "A": 1.5609, "B": 0.0087},
            "absorption": _PC_ABS,
            "scattering": {"type": "none"},
        },
        {
            "id": "pmma_clear",
            "name": "PMMA (clear)",
            "builtin": True,
            "n": {"type": "cauchy", "A": 1.4777, "B": 0.0042},
            "absorption": _PMMA_ABS,
            "scattering": {"type": "none"},
        },
        {
            "id": "silicone",
            "name": "Silicone (optical)",
            "builtin": True,
            "n": {"type": "cauchy", "A": 1.40, "B": 0.003},
            "absorption": 0.0005,
            "scattering": {"type": "none"},
        },
        {
            "id": "epoxy",
            "name": "Epoxy (optical)",
            "builtin": True,
            "n": {"type": "cauchy", "A": 1.51, "B": 0.004},
            "absorption": 0.002,
            "scattering": {"type": "none"},
        },
        {
            "id": "glass_bk7",
            "name": "Glass (N-BK7)",
            "builtin": True,
            "n": {
                "type": "sellmeier",
                "B": [1.03961212, 0.231792344, 1.01046945],
                "C": [0.00600069867, 0.0200179144, 103.560653],
            },
            "absorption": 0.0001,
            "scattering": {"type": "none"},
        },
        _diffuser("pc_tio2_0p001", "PC + 0.001 wt% TiO2 (trace, long light pipes)", 0.001),
        _diffuser("pc_tio2_0p003", "PC + 0.003 wt% TiO2 (very light diffusion)", 0.003),
        _diffuser("pc_tio2_0p01", "PC + 0.01 wt% TiO2 (light diffusion)", 0.01),
        _diffuser("pc_tio2_0p03", "PC + 0.03 wt% TiO2", 0.03),
        _diffuser("pc_tio2_0p1", "PC + 0.1 wt% TiO2", 0.1),
        _diffuser("pc_tio2_0p3", "PC + 0.3 wt% TiO2 (strong diffusion)", 0.3),
        # white translucent walls: a volume, so thin sections still leak some light (thick ones go opaque by themselves). Slower to trace
        # because of the many scattering events.
        _diffuser("pc_tio2_1p0", "PC + 1 wt% TiO2 (white, translucent thin walls)", 1.0),
        _diffuser("pc_tio2_3p0", "PC + 3 wt% TiO2 (white, nearly opaque)", 3.0),
        # opaque surface materials (mode "surface")
        _surface(
            "pcb_green_mask", "PCB, green solder mask", 0.12, 0.05, 0.04, "#1f6f3a",
            "Estimate: flat green LPI solder mask, diffuse luminance about 0.12 (0.12 to 0.20 depending on copper underneath), semi-gloss. "
            "Not measured: compare with one board and adjust.",
        ),
        _surface(
            "pcb_white_mask", "PCB, white solder mask (reflector)", 0.85, 0.05, 0.04, None,
            "Estimate: white LPI solder mask, diffuse about 0.85 (aged or thin mask 0.75 to 0.85), semi-gloss.",
        ),
        _surface(
            "pcb_white_silkscreen", "PCB, white silkscreen print (reflector)", 0.80, 0.03, 0.15, None,
            "Estimate: white legend ink printed over the board, matte, diffuse about 0.80. For a partly printed board use an area "
            "weighted value, for example green mask 0.12 with 30 % silkscreen at 0.80 gives about 0.32 (duplicate this entry and set Rd).",
        ),
        _surface("opaque_white", "Opaque plastic, white (ABS / PC)", 0.85, 0.04, 0.03, None, _PLASTIC_NOTE),
        _surface("opaque_light_grey", "Opaque plastic, light grey", 0.55, 0.04, 0.03, None, _PLASTIC_NOTE),
        _surface("opaque_dark_grey", "Opaque plastic, dark grey", 0.15, 0.04, 0.03, "#6b6b6b", _PLASTIC_NOTE),
        _surface("opaque_black", "Opaque plastic, black", 0.04, 0.04, 0.03, None, _PLASTIC_NOTE),
        _surface("opaque_yellow", "Opaque plastic / paint, yellow", 0.58, 0.04, 0.03, "#f2c200", _PLASTIC_NOTE),
        _surface("opaque_orange", "Opaque plastic / paint, orange", 0.32, 0.04, 0.03, "#f07800", _PLASTIC_NOTE),
        _surface("opaque_red", "Opaque plastic / paint, red", 0.16, 0.04, 0.03, "#e00000", _PLASTIC_NOTE),
        _surface("opaque_green", "Opaque plastic / paint, green", 0.20, 0.04, 0.03, "#2e8b57", _PLASTIC_NOTE),
        _surface("opaque_blue", "Opaque plastic / paint, blue", 0.11, 0.04, 0.03, "#1050d0", _PLASTIC_NOTE),
    ]
}


def is_surface_material(doc: dict | None) -> bool:
    """True for an opaque surface material (colour + reflectances), False for a volume material (index, absorption, scattering)."""
    return bool(doc) and doc.get("mode") == "surface"


def _texture(id_: str, name: str, sigma: float, notes: str = "") -> dict:
    return {"id": id_, "name": name, "type": "texture", "sigma": sigma, **({"notes": notes} if notes else {})}


def _sigma_from_ra(ra_um: float) -> float:
    """Rule of thumb for the RMS slope of a mould texture from its Ra (um): sigma = 0.12 sqrt(Ra / 1 um) (VDI 21 -> 0.13). A judgement, not a measurement."""
    return round(0.12 * float(np.sqrt(ra_um)), 3)


_TEXTURE_NOTE = (
    "Roughness mapped from Ra with sigma = 0.12 x sqrt(Ra / 1 um): a judgement, not a measurement. Calibrate against a gloss-meter "
    "reading of a moulded sample when it matters."
)


def _vdi(n: int) -> dict:
    ra = 0.4 * 10 ** ((n - 12) / 20.0)  # VDI 3400: Ra doubles every 6 steps (12 = 0.40 um)
    return _texture(f"vdi_{n}", f"VDI {n} (Ra {ra:.2g} um, sigma {_sigma_from_ra(ra):g})", _sigma_from_ra(ra), _TEXTURE_NOTE)


# Mold-Tech sand/blast grades, approximate Ra from published conversion tables (MT is a pattern library without an official scale)
_MT = [("MT-11000", 0.4), ("MT-11010", 3.0), ("MT-11020", 4.5), ("MT-11030", 6.3), ("MT-11040", 10.0)]

BUILTIN_FINISHES: dict[str, dict] = {
    f["id"]: {**f, "builtin": True}
    for f in [
        {"id": "polished", "name": "Polished (optical)", "type": "dielectric", "sigma": 0.0},
        {"id": "texture_fine", "name": "Texture fine (sigma 0.06)", "type": "dielectric", "sigma": 0.06},
        {"id": "texture_medium", "name": "Texture medium (sigma 0.15)", "type": "dielectric", "sigma": 0.15},
        {"id": "texture_coarse", "name": "Texture coarse (sigma 0.30)", "type": "dielectric", "sigma": 0.30},
        # texture finishes (roughness only): apply to a volume part (roughens the refracting surface) and to a surface material
        # (roughens its gloss lobe); "as material" keeps the material's own roughness
        {
            "id": "as_material", "name": "As material (no texture override)", "type": "texture", "sigma": 0.0, "inherit": True,
            "notes": "Volume parts: polished. Surface materials: the roughness defined in the material.",
        },
        *[_vdi(n) for n in (12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45)],
        *[
            _texture(
                f"mt_{name.lower().replace('mt-', '')}", f"{name} (Ra about {ra:g} um, sigma {_sigma_from_ra(ra):g})", _sigma_from_ra(ra),
                "Mold-Tech grade, approximate (a pattern library without an official roughness scale): " + _TEXTURE_NOTE,
            )
            for name, ra in _MT
        ],
        {"id": "white_diffuse", "name": "White diffuse (R 0.90)", "type": "opaque", "rdiff": 0.90, "rspec": 0.0, "sigma": 0.0},
        {"id": "white_pet", "name": "White PET reflector (R 0.95)", "type": "opaque", "rdiff": 0.95, "rspec": 0.0, "sigma": 0.0},
        {"id": "grey_diffuse", "name": "Grey diffuse (R 0.50)", "type": "opaque", "rdiff": 0.50, "rspec": 0.0, "sigma": 0.0},
        {"id": "mirror", "name": "Mirror / metallised (R 0.92)", "type": "opaque", "rdiff": 0.0, "rspec": 0.92, "sigma": 0.0},
        {"id": "satin_metal", "name": "Satin metal (R 0.80, sigma 0.15)", "type": "opaque", "rdiff": 0.0, "rspec": 0.80, "sigma": 0.15},
        {"id": "led_package", "name": "LED package / PCB solder mask (R 0.60)", "type": "opaque", "rdiff": 0.60, "rspec": 0.0, "sigma": 0.0},
        {"id": "black", "name": "Black absorber", "type": "opaque", "rdiff": 0.0, "rspec": 0.0, "sigma": 0.0},
        # coloured housings (rdiff = the colour's luminance)
        {"id": "plastic_yellow", "name": "Yellow plastic / paint", "type": "opaque", "rdiff": 0.58, "rspec": 0.0, "sigma": 0.0, "color": "#f2c200"},
        {"id": "plastic_orange", "name": "Orange plastic / paint", "type": "opaque", "rdiff": 0.32, "rspec": 0.0, "sigma": 0.0, "color": "#f07800"},
        {"id": "plastic_red", "name": "Red plastic / paint", "type": "opaque", "rdiff": 0.16, "rspec": 0.0, "sigma": 0.0, "color": "#e00000"},
        {"id": "plastic_green", "name": "Green plastic / paint", "type": "opaque", "rdiff": 0.20, "rspec": 0.0, "sigma": 0.0, "color": "#2e8b57"},
        {"id": "plastic_blue", "name": "Blue plastic / paint", "type": "opaque", "rdiff": 0.11, "rspec": 0.0, "sigma": 0.0, "color": "#1050d0"},
        {"id": "plastic_dark_grey", "name": "Dark grey plastic", "type": "opaque", "rdiff": 0.15, "rspec": 0.0, "sigma": 0.0, "color": "#6b6b6b"},
    ]
}


def _lambertian_pattern(m: float = 1.0) -> dict:
    return {"type": "lambertian", "exponent": m}


BUILTIN_LEDS: dict[str, dict] = {
    l["id"]: {**l, "builtin": True}
    for l in [
        {
            "id": "white_generic",
            "name": "Generic white LED, 120 deg, 5000 K",
            "channels": [
                {
                    "id": "W",
                    "name": "White",
                    "flux_lm": 10.0,
                    "spectrum": {"type": "phosphor_white", "cct": 5000},
                    "pattern": _lambertian_pattern(1.0),
                }
            ],
            "emitter": {"shape": "rect", "size": [1.6, 1.6]},
        },
        {
            "id": "white_warm_generic",
            "name": "Generic white LED, 120 deg, 2700 K",
            "channels": [
                {
                    "id": "W",
                    "name": "Warm white",
                    "flux_lm": 10.0,
                    "spectrum": {"type": "phosphor_white", "cct": 2700},
                    "pattern": _lambertian_pattern(1.0),
                }
            ],
            "emitter": {"shape": "rect", "size": [1.6, 1.6]},
        },
        {
            "id": "rgb_generic",
            "name": "Generic RGB LED, 120 deg",
            "channels": [
                {"id": "R", "name": "Red", "flux_lm": 5.0, "spectrum": {"type": "gaussian", "peak": 628, "fwhm": 18}, "pattern": _lambertian_pattern(1.0)},
                {"id": "G", "name": "Green", "flux_lm": 12.0, "spectrum": {"type": "gaussian", "peak": 525, "fwhm": 34}, "pattern": _lambertian_pattern(1.0)},
                {"id": "B", "name": "Blue", "flux_lm": 2.0, "spectrum": {"type": "gaussian", "peak": 460, "fwhm": 24}, "pattern": _lambertian_pattern(1.0)},
            ],
            "emitter": {"shape": "rect", "size": [2.0, 2.0]},
        },
        {
            "id": "amber_generic",
            "name": "Generic amber LED, 120 deg",
            "channels": [
                {"id": "A", "name": "Amber", "flux_lm": 6.0, "spectrum": {"type": "gaussian", "peak": 592, "fwhm": 16}, "pattern": _lambertian_pattern(1.0)}
            ],
            "emitter": {"shape": "rect", "size": [1.6, 1.6]},
        },
        {
            "id": "white_narrow_generic",
            "name": "Generic white LED, narrow (cos^5), 5000 K",
            "channels": [
                {
                    "id": "W",
                    "name": "White",
                    "flux_lm": 10.0,
                    "spectrum": {"type": "phosphor_white", "cct": 5000},
                    "pattern": _lambertian_pattern(5.0),
                }
            ],
            "emitter": {"shape": "rect", "size": [1.6, 1.6]},
        },
        {
            "id": "liteon_ltst_e183cegb2w",
            "name": "Lite-On LTST-E183CEGB2W RGB (1.85 x 1.8 mm, diffused, 120 deg)",
            "channels": [
                {
                    "id": "R",
                    "name": "Red (AlInGaP)",
                    "flux_lm": 1.79,
                    "spectrum": {
                        "type": "table",
                        "lambda": [585, 590, 595, 600, 605, 610, 615, 620, 625, 630, 635, 640, 645, 650, 655, 660, 665, 670],
                        "values": [0.0, 0.002, 0.008, 0.02, 0.04, 0.075, 0.143, 0.266, 0.461, 0.736, 0.993, 0.685, 0.249, 0.095, 0.034, 0.011, 0.002, 0.0],
                    },
                    "pattern": _lambertian_pattern(1.0),
                },
                {
                    "id": "G",
                    "name": "Green (InGaN)",
                    "flux_lm": 3.30,
                    "spectrum": {
                        "type": "table",
                        "lambda": [455, 460, 465, 470, 475, 480, 485, 490, 495, 500, 505, 510, 515, 520, 525, 530, 535, 540, 545, 550, 555, 560, 565, 570, 575, 580, 585, 590, 595, 600, 605, 610, 615, 620, 625, 630],
                        "values": [0.001, 0.002, 0.004, 0.007, 0.015, 0.03, 0.057, 0.103, 0.18, 0.302, 0.484, 0.716, 0.924, 0.993, 0.896, 0.721, 0.553, 0.42, 0.313, 0.224, 0.158, 0.111, 0.078, 0.056, 0.039, 0.028, 0.02, 0.014, 0.01, 0.007, 0.005, 0.004, 0.003, 0.002, 0.002, 0.001],
                    },
                    "pattern": _lambertian_pattern(1.0),
                },
                {
                    "id": "B",
                    "name": "Blue (InGaN)",
                    "flux_lm": 0.80,
                    "spectrum": {
                        "type": "table",
                        "lambda": [415, 420, 425, 430, 435, 440, 445, 450, 455, 460, 465, 470, 475, 480, 485, 490, 495, 500, 505, 510, 515, 520, 525, 530, 535, 540, 545, 550, 555, 560, 565, 570],
                        "values": [0.001, 0.002, 0.004, 0.012, 0.028, 0.064, 0.136, 0.26, 0.458, 0.732, 0.958, 0.95, 0.72, 0.491, 0.337, 0.221, 0.14, 0.09, 0.059, 0.038, 0.024, 0.016, 0.011, 0.008, 0.006, 0.005, 0.004, 0.003, 0.002, 0.002, 0.002, 0.001],
                    },
                    "pattern": _lambertian_pattern(1.0),
                },
            ],
            # Diffused flat-top window of the 1.85 x 1.8 x 0.6 mm body (measured off the datasheet outline). The three
            # dies and the driver IC are much smaller and sit under the diffuser; the window is what emits.
            "emitter": {"shape": "rect", "size": [1.55, 1.5]},
            "notes": (
                "Lite-On DS35-2021-0062 (SMD RGB with embedded 8-bit driver IC, 20 mA per channel at full scale). "
                "From the datasheet: package and window size, 120 deg viewing angle, spectra (digitised from Fig. 1: "
                "peaks 635 / 520 / 467 nm, FWHM about 15 / 31 / 23 nm). Estimated: the datasheet gives only min/max "
                "intensity (R 340-800, G 600-1500, B 150-360 mcd), so the middle of each range is used (570 / 1050 / 255 mcd) "
                "and converted with flux = pi x I for a Lambertian pattern (the diffuser makes it close to cos^1). "
                "Real parts vary by about +/-40 % in flux per channel (binning)."
            ),
        },
        {
            "id": "cree_qls6b_fkw_cnsnsf043",
            "name": "Cree LED QLS6B-FKW-CNSNSF043 RGB (PLCC6, 4.7 x 1.3 mm, 120 deg)",
            "channels": [
                {
                    "id": "R",
                    "name": "Red (14 mA)",
                    "flux_lm": 1.74,
                    "spectrum": {"type": "gaussian", "peak": 627, "fwhm": 24},
                    "pattern": _lambertian_pattern(1.0),
                },
                {
                    "id": "G",
                    "name": "Green (12 mA)",
                    "flux_lm": 3.65,
                    "spectrum": {"type": "gaussian", "peak": 521, "fwhm": 38},
                    "pattern": _lambertian_pattern(1.0),
                },
                {
                    "id": "B",
                    "name": "Blue (16 mA)",
                    "flux_lm": 0.43,
                    "spectrum": {"type": "gaussian", "peak": 460, "fwhm": 28},
                    "pattern": _lambertian_pattern(1.0),
                },
            ],
            # Cavity opening of the PLCC6 body (4.45 x 1.17 mm in the outline drawing); the three dies sit inside it.
            # The long side is the LED's x axis.
            "emitter": {"shape": "rect", "size": [4.45, 1.17]},
            "notes": (
                "Cree LED CLD-CT1397 rev 8 (PLCC6 3-in-1, 4.7 x 1.3 x 1.5 mm). Drive level 1.0 = the datasheet test currents "
                "(R 14 mA / G 12 mA / B 16 mA; absolute maximum 30 / 20 / 20 mA). From the datasheet: package and cavity size, "
                "viewing angle (far-field pattern is close to cos^1, 120 deg), spectral bandwidth (FWHM 24 / 38 / 28 nm) and the "
                "peak wavelengths read off the spectrum plot (627 / 521 / 460 nm; the kit allows dominant wavelengths of "
                "619-624 / 520-540 / 460-480 nm). Estimated: the datasheet gives one luminous intensity range for the three "
                "dies lit together (900-2800 mcd, bin NS), not per colour. The middle of the range (1850 mcd, flux = pi x I for "
                "a Lambertian pattern, 5.8 lm) is split over the channels so that the mix lands on the centre of the white colour "
                "bins 5A-8D (x 0.28, y 0.25), which gives about 30 / 63 / 7 % of the flux in R / G / B. Real parts vary a lot "
                "(the kit spans a 3x intensity range and +/-0.02 in colour coordinates): measure or bin-select one lot before "
                "trusting absolute levels."
            ),
        },
        {
            "id": "liteon_ltst_c19hrgyw",
            "name": "Lite-On LTST-C19HRGYW red / green / yellow (1.6 x 1.6 x 0.35 mm, diffused, 130 deg)",
            "channels": [
                {
                    "id": "R",
                    "name": "Red (AlInGaP)",
                    "flux_lm": 0.313,
                    "spectrum": {"type": "gaussian", "peak": 632, "fwhm": 17},
                    "pattern": {"type": "half_angle", "half_angle_deg": 65.0},
                },
                {
                    "id": "G",
                    "name": "Green (InGaN)",
                    "flux_lm": 0.622,
                    "spectrum": {"type": "gaussian", "peak": 520, "fwhm": 35},
                    "pattern": {"type": "half_angle", "half_angle_deg": 65.0},
                },
                {
                    "id": "Y",
                    "name": "Yellow (AlInGaP)",
                    "flux_lm": 0.491,
                    "spectrum": {"type": "gaussian", "peak": 595, "fwhm": 16},
                    "pattern": {"type": "half_angle", "half_angle_deg": 65.0},
                },
            ],
            # The package is 1.6 x 1.6 mm with a white diffused lens. The outline drawing is not available as text, so the lit
            # window is an estimate a little smaller than the body.
            "emitter": {"shape": "rect", "size": [1.5, 1.5]},
            "notes": (
                "Lite-On DS22-2014-0387 (SMD tri-colour LED, 1.6 x 1.6 x 0.35 mm, white diffused lens, 20 mA per channel at full scale; "
                "absolute maximum DC 30 / 20 / 30 mA for red / green / yellow). From the datasheet: viewing angle 2 theta 1/2 = 130 deg "
                "(a cos^m pattern with half-intensity angle 65 deg, m about 0.80), peak wavelengths 632 / 520 / 595 nm and spectral "
                "half-widths 17 / 35 / 16 nm (modelled as Gaussians). Estimated: the datasheet gives only min/max intensity at 20 mA "
                "(R 45-180, G 71-450, Y 71-280 mcd), so the geometric middle of each range, sqrt(min x max), is used (90.0 / 178.7 / 141.0 mcd; the bins are roughly "
                "logarithmic, so this sits below the arithmetic middle) and converted "
                "with flux = 2 pi I / (m + 1) = 3.48 x I for that pattern. Real parts vary by about +/-40 % in flux per channel (binning "
                "codes P to T), so measure or bin-select a lot before trusting absolute levels. Also estimated: the size of the lit "
                "window (1.5 x 1.5 mm) as the outline drawing could not be read."
            ),
        },
    ]
}

_BUILTINS = {"materials": BUILTIN_MATERIALS, "finishes": BUILTIN_FINISHES, "leds": BUILTIN_LEDS}
KINDS = tuple(_BUILTINS)


def get_library() -> dict:
    """Built-ins and user entries merged: ``{materials: {id: doc}, finishes: {...}, leds: {...}}``."""
    user = store.load_user_library()
    out: dict[str, dict] = {}
    for kind in KINDS:
        merged = copy.deepcopy(_BUILTINS[kind])
        for id_, doc in user.get(kind, {}).items():
            if id_ not in merged:  # built-ins are read-only
                merged[id_] = {**doc, "id": id_}
        out[kind] = merged
    return out


def validate_entry(kind: str, doc: dict) -> dict:
    """Check a library entry by evaluating it (raises ValueError with a readable message)."""
    if kind not in KINDS:
        raise ValueError(f"unknown library kind {kind!r}")
    if not str(doc.get("name", "")).strip():
        raise ValueError("Please give the entry a name")
    try:
        if kind == "materials":
            if is_surface_material(doc):
                surface_finish_doc(doc)
            else:
                resolve_material(doc)
        elif kind == "finishes":
            resolve_finish(doc)
        else:
            from .engine import sources

            sources.validate_led(doc)
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Invalid entry: missing or malformed field {exc}") from exc
    return doc


def save_entry(kind: str, doc: dict) -> dict:
    validate_entry(kind, doc)
    user = store.load_user_library()
    id_ = str(doc.get("id") or "").strip() or "".join(c if c.isalnum() else "_" for c in doc["name"].lower()).strip("_")
    if id_ in _BUILTINS[kind]:
        id_ = f"{id_}_user"
    clean = {k: v for k, v in doc.items() if k != "builtin"}
    clean["id"] = id_
    user[kind][id_] = clean
    store.save_user_library(user)
    return clean


def delete_entry(kind: str, id_: str) -> bool:
    if kind not in KINDS:
        raise ValueError(f"unknown library kind {kind!r}")
    if id_ in _BUILTINS[kind]:
        raise ValueError("Built-in entries cannot be deleted (duplicate them to edit)")
    user = store.load_user_library()
    if id_ not in user.get(kind, {}):
        return False
    del user[kind][id_]
    store.save_user_library(user)
    return True


# -- resolving to arrays the engine uses ----------------------------------------------------
class Resolved:
    """A material on the wavelength grid."""

    def __init__(self, n: np.ndarray, ka: np.ndarray, mus: np.ndarray, g: np.ndarray) -> None:
        self.n, self.ka, self.mus, self.g = n, ka, mus, g


def resolve_material(doc: dict) -> Resolved:
    if is_surface_material(doc):
        raise ValueError(f"{doc.get('name', 'material')!r} is an opaque surface material: it has no volume properties")
    n = eval_curve(doc.get("n", 1.5))
    if np.any(n < 1.0):
        raise ValueError("refractive index must be >= 1")
    ka = np.clip(eval_curve(doc.get("absorption", 0.0)), 0.0, None)
    sc = doc.get("scattering") or {"type": "none"}
    kind = sc.get("type", "none")
    if kind == "none":
        mus = np.zeros(spectral.NL)
        g = np.zeros(spectral.NL)
    elif kind == "constant":
        mus = np.full(spectral.NL, float(sc.get("mus", 0.0)))
        g = np.full(spectral.NL, float(sc.get("g", 0.0)))
    elif kind == "table":
        mus = eval_curve({"type": "table", "lambda": sc["lambda"], "values": sc["mus"]})
        g = eval_curve({"type": "table", "lambda": sc["lambda"], "values": sc["g"]})
    elif kind == "mie":
        from .materials import mie

        mus, g = mie.scattering_from_params(sc)
    else:
        raise ValueError(f"unknown scattering type {kind!r}")
    mus = np.clip(mus, 0.0, None)
    if np.any(np.abs(g) >= 1.0):
        raise ValueError("anisotropy g must be inside (-1, 1)")
    return Resolved(n, ka, mus, g)


def resolve_finish(doc: dict) -> tuple[int, np.ndarray]:
    """-> (type code, params[4]): type 0 dielectric (params: sigma), 1 opaque (rdiff, rspec, sigma)."""
    kind = doc.get("type", "dielectric")
    sigma = float(doc.get("sigma", 0.0))
    if sigma < 0 or sigma > 1.5:
        raise ValueError("sigma (RMS slope) must be between 0 and 1.5")
    if kind in ("dielectric", "texture"):  # a texture is a roughness only: on a volume part it is a dielectric surface
        return 0, np.array([sigma, 0.0, 0.0, 0.0])
    if kind == "opaque":
        rd, rs = float(doc.get("rdiff", 0.0)), float(doc.get("rspec", 0.0))
        if rd < 0 or rs < 0 or rd + rs > 1.0 + 1e-9:
            raise ValueError("rdiff + rspec must be between 0 and 1")
        if doc.get("color"):
            spectral.srgb_hex_to_linear(doc["color"])  # validates the colour
        return 1, np.array([rd, rs, sigma, 0.0])
    raise ValueError(f"unknown finish type {kind!r}")


def is_texture_finish(doc: dict | None) -> bool:
    """A finish that only carries a roughness (polished, textures, VDI, MT), as opposed to a bundled opaque finish."""
    return bool(doc) and doc.get("type", "dielectric") in ("dielectric", "texture")


def surface_finish_doc(material: dict, finish: dict | None = None) -> dict:
    """Compose an opaque finish document from a surface material and (optionally) a texture finish.

    The reflectances and colour come from the material. The gloss roughness is the material's own ``sigma`` unless the texture
    finish gives one (``inherit`` finishes and bundled opaque finishes leave the material's value alone). The result goes through
    ``resolve_finish`` / ``finish_spectra`` exactly like a stored opaque finish, so the engine needs no new code.
    """
    if not is_surface_material(material):
        raise ValueError("surface_finish_doc needs a surface material")
    sigma = float(material.get("sigma", 0.0))
    if finish is not None and is_texture_finish(finish) and not finish.get("inherit"):
        sigma = float(finish.get("sigma", 0.0))
    doc = {
        "type": "opaque",
        "rdiff": float(material.get("rdiff", 0.0)),
        "rspec": float(material.get("rspec", 0.0)),
        "sigma": sigma,
    }
    if material.get("color"):
        doc["color"] = material["color"]
    if material.get("tint_specular"):
        doc["tint_specular"] = True
    resolve_finish(doc)  # validates reflectances, roughness and colour
    return doc


def finish_spectra(doc: dict) -> np.ndarray:
    """Wavelength dependence of an opaque finish, shape (2, NL): multipliers for the diffuse and the specular part.

    Without a colour both are 1 (a grey surface). With ``color`` the diffuse part gets the shape of a smooth reflectance
    spectrum of that colour, scaled so its luminance is 1: the *level* is still ``rdiff`` (the editor sets ``rdiff`` to the
    colour's luminance when a colour is picked). ``tint_specular`` colours the mirror part too (metals).
    """
    out = np.ones((2, spectral.NL))
    color = doc.get("color")
    if doc.get("type", "dielectric") != "opaque" or not color:
        return out
    r = spectral.reflectance_from_srgb(color)
    y = spectral.reflectance_luminance(r)
    if y < 2e-3:  # black: the colour carries no information
        return out
    shape = r / y
    out[0] = shape
    if doc.get("tint_specular"):
        out[1] = shape
    return out


# -- plain descriptions for display (the results view shows what a run was simulated with) -------------------
def _at_nm(curve: Curve, nm: float = 550.0, default: float = 0.0) -> float:
    """A curve's value at one wavelength (nearest point of the 5 nm grid)."""
    values = eval_curve(curve, default)
    i = int(np.clip(round((nm - spectral.LAM0) / spectral.DLAM), 0, spectral.NL - 1))
    return float(values[i])


def _curve_kind(curve: Curve) -> str:
    return "constant" if curve is None or isinstance(curve, (int, float)) else str(curve.get("type", "constant"))


def describe_material(doc: dict | None) -> dict | None:
    """The properties of a material entry as the results view lists them (numbers at 550 nm for the wavelength dependent ones)."""
    if not doc:
        return None
    out: dict = {"id": doc.get("id"), "name": doc.get("name") or doc.get("id"), "builtin": bool(doc.get("builtin")), "notes": doc.get("notes") or ""}
    if is_surface_material(doc):
        out.update(
            {
                "kind": "surface",
                "rdiff": float(doc.get("rdiff", 0.0)),
                "rspec": float(doc.get("rspec", 0.0)),
                "sigma": float(doc.get("sigma", 0.0)),
                "color": doc.get("color") or None,
                "tintSpecular": bool(doc.get("tint_specular")),
            }
        )
        return out
    sc = doc.get("scattering") or {"type": "none"}
    skind = str(sc.get("type", "none"))
    scatter: dict = {"type": skind}
    try:
        res = resolve_material(doc)
        scatter["mus550"] = float(res.mus[int(round((550.0 - spectral.LAM0) / spectral.DLAM))])
        scatter["g550"] = float(res.g[int(round((550.0 - spectral.LAM0) / spectral.DLAM))])
    except ValueError:  # a malformed entry in an old snapshot: show what is there
        pass
    if skind == "mie":
        scatter.update({k: sc[k] for k in ("tio2_wt_pct", "diameter_um", "sigma_g", "efficiency", "host") if k in sc})
    out.update(
        {
            "kind": "volume",
            "n550": _at_nm(doc.get("n", 1.5), 550.0, 1.5),
            "nCurve": _curve_kind(doc.get("n", 1.5)),
            "absorption550": _at_nm(doc.get("absorption", 0.0), 550.0),  # 1/mm
            "scattering": scatter,
        }
    )
    return out


def describe_finish(doc: dict | None) -> dict | None:
    """The properties of a finish entry as the results view lists them."""
    if not doc:
        return None
    kind = str(doc.get("type", "dielectric"))
    out: dict = {
        "id": doc.get("id"),
        "name": doc.get("name") or doc.get("id"),
        "builtin": bool(doc.get("builtin")),
        "type": kind,
        "sigma": float(doc.get("sigma", 0.0)),
        "notes": doc.get("notes") or "",
    }
    if kind == "opaque":
        out.update({"rdiff": float(doc.get("rdiff", 0.0)), "rspec": float(doc.get("rspec", 0.0)), "color": doc.get("color") or None})
    if doc.get("inherit"):
        out["inherit"] = True
    return out
