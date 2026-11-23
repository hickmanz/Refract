"""Opaque surface materials + texture finishes: composition, legacy parity, face overrides and translucent white plastic."""

import copy

import numpy as np
import pytest

from refract import library as lib
from refract.engine.scene import build_scene
from test_engine_physics import Builder, detector, led, library_with, run


def _scene(setup, library=None, closed=True):
    b = Builder()
    b.add_box("plate", (-60, -60, 10), (60, 60, 13))
    opt, doc = b.finish()
    if not closed:
        doc = copy.deepcopy(doc)
        doc["instances"][0]["closed"] = False
    return build_scene(opt, doc, setup, library or lib.get_library())


def _params(scene, face_tri=0):
    """(type, [rdiff, rspec, sigma]) of the finish of the first triangle (or a given one)."""
    k = int(scene.tri_fin[face_tri])
    return int(scene.fin_type[k]), scene.fin_p[k][:3]


def test_all_builtins_validate():
    library = lib.get_library()
    for kind in ("materials", "finishes"):
        for doc in library[kind].values():
            lib.validate_entry(kind, doc)


def test_vdi_textures_follow_ra_rule():
    fin = lib.get_library()["finishes"]
    assert fin["vdi_21"]["type"] == "texture"
    assert fin["vdi_21"]["sigma"] == pytest.approx(0.127, abs=0.002)  # Ra 1.12 um
    sig = [fin[f"vdi_{n}"]["sigma"] for n in (12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45)]
    assert sig == sorted(sig) and sig[-1] < 0.6
    assert {"mt_11000", "mt_11010", "mt_11020", "mt_11030", "mt_11040"} <= set(fin)


def test_surface_materials_are_flagged_and_have_no_volume():
    mats = lib.get_library()["materials"]
    for key in ("pcb_green_mask", "pcb_white_mask", "pcb_white_silkscreen", "opaque_white", "opaque_black"):
        assert lib.is_surface_material(mats[key])
        with pytest.raises(ValueError):
            lib.resolve_material(mats[key])
    assert not lib.is_surface_material(mats["pc_clear"])  # old documents (no mode) are volume materials
    assert not lib.is_surface_material(mats["pc_tio2_1p0"])


def test_compose_texture_overrides_roughness_but_inherit_and_presets_do_not():
    lg = lib.get_library()
    mat = lg["materials"]["opaque_white"]
    assert lib.surface_finish_doc(mat, lg["finishes"]["vdi_21"])["sigma"] == pytest.approx(lg["finishes"]["vdi_21"]["sigma"])
    assert lib.surface_finish_doc(mat, lg["finishes"]["as_material"])["sigma"] == mat["sigma"]
    assert lib.surface_finish_doc(mat, lg["finishes"]["black"])["sigma"] == mat["sigma"]  # a bundled preset carries no texture
    assert lib.surface_finish_doc(mat)["rdiff"] == mat["rdiff"]


def test_surface_material_plus_texture_in_the_scene():
    setup = {"parts": [{"match": "*", "finish": "black"}, {"match": "plate", "material": "opaque_white", "finish": "vdi_21"}], "leds": [], "detectors": []}
    lg = lib.get_library()
    scene = _scene(setup, lg)
    code, p = _params(scene)
    assert code == 1
    assert p == pytest.approx([0.85, 0.04, lg["finishes"]["vdi_21"]["sigma"]])
    body = scene.bodies[0]
    assert body["transparent"] is False and body["material"] == "opaque_white"
    assert int(scene.body_mat[0]) == 0  # no volume


def test_surface_material_on_open_mesh_has_no_warning_and_default_finish_keeps_material_roughness():
    # the "*" rule's black finish is a bundled preset: the material's own roughness applies
    setup = {"parts": [{"match": "*", "finish": "black"}, {"match": "plate", "material": "pcb_white_silkscreen"}], "leds": [], "detectors": []}
    scene = _scene(setup, closed=False)
    assert not [w for w in scene.warnings if "watertight" in w]
    code, p = _params(scene)
    assert code == 1 and p == pytest.approx([0.80, 0.03, 0.15])


def test_legacy_finishes_are_unchanged():
    lg = lib.get_library()
    setup = {"parts": [{"match": "*", "finish": "black"}, {"match": "plate", "finish": "white_diffuse"}], "leds": [], "detectors": []}
    code, p = _params(_scene(setup, lg))
    ref_code, ref_p = lib.resolve_finish(lg["finishes"]["white_diffuse"])
    assert code == ref_code == 1
    assert p == pytest.approx(ref_p[:3])


