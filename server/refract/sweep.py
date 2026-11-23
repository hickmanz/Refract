"""Parameter sweeps: one simulation run per value of a design parameter, on the same revision and setup.

A sweep never changes the project's setup. Each run gets its own copy of the setup and the library in which the
swept part has a private variant of its material, so results stay reproducible and comparable afterwards.

Supported parameters (all act on the material of one transparent part):

* ``tio2_wt_pct``      TiO2 filler loading in weight percent (Mie model; a clear material becomes PC + TiO2)
* ``diameter_um``      TiO2 particle diameter in micrometres (Mie model)
* ``mus``              grey scattering coefficient in 1/mm (keeps the anisotropy of the material, or 0.6)
"""

from __future__ import annotations

import copy
import re

from .engine.scene import name_matches, resolve_part
from .materials import mie

PARAMETERS = {
    "tio2_wt_pct": {"label": "TiO2 loading", "unit": "wt %", "min": 0.0, "max": 60.0},
    "diameter_um": {"label": "TiO2 particle diameter", "unit": "um", "min": 0.05, "max": 5.0},
    "mus": {"label": "Scattering coefficient", "unit": "1/mm", "min": 0.0, "max": 500.0},
}
MAX_VALUES = 24


def _fmt(v: float) -> str:
    return f"{v:g}"


def build_variants(setup: dict, library: dict, instances: list[dict], spec: dict, min_values: int = 2) -> list[dict]:
    """-> [{value, label, setup, library}] for a sweep spec ``{parameter, part, values}``."""
    param = spec.get("parameter")
    if param not in PARAMETERS:
        raise ValueError(f"unknown sweep parameter {param!r}")
    info = PARAMETERS[param]
    pattern = (spec.get("part") or "").strip()
    if not pattern:
        raise ValueError("choose the part to sweep")
    try:
        values = [float(v) for v in spec.get("values", [])]
    except (TypeError, ValueError):
        raise ValueError("the sweep values must be numbers") from None
    if not min_values <= len(values) <= MAX_VALUES:
        raise ValueError(f"a sweep needs between {min_values} and {MAX_VALUES} values")
    for v in values:
        if not info["min"] <= v <= info["max"]:
            raise ValueError(f"{info['label']} must be between {info['min']:g} and {info['max']:g} {info['unit']}")

    rules = setup.get("parts", [])
    matched = [i for i in instances if name_matches(pattern, i)]
    if len(matched) != 1:
        raise ValueError(f"the part pattern {pattern!r} matches {len(matched)} parts (it must match exactly one)")
    inst = matched[0]
    eff = resolve_part(inst, rules)
    if not eff["enabled"] or not eff["material"]:
        raise ValueError(f"{inst['path']} has no material. Assign the diffuser material to it first.")
    base_id = eff["material"]
    base = library["materials"].get(base_id)
    if base is None:
        raise ValueError(f"material {base_id!r} is not in the library")
    if base.get("mode") == "surface":
        raise ValueError(f"{inst['path']} has an opaque surface material, which has no volume to sweep. Assign a diffuser (volume) material.")

    out = []
    for k, v in enumerate(values, start=1):
        doc = copy.deepcopy(base)
        new_id = f"{re.sub(r'__sweep.*$', '', base_id)}__sweep{k}"
        doc["id"] = new_id
        doc["builtin"] = False
        sc = dict(doc.get("scattering") or {"type": "none"})
        if param in ("tio2_wt_pct", "diameter_um"):
            if sc.get("type") != "mie":
                sc = {
                    "type": "mie",
                    "tio2_wt_pct": 0.0,
                    "diameter_um": mie.DEFAULT_DIAMETER_UM,
                    "sigma_g": mie.DEFAULT_SIGMA_G,
                    "host": "pc",
                    "efficiency": mie.DEFAULT_EFFICIENCY,
                }
            sc["tio2_wt_pct" if param == "tio2_wt_pct" else "diameter_um"] = v
        else:  # mus
            g = float(sc.get("g", 0.6)) if sc.get("type") == "constant" else 0.6
            sc = {"type": "constant", "mus": v, "g": g}
        doc["scattering"] = sc
        label = f"{info['label']} {_fmt(v)} {info['unit']}"
        doc["name"] = f"{base.get('name', base_id)} ({label})"
        lib2 = copy.deepcopy(library)
        lib2["materials"][new_id] = doc
        setup2 = copy.deepcopy(setup)
        setup2["parts"] = list(setup2.get("parts", [])) + [{"match": pattern, "material": new_id}]
        out.append({"value": v, "label": label, "setup": setup2, "library": lib2})
    return out
