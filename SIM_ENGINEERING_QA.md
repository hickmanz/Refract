# LightSim: answers for simulation engineering (working draft)

Temporary notes to answer review questions about the light-pipe simulations. Numbers quoted are from the
latest run of **rev14** (`2026-11-16Lightpipe-asm-rev14.stp`, run `90682ce3bc2b`, 20 M rays per LED channel,
GPU solver) unless stated otherwise. Items marked **TODO** need input before this goes out.

---

## 0. Simulation method at a glance

LightSim is a **spectral, non-sequential Monte Carlo ray tracer** run directly on the STEP assembly. Millions
of rays are launched from each LED. Each ray carries one wavelength and a share of that LED's flux, and is
followed through the real CAD geometry, bounce by bounce, until it is absorbed, leaves the model, or exits
through the exit face. The light arriving at the exit face is binned into a map, which gives luminance,
uniformity, colour and efficiency. Everything that doesn't arrive is tallied by where it was lost, so the
energy budget always closes to 100 %. The tool is built for **comparing designs (relative accuracy)**;
absolute numbers need a check against one measured part.

**The path of one ray:**

1. **Geometry.** The STEP assembly is triangulated (rev14: about 297,000 triangles). Each part is given a
   material (it transmits light, with an index, absorption and scattering) or an opaque finish (it only
   reflects or absorbs). Faces can override their part's finish.
2. **Launch.** A ray starts on an LED's emitting face with a position, direction and wavelength drawn from
   that LED channel's model (section 3).
3. **Find the next surface.** The ray is intersected with the mesh (Embree on the CPU, NVIDIA Warp on the GPU).
4. **Travel through a medium.** Inside the light pipe, the ray loses energy to bulk absorption
   (Beer-Lambert) and may scatter off TiO2 particles before reaching the surface. Scattering strength and
   angle come from Mie theory (section 5).
5. **Interact with the surface.**
   - **Opaque part:** the ray is absorbed, or reflected diffusely (Lambertian) or specularly (optionally
     rough), according to the finish's reflectances.
   - **Plastic/air boundary:** the ray reflects or refracts by Fresnel's equations, including total internal
     reflection, at a microfacet normal set by the surface roughness (section 2).
6. **Repeat** steps 3–5. Very weak rays are culled by an unbiased "Russian roulette", and a ray is stopped
   after 500 surface hits.
7. **Score.**
   - **At the exit face:** the transmitted part is binned into the map pixel it crosses, with its colour
     (CIE XYZ) and its angle to the face normal. Light inside the 22° viewing cone gives luminance.
   - **Everywhere else:** absorbed light is tallied per part, and light that leaves the model is tallied as
     "escaped".
8. **Combine.** Each colour channel of each LED (R, G, B × 5 LEDs = 15) is traced separately. Light transport
   is linear, so every state (White, Yellow, LED n only) is a weighted sum of the channel results, with no
   re-tracing.

---

## 1. "I assume this is a comparative analysis between the 2 geometries."

Yes, that is the intended use. Every revision is run with the same setup (materials, finishes, LED model,
exit face, viewing cone) and the same random seed, so the difference between two runs comes from the
geometry change plus Monte Carlo noise. See section 6 for how much of a difference is real.

**TODO:** name the two geometries being compared (for example `SP1-lightpipe-design` against
`2026-11-16Lightpipe-asm-rev14`) and the run IDs used.

---

## 2. "Are you accounting for surface finish on the parts?"

Yes. Every part has a finish, and individual faces can override it. There are two kinds.

**Opaque finishes** (housing, PCB, reflector, LED packages) have a diffuse reflectance `Rd`, a specular
reflectance `Rs` and a roughness `sigma` for the specular lobe. Whatever is not reflected is absorbed.
- Diffuse reflection is ideal Lambertian (cosine-weighted).
- Specular reflection is a mirror reflection about a microfacet normal drawn from a Beckmann slope
  distribution with RMS slope `sigma` (0 means a perfect mirror).
- A ray picks diffuse or specular with probability `Rd : Rs`.

**Dielectric finishes** (the light pipe faces) set only the surface roughness; the optics come from the
refractive indices on both sides. At each hit the tracer draws a Beckmann microfacet normal (RMS slope
`sigma`), computes the unpolarised Fresnel reflectance at that normal (with TIR), and either reflects or
refracts.
- `polished`: sigma 0 (ideal optical surface).
- `texture_fine` / `texture_medium` / `texture_coarse`: sigma 0.06 / 0.15 / 0.30.

**What is not modelled:**
- Measured BRDF/BSDF data. Finishes are parametric, so a mould-texture spec (VDI 3400, MT-xxxx) has to be
  mapped to a `sigma` by judgement.
- Masking and shadowing of the microfacets (the Smith term). Rough dielectric surfaces are an
  approximation at grazing angles.
