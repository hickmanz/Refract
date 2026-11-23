"""Face references that were re-matched on a revision can be stored in the setup."""

from __future__ import annotations

import numpy as np

from refract.engine import faceref


def _scene():
    """One part with three equal quads side by side (faces 0, 1, 2), all facing +Z."""
    verts, tris, tri_face = [], [], []
    for k in range(3):
        x = 10.0 * k
        base = len(verts)
        verts += [[x, 0, 0], [x + 1, 0, 0], [x + 1, 1, 0], [x, 1, 0]]
        tris += [[base, base + 1, base + 2], [base, base + 2, base + 3]]
        tri_face += [k, k]
    opt = {"verts": np.array(verts, dtype=np.float32), "tris": np.array(tris, dtype=np.int32), "tri_face": np.array(tri_face, dtype=np.int32)}
    inst = {"id": "i0", "ordinal": 0, "path": "Root/Part", "name": "Part", "partName": "Part", "triOffset": 0, "triCount": len(tris), "faceCount": 3}
    return opt, inst, {"instances": [inst], "diagonal": 100.0}


def test_accept_rematches_rewrites_index_and_signature():
    opt, inst, doc = _scene()
    sig1 = faceref.face_signature(opt, inst, 1)
    setup = {
        "parts": [{"match": "Part", "faces": {"2": "mirror"}, "face_sigs": {"2": sig1}}],
        "leds": [{"id": "l1", "name": "LED 1", "placement": {"type": "face", "part": "Part", "face": 2, "sig": sig1}}],
        "detectors": [{"id": "d1", "name": "Exit", "faces": [{"part": "Part", "face": 2, "sig": sig1}]}],
        "analysisGroups": [{"id": "g1", "name": "G", "faces": [{"part": "Part", "face": 2, "sig": sig1}]}],
    }
    warnings: list[str] = []
    assert faceref.resolve_face(opt, inst, 2, sig1, 100.0, "x", warnings) == 1 and warnings  # the warning that used to repeat forever

    new, changes = faceref.accept_rematches(setup, opt, doc)
    assert len(changes) == 4 and all("face 2 is now face 1" in c for c in changes)
    assert new["leds"][0]["placement"]["face"] == 1
    assert new["detectors"][0]["faces"][0]["face"] == 1
    assert new["analysisGroups"][0]["faces"][0]["face"] == 1
    assert new["parts"][0]["faces"] == {"1": "mirror"} and list(new["parts"][0]["face_sigs"]) == ["1"]
    assert setup["leds"][0]["placement"]["face"] == 2  # the given setup is untouched

    # nothing left to re-match: no warning and no further change
    warnings.clear()
    assert faceref.resolve_face(opt, inst, 1, new["leds"][0]["placement"]["sig"], 100.0, "x", warnings) == 1 and not warnings
    again, more = faceref.accept_rematches(new, opt, doc)
    assert more == [] and again == new


def test_accept_rematches_skips_ambiguous_part_patterns():
    opt, inst, doc = _scene()
    twin = {**inst, "id": "i1", "ordinal": 1, "path": "Root/Part2", "name": "Part2"}
    doc["instances"] = [inst, twin]
    sig1 = faceref.face_signature(opt, inst, 1)
    setup = {"leds": [{"id": "l1", "name": "Top", "placement": {"type": "face", "part": "Part*", "face": 2, "sig": sig1}}]}
    new, changes = faceref.accept_rematches(setup, opt, doc)
    assert changes == [] and new == setup
