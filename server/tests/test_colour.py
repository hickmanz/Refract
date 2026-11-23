import pytest

from refract import library as lib
from refract.engine import spectral


def test_finish_colour_spectrum():
    plain = lib.finish_spectra({"type": "opaque", "rdiff": 0.8})
    assert plain.shape == (2, spectral.NL) and (plain == 1).all()
    doc = {"type": "opaque", "rdiff": 0.58, "color": "#f2c200"}
    sh = lib.finish_spectra(doc)[0]
    lam = spectral.LAM
    assert sh[lam == 450][0] < 0.2 < 1.0 < sh[lam == 650][0]  # yellow: little blue, plenty of red
    assert abs(spectral.reflectance_luminance(sh) - 1.0) < 1e-6
    assert (lib.finish_spectra({**doc, "type": "dielectric"}) == 1).all()  # only opaque finishes are coloured
    assert (lib.finish_spectra({**doc, "tint_specular": True})[1] == sh).all()


def test_colour_round_trip_and_validation():
    for c in ["#f2c200", "#e00000", "#1050d0", "#2e8b57", "#8b4513"]:
        r = spectral.reflectance_from_srgb(c)
        assert 0.0 <= r.min() and r.max() <= 1.0
        assert abs(spectral.reflectance_to_linear_rgb(r) - spectral.srgb_hex_to_linear(c)).max() < 0.01
    with pytest.raises(ValueError):
        lib.validate_entry("finishes", {"name": "bad", "type": "opaque", "rdiff": 0.5, "color": "yellow"})