- Polarisation across bounces (each Fresnel event uses the unpolarised average), interference, diffraction.
- Sub-surface light in white plastics. A white reflector is a surface Lambertian reflector, not a scattering
  volume.

**Finishes actually applied in rev14** (after all setup rules are merged, later rules overriding earlier):

| Part | What it is | Finish | Parameters |
|---|---|---|---|
| M0002511-C4 Body 1 | Light pipe (traced side) | `polished`, **exit face 28 also `polished`** | sigma 0 |
| T11C2-HV-PCBA Body 2 | Reflector (traced side) | `white_diffuse` | Rd 0.90 |
| T11C2-HV-PCBA Body 1 | PCB | `satin_metal` | Rs 0.80, sigma 0.15 |
| T11C2-HV-PCBA Body 3 | Reflector (other side) | `grey_diffuse` | Rd 0.50 |
| T11C2-HV-PCBA Body 4–8 | LED packages, traced side | `led_package` | Rd 0.60 |
| T11C2-HV-PCBA Body 9–13 | LED packages, other side | `black` | R 0 |
| M0002492-C3 Body 1 | Housing | `plastic_yellow` | Rd 0.58 (spectral, yellow) |
| M0002492-C3 Body 2, 3 | Housing inserts | `plastic_dark_grey` | Rd 0.15 |
| M0002511-C4 Body 2 | Light pipe (other side) | `black` | R 0 (not traced as optics) |

> **Open items to fix or confirm before sharing:**
> - The setup contains an exit-face rule `texture_fine`, but a later rule sets face 28 back to `polished`.
>   That override has applied since about rev6, so **those runs model a polished exit face.**
> - The reflector has a `white_pet` (R 0.95) rule, overridden by a later `white_diffuse` (R 0.90) rule.
> - The PCB has a `white_pet` rule, overridden by `satin_metal` (specular R 0.80). Is that what the board
>   really is? A white solder mask is closer to diffuse 0.75–0.85; a green one to 0.3–0.5.
> - The `grey_diffuse` rules for `Body 9`–`Body 13` don't match any part (the pattern needs the full name),
>   so those packages are black. Only one side is lit, so this has almost no effect.

---

## 3. "What type of LED model are you using? Do we know the LED type/manufacturer?"

**Part:** Lite-On **LTST-E183CEGB2W**, an SMD RGB LED with an embedded driver IC (datasheet DS35-2021-0062).
Five are placed (LED 1–5) at a pitch of about 9.93 mm, each emitting from the top face of its CAD package
body (+y, away from the PCB).

**The model:** a "datasheet model", not a measured near-field ray file.

| Channel | Flux used | Spectrum | Angular pattern |
|---|---|---|---|
| Red (AlInGaP) | 1.79 lm | digitised from datasheet Fig. 1, peak about 635 nm | Lambertian (cos^1) |
| Green (InGaN) | 3.30 lm | digitised, peak about 520 nm | Lambertian |
| Blue (InGaN) | 0.80 lm | digitised, peak about 467 nm | Lambertian |

That is 5.89 lm per LED and 29.45 lm for five LEDs on full white.

**How the emitter is modelled:**
- **Emitting surface:** the top face of each LED package body in the CAD (about 2.1 mm², facing away from the
  PCB). The package itself is an opaque part (diffuse R 0.60), so light that comes back to the LED is partly
  reflected and partly absorbed, as on a real package.
- **Launch position:** uniform over that face, weighted by area. That is equivalent to a uniformly bright
  (spatially flat) emitter covering the whole package top.
- **Launch direction:** the polar angle from the face normal is drawn from the channel's intensity pattern
  (here cos^1, Lambertian, on a 0.25° grid). The azimuth is uniform, so the pattern is rotationally symmetric.
- **Wavelength:** drawn per ray from the channel's spectrum (5 nm table, sub-bin jitter), so colour and
  dispersion come from the same rays.
- **Flux:** every ray starts with equal weight, and the tallies are scaled by the channel's flux, converted
  from lumens to watts with that spectrum's luminous efficacy. The drive level of a state then scales each
  channel (0–1).
- **Independence:** R, G and B share the same emitting face but are traced as separate sources. Each ray's
  random stream depends only on the run seed and the ray number, so a run is reproducible.
- **Alternatives the tool supports but this study doesn't use:** a rectangle or disc emitter of a given
  size, a measured intensity table (for example from an IES/LDT file), or a measured near-field ray file
  (`x y z l m n flux [wavelength]`, for example exported from TM-25 or Zemax).

**How the flux was derived:** the datasheet gives only a min–max intensity range per colour (R 340–800,
G 600–1500, B 150–360 mcd). The model uses the middle of each range (570 / 1050 / 255 mcd) and converts it
with flux = pi x I, which holds for a Lambertian emitter. The datasheet's 120° viewing angle matches cos^1.

