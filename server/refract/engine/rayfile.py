"""Measured or vendor ray sets as LED sources.

Accepted input is a plain text ray list, one ray per line::

    x  y  z  l  m  n  flux  [wavelength_nm]

Position in millimetres in the LED's own frame, direction cosines (l, m, n) with +z the emission axis, relative
flux (any unit) and an optional wavelength. Values may be separated by spaces, tabs, commas or semicolons; lines
starting with ``#`` or ``//`` and lines that do not begin with a number (headers) are skipped. Six columns
(no flux) mean equal flux per ray.

This is a generic reader. Vendor formats such as IES/LDT are intensity tables, not ray sets, and binary or
XML ray files (TM-25 and Zemax .DAT/.SRC) must be exported to this text layout first; the importer says so when
it cannot read a file instead of guessing.

Stored as ``rays/<sha1>.npy`` (float32, N x 8, columns x y z l m n flux wavelength with NaN for unknown wavelength)
in the data folder, so library entries and run snapshots only carry the file id.
"""

from __future__ import annotations

import hashlib
import re

import numpy as np

from .. import store
from . import spectral

MAX_RAYS = 2_000_000
_SPLIT = re.compile(r"[\s,;]+")


def parse_text(text: str) -> np.ndarray:
    rows: list[list[float]] = []
    width = None
    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        toks = [t for t in _SPLIT.split(line) if t]
        try:
            vals = [float(t) for t in toks]
        except ValueError:
            if not rows:  # header line before the data
                continue
            raise ValueError(f"line {ln}: cannot read numbers from {line[:60]!r}")
        if len(vals) not in (6, 7, 8):
            if not rows:
                continue
            raise ValueError(f"line {ln}: expected 6 to 8 values (x y z l m n [flux [wavelength]]), found {len(vals)}")
        if width is None:
            width = len(vals)
        elif len(vals) != width:
            raise ValueError(f"line {ln}: {len(vals)} values but earlier lines had {width}")
        rows.append(vals)
        if len(rows) > MAX_RAYS:
            raise ValueError(f"The ray file has more than {MAX_RAYS:,} rays. Please thin it out first.")
    if not rows:
        raise ValueError(
            "No rays found. Expected a text list with one ray per line: x y z l m n flux [wavelength_nm]. "
            "Binary or XML ray files need to be exported to this text layout first."
        )
    a = np.asarray(rows, dtype=np.float64)
    n = a.shape[0]
    out = np.full((n, 8), np.nan)
    out[:, :6] = a[:, :6]
    out[:, 6] = a[:, 6] if a.shape[1] >= 7 else 1.0
    if a.shape[1] >= 8:
        out[:, 7] = a[:, 7]
    if not np.all(np.isfinite(out[:, :7])):
        raise ValueError("The ray file contains values that are not finite numbers")
    d = out[:, 3:6]
    norm = np.linalg.norm(d, axis=1)
    keep = (norm > 1e-9) & (out[:, 6] > 0)
    if keep.sum() < 100:
        raise ValueError(f"Only {int(keep.sum())} usable rays (needs a direction and positive flux). At least 100 are needed.")
    out = out[keep]
    out[:, 3:6] /= norm[keep][:, None]
    if a.shape[1] >= 8:
        lam = out[:, 7]
        if np.nanmin(lam) < 100 or np.nanmax(lam) > 5000:
            raise ValueError("Wavelengths must be in nanometres (between 100 and 5000)")
    return out.astype(np.float32)


def save(rays: np.ndarray) -> str:
    sha = hashlib.sha1(np.ascontiguousarray(rays).tobytes()).hexdigest()[:20]
    d = store.data_dir() / "rays"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{sha}.npy"
    if not path.exists():
        np.save(path, rays)
    return sha


def load(file_id: str) -> np.ndarray:
    if not file_id:
        raise ValueError("A channel uses a measured ray set but no ray file has been chosen")
    if not re.fullmatch(r"[0-9a-f]{8,40}", str(file_id)):
        raise ValueError("invalid ray file id")
    path = store.data_dir() / "rays" / f"{file_id}.npy"
    if not path.exists():
        raise ValueError(f"The ray file {file_id} is missing from the data folder. Import it again in the LED library.")
    return np.load(path)


def summarise(rays: np.ndarray) -> dict:
    """Facts shown to the user after an import: counts, extent, angular profile, spectrum if present."""
    w = rays[:, 6].astype(np.float64)
    nz = rays[:, 5].astype(np.float64)
    theta = np.degrees(np.arccos(np.clip(nz, -1, 1)))
    bins = np.arange(0, 181, 5.0)
    flux, _ = np.histogram(theta, bins=bins, weights=w)
    omega = 2 * np.pi * (np.cos(np.radians(bins[:-1])) - np.cos(np.radians(bins[1:])))
    inten = flux / np.maximum(omega, 1e-9)
    inten = inten / inten.max() if inten.max() > 0 else inten
    pos = rays[:, :3].astype(np.float64)
    has_lam = bool(np.isfinite(rays[:, 7]).all())
    out = {
        "count": int(len(rays)),
        "totalFlux": float(w.sum()),
        "extentMm": [float(np.ptp(pos[:, i])) for i in range(3)],
        "backwardFraction": float(w[nz < 0].sum() / w.sum()),
        "hasWavelength": has_lam,
        "angles": ((bins[:-1] + bins[1:]) / 2).tolist(),
        "intensity": inten.tolist(),
    }
    if has_lam:
        idx = np.clip(np.round((rays[:, 7].astype(np.float64) - spectral.LAM0) / spectral.DLAM).astype(int), 0, spectral.NL - 1)
        s = np.bincount(idx, weights=w, minlength=spectral.NL)
        out["spectrum"] = {"type": "table", "lambda": spectral.LAM.tolist(), "values": (s / s.sum()).tolist()}
    return out


def import_text(text: str) -> dict:
    rays = parse_text(text)
    info = summarise(rays)
    info["file"] = save(rays)
    return info
