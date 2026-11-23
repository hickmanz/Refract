# Refract

A local desktop tool for simulating LED light-pipe designs: import STEP revisions into a project, describe the LEDs,
materials and finishes, run a spectral Monte Carlo ray trace and look at exit-surface brightness, uniformity,
hotspots, colour mixing and the efficiency of getting light from the LEDs to that surface.

Everything runs on your computer. Projects and results are stored in a data folder (shown in Settings).

*Refract was called LightSim before. On the first start the old data folder (`%LOCALAPPDATA%\lightsim`) is renamed to `refract`
and the saved UI settings are carried over; the old `LIGHTSIM_*` environment variables and `LightSim-Setup-*.exe` installer names are still understood.*

## Start

```powershell
.\start.ps1 -Setup    # first time: virtual environment, Python packages, UI build
.\start.ps1           # afterwards
```

It was developed with Python 3.13 and a recent Node (the UI is built once with `npm run build`). The packaged installer is built with
`.\packaging\build.ps1` (PyInstaller and Inno Setup).

**GPU solver (optional).** With an NVIDIA GPU, `.venv\Scripts\python.exe -m pip install -r server\requirements-gpu.txt` adds a second solver (NVIDIA Warp on CUDA). It traces the same physics about 20 times faster than the CPU solver (about 3 million rays per second against 160,000 on a Ryzen 7 7800X3D with an RTX 5080, measured on a 330,000 triangle light pipe) and agrees with it within the noise of the maps. Choose it in the *Solver* select of the Run tab or the live preview; the CPU solver stays the default. Ray state and optics are float32 on the GPU (tallies are summed in float64), so results match the CPU solver statistically, not digit for digit. The light path analysis runs on the GPU too (about 1.6 million rays per second with it on): the face tables and group maps take every ray of every pass, also when a run stops early at a noise target, so no separate final pass is needed; the ray logs behind the analysis views still cover the first 2 million rays of a channel. A run's rays can only be replayed by the solver that traced them, so an analysis remembers its solver. `python server\scripts\gpu_bench.py <project> <revision> <setup>` compares and times both solvers on a stored project. The packaged installer does not include Warp.

## Releasing and updating

**Release:** push a tag (`git tag v0.2.0; git push origin v0.2.0`; a tag with a suffix such as `v0.3.0-beta.1` becomes a pre-release),
or run *Actions > Release installer* by hand with a version. The workflow (`.github/workflows/release.yml`) builds
`Refract-Setup-<version>.exe` on a Windows runner and publishes it with its `.sha256` as a GitHub release. The version is
stamped into the build from the tag; nothing needs to be committed first. The *Bundle Mitsuba 3* option of the manual run adds
Render picture to the installer (several hundred MB).

**Update:** the app checks for a newer installer when it starts (switch off in *Settings > Updates*) and offers *Update now*.
The installer is downloaded, checked against its SHA-256 and run silently; the app restarts by itself. *Update source*
in the same place chooses where it looks:

- *GitHub releases* (default; the repository must be public), optionally including pre-releases.
- *A folder or web address of my own*: a local folder, a network share (`\\server\share\Refract`) or `https://…`. It holds
  `Refract-Setup-<version>.exe` (the highest version is offered, `.sha256` next to it is checked). `packaging\build.ps1` also
  writes `latest.json` (`version`, `file`, `sha256`, `notes`) into `dist-installer\`; a web address needs that file.

The installer is not code-signed, so Windows SmartScreen may warn when the installer is run by hand.

## Typical use

1. Drop a STEP file on the start page. A new project is created; later drops can be added to it. A project is organised in two levels:
   **designs** (for example "Baseline" and "Dome reflector") and, inside each design, **variants** (for example several diffuser
   thicknesses). Each variant has its own setup (materials, LEDs, exit surface), and its revisions (Rev 1, Rev 2, ...) are
   successive edits of that variant. The design switcher in the top bar groups the variants by design (a revision dropdown next to it appears when a variant has several revisions); it is only a visual grouping,
   runs of any variants can be compared. A dropped file is a new revision of a variant, a new variant in a design, or a new design
   (the choice in the upload dialog, or *New variant* / *New design* in the top bar). A new variant or design starts from a copy of
   the setup you pick and adapts it to the new geometry (LEDs added or removed, the "only LED n" states regenerated); *Adapt setup*
   does the same for an existing revision. Check the notes it shows.
   *Duplicate* (top bar, or the copy icon on a variant in the switcher) makes a new variant in the same design with the same geometry
   and no upload, with a copy of the setup, for trying other materials or LEDs next to the original; the CAD is imported again as
   its Rev 1 and no runs are copied. *Move to design* regroups a variant. *Re-mesh* adds a revision of the same CAD at another mesh
   quality; it is listed under its source revision and is not numbered. A design keeps at least one variant, and a project at
   least one design; deleting a design deletes its variants.
2. **Auto-setup** suggests materials, the LED positions and the exit surface from the CAD. Check them and change what is wrong:
   parts get a material (for example *PC + 0.03 wt % TiO2*) and a finish, LEDs are placed by clicking the emitting face, the
   exit surface is picked by clicking faces, and *States* define which LEDs (or colour channels) are on.
3. **Run** a simulation. Quick looks use 200,000 rays per LED channel; use more for smooth colour maps (the run settings go up to 50 million). The result shows *Map noise*: the relative standard error of a map pixel, with the number of rays or the map resolution that would bring it to 5 %. A map is judged converged at 5 % or less; before that, speckle on the map is noise, not light. *Stop early when map noise is below* traces in growing passes and stops as soon as the target is reached (the ray count is then the maximum; tick *Keep light path analysis* to add one final pass of about 2 million rays per channel that provides the analysis views; without it such a run has no analysis). *Quick look: one channel per LED* traces only the brightest driven channel of each LED (about 3 times faster for RGB LEDs), which is enough to judge how the light is spread but not its colour or absolute brightness.
4. Open a run to see efficiency, luminance map, uniformity, hotspots, colour, per-LED contributions and where the light went.
   *Map on model* drapes the map on the CAD, *Rays* shows recorded ray paths, *Compare* sets two runs side by side (maps), or lists any number of runs of the project, across variants, in one table with
   changes against a baseline run and overlaid profiles. A state that a variant does not have falls back to "All LEDs on".
   **Light path analysis** (saved runs only, kept for the first 2,000,000 rays per channel; the run settings `analysis` and `analysis_rays` change that): the *Groups* tab names the surfaces to analyse (by default every opaque face, the largest 62; you can also add faces of the light pipe). Under the map of an open run, *Surfaces* lists what share of the exit light left each surface last or touched it at all, how much it absorbs, and the footprint of its light on the exit surface; *Where does it come from?* takes a box or an outline on the exit map and breaks down the light landing there by last surface, LED, number of bounces and route; *Routes* groups all rays by the sequence of surfaces they touched; *What to change* shows the deficit map and a first-order estimate of how much each surface would have to send more or less light to the exit to flatten the map. Every list can send its rays to the 3D view (*Analysis* panel): the rays are traced again exactly, coloured by LED, remaining flux, bounce, event or surface, with a bounce slider, a glow (density) mode, and filters. In the *Surfaces* section of that panel you can click a face of the model to see what hits it, what it absorbs and where its light comes from and goes, or paint every face by the light it gets.
5. **Parameter sweep** repeats the run for several TiO2 loadings, particle sizes or scattering coefficients.
6. **Render picture** makes a camera view of the exit surface with Mitsuba 3. It uses an NVIDIA GPU (CUDA) when one works and falls back to the CPU otherwise; set `REFRACT_MITSUBA_VARIANT=scalar_spectral` to force the CPU.
7. **Live** (toolbar above the 3D view) simulates the setup as it is in the editor, without a Run. Switch on *Physics*: the exit-surface map is drawn on the model within a few seconds and sharpens while more rays are traced; any edit of a part, LED, state or the library, or a move of the TiO2 slider (the setup is not changed), starts it again. The numbers shown are efficiency, min/max, hotspots, colour and CCT. *Keep* turns a finished preview into an ordinary run. *Photoreal view* draws the model lit by the simulated exit surface over the 3D view with the same camera, rendered by Mitsuba on the GPU; it refines while the camera is still and follows the simulation as it improves. While you orbit, the picture is hidden and the plain CAD view is shown, so positioning the camera is smooth; the picture comes back a moment after you let go. *Resolution* sets the size of the picture, from the size of the 3D view up to 4096 px along the long side; a bigger picture is sharper but each pass takes longer, so it needs more time to refine. *Studio light* sets a soft studio environment (a key light, a fill and a rim light that turn with the camera) as a fraction of the brightness of the exit surface; it lights the housing and shows reflections in the parts. The picture lights only the exit surface by default; choose *Full light transport* to let the LEDs light the whole model, so light leaking through the sides or glowing in the pipe is visible too. That mode is a plain path trace (the rays find the LEDs by chance): it is grainy at first and cleans up over many seconds while the camera is still, and its brightness is scaled to the exit surface of the simulation. Cuts and hidden parts are not shown in that picture. Live previews use coarser maps (at most 200 pixels per side) and are not listed under *Run*.

## What is modelled

- Materials and textures: a *material* is what a part is made of, a *finish* is how its surface is textured. Volume materials (clear, TiO2-diffusing,
  thin translucent white plastic) let light in; opaque surface materials (PCB solder mask, white silkscreen, ABS/PC housings) only reflect or absorb at
  the surface, a faster shortcut for walls that are thick enough. Texture finishes (VDI 3400, Mold-Tech MT-110xx, polished) set the roughness for either;
  the roughness for each grade is a rule-of-thumb estimate (see `SIM_ENGINEERING_QA.md`). Older bundled opaque finishes remain available as surface presets.
- Light transport: Fresnel reflection and total internal reflection, Beckmann rough surfaces, opaque finishes (diffuse + specular),
  Beer-Lambert absorption, Henyey-Greenstein volume scattering, nested media, Russian roulette. Wavelength is traced per ray, so
  colour mixing and dispersion of scattering come out of the same run.
- LEDs: datasheet model (flux, spectrum, angular pattern, emitting area) per channel, or a measured text ray list.
- Diffuser: TiO2 in PC from Mie theory (loading, median particle size 0.3 um and size spread 1.3 by default), or fitted to a
  datasheet transmission and haze of a plaque. Mie theory describes an ideally dispersed filler, so a real compound usually scatters
  less: the *dispersion efficiency* of the material scales the scattering coefficient (1 = ideal; try 0.3 to 0.7 and set it from one
  measured part). Loadings are small for a light pipe: the built-in materials start at 0.001 wt% and the Live slider at 0.0005 wt%.
  Volume-scattering events have their own cap per ray (20,000), apart from the 500 surface-hit limit (*max_bounces*), so strongly
  scattering parts no longer lose light to the bounce limit; very high loadings are slow to trace.
- Exit surface: flat projection, or unwrapped (least squares conformal map) when strongly curved. The surface area of every map pixel is
  computed exactly (the exit triangles are clipped against the pixel grid), so the luminance map carries no noise other than that of the rays.
  The drawn map is smoothed with the *Smoothing* setting of the results (the same as for the statistics; *none* shows the raw pixels).
- Viewing cone: luminance is the light inside the half angle set on the exit surface. Besides that cone, both solvers tally per pixel the
  light in six rings of 5° (narrower for a small cone) around it, three inside and three outside, so the *View cone* control of the
  results shows any angle of that range (about ±15° around the set one) without tracing again; the noise estimate follows. Colour at another
  angle is taken from the traced cone (the rings keep luminance only), and the *Light path analysis* and the rendered picture stay at the
  traced cone. Runs made before the rings existed show their traced cone only.
- *Let the light go on after this surface* (exit surface option): by default the light that leaves through an exit surface is counted and the
  ray ends there. With the option on it is only measured: the part that leaves is tallied exactly as before, and the ray itself goes on in
  the air (at each hit it either reflects or goes through with the Fresnel probability, so the energy stays unbiased). Use it to get the
  pipe's own map and the pattern on a table in one run. The measured light is not an outcome of the rays, so it is not an entry of the
  energy budget; light that comes back and leaves through the surface again counts again. Rays still end where they always did: absorbed
  by a surface or the material, escaping out of the model, Russian roulette at very low weight, or the bounce limit.
- Receiver (detector type *Receiver*, for a design whose point is the light cast onto a surface such as a table, not the exit of the
  pipe): model the surface as real geometry (STEP) with the finish it has, pick its face as the detector and set *Type* to *Receiver*.
  Unlike an exit surface it does not consume the light: every ray that arrives on the face from the air side is counted and then
  carries on (absorbed, or reflected by the finish), so the energy budget is unchanged. One detector gives two maps. *Illuminance*
  (quantity *all directions*, lux) is the light that arrives. *Apparent luminance* (quantity *viewing cone*, cd/m2) is the light the
  surface reflects into a cone of half angle *Cone* around the *View elevation* / *View azimuth* direction (90° elevation = along the
  face normal; 3D view: blue arrow); it is estimated by sampling the reflected rays, so it needs more rays than the illuminance, and
  it is normalised with the projected solid angle of the cone, pi sin2(cone) sin(elevation). The KPI shows the flux arriving on the
  receiver (light that comes back and arrives again counts again) and the flux reflected toward the viewer. Both solvers (CPU, GPU),
  the light path analysis and the ray replay support receivers; the arrival is the "exit" event of a path. Limits: the reflected map
  is the reflection of an opaque finish (parametric finishes only, no subsurface scattering); a clear body counts the light arriving
  on it but has no reflected map; the faces of a receiver are not tagged for the analysis groups (they are not listed as surfaces);
  the Mitsuba picture draws a receiver as a Lambertian surface with its apparent luminance toward the viewing direction (so it looks the same from
  every angle). The picture is taken from the air side of the receiver's faces, and *Look at* chooses which detector the camera targets (it follows
  the detector shown in the results).

## Accuracy

The tool is meant for comparing designs ("relative accuracy"). Check absolute numbers against a measurement of one real part
before relying on them: LED binning, real diffuser scattering and surface texture are the largest uncertainties.

## Ray files

*Library, LEDs, Light source data, Measured ray set* reads a text file with one ray per line: `x y z l m n flux [wavelength_nm]`
(millimetres, direction cosines, +z along the emission axis). Binary and XML vendor formats (TM-25, Zemax DAT/SRC) must be
exported to this form first. IES and LDT files are intensity tables, not ray sets: enter them as a polar table.

## Tests

```powershell
cd server
..\.venv\Scripts\python.exe -m pytest tests -q
```