def test_face_overrides_with_material_and_texture():
    setup = {
        "parts": [
            {"match": "*", "finish": "black"},
            {"match": "plate", "material": "opaque_white", "finish": "as_material", "faces": {"5": "mat:pcb_green_mask", "4": "vdi_30"}},
        ],
        "leds": [],
        "detectors": [],
    }
    lg = lib.get_library()
    scene = _scene(setup, lg)
    # triangles are in face order: face f has the triangles with tri_face f (two triangles each for a quad)
    b = Builder()
    b.add_box("plate", (-60, -60, 10), (60, 60, 13))
    opt, _ = b.finish()
    by_face = {f: int(np.nonzero(opt["tri_face"] == f)[0][0]) for f in range(6)}
    assert _params(scene, by_face[0])[1] == pytest.approx([0.85, 0.04, 0.03])
    assert _params(scene, by_face[5])[1] == pytest.approx([0.12, 0.05, 0.04])  # painted with the green mask
    assert _params(scene, by_face[4])[1] == pytest.approx([0.85, 0.04, lg["finishes"]["vdi_30"]["sigma"]])  # texture only


def test_bad_surface_override_is_an_error():
    setup = {"parts": [{"match": "*", "finish": "black"}, {"match": "plate", "material": "pc_clear", "finish": "polished", "faces": {"5": "mat:pc_clear"}}], "leds": [], "detectors": []}
    with pytest.raises(ValueError, match="not an opaque surface material"):
        _scene(setup)


def test_photoreal_medium_of_a_clear_material_has_a_nonzero_albedo():
    """Mitsuba rejects a 'regular' spectrum that is zero everywhere: a clear (absorbing only) body must not export albedo 0."""
    from refract.render import mitsuba_export as mx

    setup = {"parts": [{"match": "*", "finish": "black"}, {"match": "plate", "material": "pc_clear", "finish": "polished"}], "leds": [], "detectors": []}
    scene = _scene(setup)
    med = mx._medium(scene, int(scene.body_mat[0]))
    assert med is not None
    values = [float(v) for v in med["albedo"]["values"].split(",")]
    assert min(values) > 0.0


def _slab_transmission(material, thickness):
    b = Builder()
    b.add_box("plate", (-300, -300, 10), (300, 300, 10 + thickness))
    setup = {
        "parts": [{"match": "*", "finish": "black"}, {"match": "plate", "material": material, "finish": "polished" if material.startswith("pc_") else "as_material"}],
        "leds": [led((0, 0, 0))],
        "detectors": [detector("plate", 5, res=8)],
    }
    _, _, t = run(b, setup, library_with(), n=20_000)
    return t.det_pow[0] / t.n_rays


def test_translucent_white_leaks_light_but_the_opaque_shortcut_does_not():
    thin = _slab_transmission("pc_tio2_1p0", 0.5)
    thick = _slab_transmission("pc_tio2_3p0", 3.0)
    opaque = _slab_transmission("opaque_white", 0.5)
    assert thin > 0.01  # a thin white wall passes light
    assert thick < thin / 3  # a thick, heavily loaded wall passes much less
    assert opaque == 0.0  # surface-only: nothing gets through


class _FakeRun:
    """Stands in for a finished run: two exit surfaces, the second four times brighter."""

    scene = {"detectors": [{"cone_deg": 60.0, "view_cos": 1.0}, {"cone_deg": 60.0, "view_cos": 1.0}]}

    def state_ids(self):
        return ["s"]

    def combine(self, state_id):
        return {}

    def detector_maps(self, comb, k, analysis):
        cone = np.full((4, 4, 3), 1.0 if k == 0 else 4.0)
        return {"cone": cone, "area": np.ones((4, 4))}


def test_hidden_exit_surfaces_are_dark_and_leave_the_exposure(monkeypatch):
    from refract import results
    from refract.render import mitsuba_export as mx

    monkeypatch.setattr(results.RunData, "load", classmethod(lambda cls, rid, runid: _FakeRun()))
    maps, peak = mx.exit_maps("r", "x", "s", 0.0)
    assert peak > 0 and maps[1].max() == pytest.approx(1.0) and maps[0].max() == pytest.approx(0.25, rel=0.01)
    maps0, peak0 = mx.exit_maps("r", "x", "s", 0.0, hidden=(1,))
    assert peak0 == pytest.approx(peak / 4, rel=0.01)  # exposure follows the surface that is shown
    assert maps0[0].max() == pytest.approx(1.0) and maps0[1].max() == 0.0
    with pytest.raises(ValueError, match="switched off"):
        mx.exit_maps("r", "x", "s", 0.0, hidden=(0, 1))
    assert mx.hidden_exits({"hidden_exits": [1, 1, 0]}) == (0, 1) and mx.hidden_exits({}) == ()