**Simplifications to state up front:**
- Absolute flux is a mid-bin estimate. Real parts vary about ±40 % per channel with binning. This scales
  absolute luminance but not uniformity or a relative comparison.
- Rays start uniformly over the whole top face of the package in the CAD (about 2.1 mm²), not from the three
  individual dies. In a real part the R, G and B dies sit side by side under a diffusing encapsulant, so
  near-field colour separation is under-represented.
- The pattern is an ideal cos^1. A measured ray file (TM-25, or a vendor ray set exported to text) can be
  loaded instead. The tool supports this; we don't have one for this part.

---

## 4. "Can you clarify which design slides 4–9 is showing?"

**TODO:** I need the deck to answer this. For each slide, record the revision file, the run, and the state shown:

| Slide | Revision / STEP file | Run ID | State (White / Yellow / LED n only) | Map view (luminance cone / exitance) |
|---|---|---|---|---|
| 4 | | | | |
| 5 | | | | |
| 6 | | | | |
| 7 | | | | |
| 8 | | | | |
| 9 | | | | |

Revisions available in the project: rev1–rev14, `2026-11-16Lightpipe-asm-chamf-reflect`,
`SP1-lightpipe-design`, `craazyidea`.

---

## 5. "What are you assuming as the optical properties of the plastic and the internal materials (PCB, reflector, etc.)?"

### Light pipe: PC + 0.01 wt% TiO2 (`pc_tio2_0p01`)

| Property | Value | Source |
|---|---|---|
| Refractive index | Cauchy A = 1.5609, B = 0.0087 µm² (n about 1.59 at 530 nm) | typical optical PC |
| Bulk absorption | 0.0012 /mm at 530 nm (about 0.12 % per mm), rising to 0.12 /mm at 380 nm | typical clear PC curve |
| Scattering | Mie theory, rutile TiO2 spheres in PC, median diameter 0.3 µm, lognormal spread 1.3, ideal dispersion (efficiency 1.0) | computed |

What that scattering works out to (Henyey-Greenstein parameters the tracer uses):

| Loading | λ | Scattering coeff. µs | Anisotropy g | Mean free path | Transport mean free path 1/(µs(1−g)) |
|---|---|---|---|---|---|
| 0.01 wt% (current) | 460 nm | 0.37 /mm | 0.48 | 2.7 mm | 5.2 mm |
| 0.01 wt% (current) | 530 nm | 0.45 /mm | 0.58 | 2.2 mm | 5.3 mm |
| 0.01 wt% (current) | 630 nm | 0.47 /mm | 0.62 | 2.1 mm | 5.6 mm |
| 0.03 wt% | 530 nm | 1.34 /mm | 0.58 | 0.75 mm | 1.8 mm |
| 0.1 wt% | 530 nm | 4.46 /mm | 0.58 | 0.22 mm | 0.53 mm |

The pipe is about 4 mm deep from the input face to the exit face. With a transport mean free path of about
5 mm, the current loading only partly randomises the light's direction before it exits.

**Caveat:** Mie theory with ideal dispersion is an upper bound. Real compounds agglomerate and scatter less.
The material's dispersion-efficiency factor (typically 0.3–0.7) should be set from one measured plaque or
part (transmission and haze), which the tool can fit.

### Opaque parts

All are surface models as in section 2. Values are library defaults, not measurements of these parts.

| Part | Assumed | Basis |
|---|---|---|
| Reflector | diffuse R 0.90 | generic white plastic. **TODO:** actual resin and colour? |
| PCB | specular R 0.80, sigma 0.15 | **TODO:** confirm (see section 2) |
| LED package top / sides | diffuse R 0.60 | generic package / solder mask |
| Housing (yellow) | diffuse, spectral yellow, R about 0.58 | colour-matched estimate |
| Housing inserts (dark grey) | diffuse R 0.15 | estimate |

The largest absolute uncertainties are reflector reflectance, real diffuser scattering, and LED flux binning.

---

## 6. "Do you have an energy conservation plot anywhere? If yes, can you share it."

Yes, in two forms.

**1. Per-ray accounting check (numerical conservation).** Every ray's weight ends in exactly one bucket:
reached the exit face, absorbed in a body's volume, absorbed at a body's surface, escaped to infinity,
leaked through a mesh hole, hit the bounce limit, a medium-tracking error, or Russian-roulette net noise.
After every run the tool checks that these buckets sum to the emitted flux for every channel and stores the
worst deviation as `maxAccountingError`. For rev14 it is **2.1 × 10⁻⁸**, which is float rounding.

**2. Energy budget ("Where the light goes", Results page → LossBar).** For the rev14 run, White state,
29.45 lm emitted:

| Where the emitted light ends up | Share of emitted radiant flux |
|---|---|
| **Exits through the exit face** | **17.3 %** |
| Absorbed by the housing (yellow, M0002492-C3 Body 1) | 29.3 % |
| Absorbed by the reflector (T11C2-HV-PCBA Body 2) | 20.9 % |
| Absorbed by the PCB (Body 1) | 15.1 % |
| Absorbed by the LED packages (Bodies 4–8) | 6.8 % |
| Escaped through other surfaces / openings | 9.1 % |
| Absorbed in the light pipe volume | 0.9 % |
| Other (second pipe, inserts, other reflector) | 0.5 % |
| Leaked through mesh holes + medium-tracking errors | 0.005 % |
| **Sum** | **100.0 %** |

Notes for the reader:
- The summary "efficiency" (17.5 %) is luminous (lm at exit / lm emitted). The budget is radiant (W), so the
  two differ slightly because of spectral weighting.
- "Escaped" is light that left the model to infinity through openings, not light lost to a modelling error.
  Modelling errors are the leaked and medium-tracking lines, about 0.005 %.
- **TODO:** screenshot the LossBar from the Results page for the deck, or export this table.

---

## 7. "Are we interpreting correlation as causation?"

Inside the model, a difference between two runs is caused by what changed between them, because a simulation
is a controlled experiment: same LED model, materials, finishes, exit face, cone and seed, with only the CAD
different. Three things can still make a difference misleading.

**1. Monte Carlo noise.** The rev14 map noise is 5.6 % per pixel (relative standard error). Pixel statistics
such as min/max are dominated by noise and by dark edge or corner pixels. Compare designs on
noise-robust metrics: the CV, the peak-to-valley of the profile averaged across the width, and total
efficiency. Treat a difference smaller than a few times the noise as no difference. Both runs use seed 1,
which reduces the noise of the difference somewhat, but rays decorrelate once the geometry differs.

**2. Several things change at once.** A CAD revision often moves more than one feature, and setup rules
have shifted between some runs (section 2), which confounds them. To attribute an effect to a feature, change
only that feature, or use a setup-only test such as setting one surface to black or to a mirror and
re-running. That is a true counterfactual inside the model.

**3. The light-path analysis is bookkeeping, not a counterfactual.** "Surfaces → Last surface" says what
fraction of the exit light last touched a surface. It doesn't say what would happen if that surface changed.
A surface can be the last bounce for much of the light and still make little difference to the result,
because the light would have arrived by another route. The "What to change" tab is labelled as a
first-order estimate for this reason.

**Model versus reality:** a correct comparison inside the model is only as good as the assumptions in
sections 2, 3 and 5. The relative ranking of designs is usually robust to those assumptions. Absolute
luminance and efficiency are not, and should be validated against one measured part. The README says the same.

---

## Short version

- **Method:** a spectral, non-sequential Monte Carlo ray trace of the STEP assembly. It's built for
  comparing designs; absolute values need one measured part.
- **Comparison:** same setup and seed for every revision, so the geometry is the only variable (plus noise).
- **Surface finish:** yes. Opaque parts have diffuse and specular reflectance with roughness. Pipe faces have
  Fresnel reflection, total internal reflection and roughness. There's no measured reflectance data.
- **LED:** Lite-On LTST-E183CEGB2W. The datasheet model has digitised spectra, a Lambertian 120° pattern and
  mid-bin flux (5.89 lm per LED). It emits uniformly from the package top face.
- **Materials:** the pipe is PC (n about 1.59) with 0.01 wt% TiO2, Mie-modelled (it scatters only weakly
  over the 4 mm depth). Reflector diffuse 0.90, PCB satin metal 0.80, LED packages 0.60, housing yellow
  about 0.58. These are library estimates, not measurements.
- **Energy conservation:** the tool checks it on every run (rev14 error 2×10⁻⁸). The budget is 17 % to the
  exit, 29 % housing, 21 % reflector, 15 % PCB, 7 % LEDs, 9 % escaped.
- **Correlation vs causation:** differences between runs are caused by the change inside the model. Watch
  the 5.6 % map noise, revisions that change several things at once, and "last surface" shares, which are
  bookkeeping rather than what-if results.
- **Slides 4–9:** TODO.

---

## Appendix: quick reference

- Rays: 20 M per channel × 15 channels = 300 M rays, about 190 s on the GPU (RTX 5080).
- Bounce limit: 500 surface hits per ray. Volume-scattering events are capped separately at 20,000.
- Russian roulette below 1 % remaining weight, unbiased: the "Roulette noise (net)" bucket averages to zero.
- Wavelength grid: 380–780 nm in 5 nm steps. Colour comes from CIE 1931 colour-matching functions per ray.
- Luminance map: light leaving the exit face inside a 22° half-angle cone around its normal, 800 px along the
  length. The exact area of each pixel is computed from the exit triangles, so only ray noise remains.
