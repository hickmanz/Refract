<template>
  <div class="form-grid">
    <label class="field"><span>Name</span><input v-model="doc.name" :disabled="readonly" /></label>

    <label class="field">
      <span>Kind of material</span>
      <select :value="mode" :disabled="readonly" @change="setMode(($event.target as HTMLSelectElement).value)">
        <option value="volume">Volume: light enters it (clear, diffusing or translucent plastic, glass)</option>
        <option value="surface">Opaque surface: only reflects or absorbs at the surface (PCB, housing, paint)</option>
      </select>
      <small class="muted">A thin white plastic wall passes some light, so model it as a volume with TiO₂ scattering. An opaque surface is the faster shortcut for walls thick enough that nothing gets through.</small>
    </label>

    <template v-if="mode === 'surface'">
      <div class="field">
        <span>Colour</span>
        <div class="color-row">
          <input type="color" :value="doc.color || '#ffffff'" :disabled="readonly" @input="pickColor($event)" />
          <code class="muted">{{ doc.color || "neutral (grey)" }}</code>
          <button v-if="doc.color && !readonly" class="btn small" type="button" @click="doc.color = undefined">No colour</button>
        </div>
        <small class="muted">The colour of the surface in white light. Picking one also sets the diffuse reflectance to that colour's brightness, which you can still change below.</small>
      </div>
      <label v-if="doc.color && (doc.rspec ?? 0) > 0.2" class="check small"><input v-model="doc.tint_specular" type="checkbox" :disabled="readonly" /> also colour the mirror part (coloured metal)</label>
      <label class="field">
        <span>Diffuse reflectance</span>
        <input v-model.number="doc.rdiff" type="number" step="0.01" min="0" max="1" :disabled="readonly" />
        <small class="muted">White plastic or mask about 0.80 to 0.90, grey 0.2 to 0.6, black about 0.04.</small>
      </label>
      <label class="field">
        <span>Gloss reflectance</span>
        <input v-model.number="doc.rspec" type="number" step="0.01" min="0" max="1" :disabled="readonly" />
        <small class="muted">The mirror-like part. Plastics and solder mask are about 0.04 to 0.05; polished metal 0.8 or more. Diffuse + gloss must not exceed 1.</small>
      </label>
      <label class="field">
        <span>Gloss roughness (RMS slope σ)</span>
        <input v-model.number="doc.sigma" type="number" step="0.01" min="0" max="1.5" :disabled="readonly" />
        <small class="muted">The default for this material. A texture finish on the part (VDI, MT) overrides it; "As material" keeps this value.</small>
      </label>
    </template>

    <template v-if="mode === 'volume'">
    <div class="field">
      <span>Refractive index</span>
      <CurveEditor v-model="doc.n" :kinds="['constant', 'cauchy', 'table']" :readonly="readonly" />
    </div>
    <div class="field">
      <span>Absorption coefficient (1/mm)</span>
      <CurveEditor v-model="doc.absorption" :kinds="['constant', 'power', 'table']" :readonly="readonly" />
      <small class="muted">Light lost per mm of travel inside the material. Clear PC at 550 nm is about 0.001 per mm; yellowing raises it at blue wavelengths.</small>
    </div>

    <div class="field">
      <span>Volume scattering (diffusion)</span>
      <select :value="scType" :disabled="readonly" @change="setScType(($event.target as HTMLSelectElement).value)">
        <option value="none">None (clear)</option>
        <option value="mie">TiO₂ filler: calculated from particle size and loading (Mie)</option>
        <option value="constant">Fixed values: scattering coefficient and anisotropy</option>
        <option value="table">Table by wavelength (JSON mode)</option>
      </select>
    </div>

    <template v-if="scType === 'constant'">
      <label class="field"><span>Scattering coefficient μs (1/mm)</span><input v-model.number="sc.mus" type="number" step="any" min="0" :disabled="readonly" /></label>
      <label class="field">
        <span>Anisotropy g</span>
        <input v-model.number="sc.g" type="number" step="0.05" min="-0.99" max="0.99" :disabled="readonly" />
        <small class="muted">0 scatters equally in all directions, 0.5 to 0.8 is typical for TiO₂ in PC (mostly forward).</small>
      </label>
    </template>

    <template v-if="scType === 'mie'">
      <label class="field"><span>TiO₂ loading (weight %)</span><input v-model.number="sc.tio2_wt_pct" type="number" step="any" min="0" max="60" :disabled="readonly" /></label>
      <label class="field"><span>Particle diameter (µm)</span><input v-model.number="sc.diameter_um" type="number" step="0.01" min="0.05" max="5" :disabled="readonly" /></label>
      <label class="field">
        <span>Size spread (geometric σ)</span>
        <input v-model.number="sc.sigma_g" type="number" step="0.05" min="1" max="2" :disabled="readonly" />
        <small class="muted">1.0 means all particles are the same size; real powders are about 1.3 to 1.5.</small>
      </label>
      <label class="field">
        <span>Dispersion efficiency (1 = ideal)</span>
        <input v-model.number="sc.efficiency" type="number" step="0.05" min="0" max="10" placeholder="1" :disabled="readonly" />
        <small class="muted">Scales the scattering coefficient. 1 is a perfectly dispersed filler of spheres; agglomerated or poorly dispersed TiO₂ scatters less (try 0.3 to 0.7), so set it from one measured part.</small>
      </label>
      <label class="field">
        <span>Host polymer</span>
        <select v-model="sc.host" :disabled="readonly">
          <option value="pc">Polycarbonate</option>
          <option value="pmma">PMMA</option>
          <option value="silicone">Silicone</option>
          <option value="epoxy">Epoxy</option>
        </select>
      </label>
    </template>

    <section v-if="scType === 'mie' || scType === 'constant'" class="diffuser-tools">
      <div class="dt-head">
        <strong>Diffuser calculator</strong>
        <span class="muted small">Check what this diffuser does at a given thickness, or tune it to datasheet transmission. If the light passing straight through plus the diffuse transmission is below about 1 % at the wall thickness, an opaque surface material is a safe, faster shortcut.</span>
      </div>
      <div class="dt-row">
        <label class="mini">Thickness (mm) <input v-model.number="thickness" type="number" step="0.1" min="0.1" /></label>
        <button v-if="scType === 'mie'" class="btn small" :disabled="busy" @click="runMie"><Calculator :size="14" /> Calculate</button>
        <span v-if="mie" class="small">
          Volume fraction {{ (mie.volumeFraction * 100).toFixed(2) }} %.
          At 550 nm: μs = {{ at550(mie.mus).toFixed(2) }}/mm, g = {{ at550(mie.g).toFixed(2) }},
          light passing straight through {{ (at550(mie.ballisticTransmission) * 100).toFixed(1) }} %.
        </span>
      </div>
      <LineChart v-if="mie" title="Scattering coefficient by wavelength" :series="mieSeries" x-label="nm" y-label="1/mm" />

      <div class="dt-fit">
        <strong class="small">Fit to datasheet</strong>
        <p class="muted small">Enter total light transmission (and haze if given) for one or more plaque thicknesses. The fit finds μs and g that reproduce them using the same scattering model as the simulation (Fresnel reflection at both faces, Henyey-Greenstein scattering inside). Haze lets it fit g as well; without it g is fixed at 0.6. The result is a fixed-value scattering that you can apply to this material.</p>
        <table class="data-table compact">
          <thead><tr><th>Thickness (mm)</th><th>Total transmission (%)</th><th>Haze (%, optional)</th><th></th></tr></thead>
          <tbody>
            <tr v-for="(s, i) in samples" :key="i">
              <td><input v-model.number="s.thickness_mm" type="number" step="0.1" min="0.1" /></td>
              <td><input v-model.number="s.total" type="number" step="0.1" min="0" max="100" /></td>
              <td><input v-model.number="s.haze" type="number" step="0.1" min="0" max="100" /></td>
              <td><button class="icon-btn" title="Remove" @click="samples.splice(i, 1)"><X :size="14" /></button></td>
            </tr>
          </tbody>
        </table>
        <div class="dt-row">
          <button class="btn small" @click="samples.push({ thickness_mm: 2, total: 60, haze: null })"><Plus :size="14" /> Add thickness</button>
          <button class="btn primary small" :disabled="busy || !samples.length" @click="runFit"><Wand2 :size="14" /> Fit</button>
          <span v-if="busy" class="spinner" />
        </div>
        <div v-if="fit" class="notice">
          <div>
            <p class="small"><strong>μs = {{ fit.mus.toFixed(3) }} /mm, g = {{ fit.g.toFixed(2) }}</strong> (fit error {{ fit.cost.toFixed(4) }})</p>
            <table class="data-table compact">
              <thead><tr><th>Thickness</th><th>Measured T</th><th>Model T</th><th>Measured haze</th><th>Model haze</th></tr></thead>
              <tbody>
                <tr v-for="p in fit.predictions" :key="p.thickness_mm">
                  <td>{{ p.thickness_mm }} mm</td>
                  <td>{{ (p.measured_total * 100).toFixed(1) }} %</td>
                  <td>{{ (p.total_transmission * 100).toFixed(1) }} %</td>
                  <td>{{ p.measured_haze == null ? "–" : (p.measured_haze * 100).toFixed(1) + " %" }}</td>
                  <td>{{ p.haze == null ? "–" : (p.haze * 100).toFixed(1) + " %" }}</td>
                </tr>
              </tbody>
            </table>
            <p v-for="n in fit.notes ?? []" :key="n" class="muted small">{{ n }}</p>
            <button class="btn small" :disabled="readonly" @click="applyFit"><Check :size="14" /> Use these values for this material</button>
          </div>
        </div>
      </div>
      <p v-if="error" class="error small">{{ error }}</p>
    </section>
    </template>

    <label class="field"><span>Notes</span><textarea v-model="doc.notes" rows="2" :disabled="readonly" /></label>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { Calculator, Check, Plus, Wand2, X } from "lucide-vue-next";
import { api, type FitResult, type MaterialDoc, type MieResult } from "../../api";
import LineChart from "../LineChart.vue";
import CurveEditor from "./CurveEditor.vue";

const props = defineProps<{ doc: MaterialDoc; readonly?: boolean }>();

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const sc = computed<Record<string, any>>(() => {
  if (!props.doc.scattering) props.doc.scattering = { type: "none" };
  return props.doc.scattering as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
});
const scType = computed(() => String(sc.value.type ?? "none"));

const mode = computed(() => (props.doc.mode === "surface" ? "surface" : "volume"));
function setMode(m: string) {
  const d = props.doc;
  if (m === "surface") {
    d.mode = "surface";
    d.rdiff ??= 0.8;
    d.rspec ??= 0.04;
    d.sigma ??= 0.03;
  } else {
    d.mode = "volume";
    d.n ??= { type: "cauchy", A: 1.58, B: 0.01 };
    d.absorption ??= 0.001;
    d.scattering ??= { type: "none" };
  }
}
function pickColor(e: Event) {
  const hex = (e.target as HTMLInputElement).value;
  props.doc.color = hex;
  // the reflectance level follows the colour's luminance (linear light), so a dark colour is also a dark surface
  const lin = [1, 3, 5].map((i) => {
    const v = parseInt(hex.slice(i, i + 2), 16) / 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  const y = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
  const total = Math.max(0, 1 - (props.doc.rspec ?? 0));
  props.doc.rdiff = Math.round(Math.min(y, total) * 100) / 100;
}

function setScType(t: string) {
  if (t === "none") props.doc.scattering = { type: "none" };
  else if (t === "constant") props.doc.scattering = { type: "constant", mus: 0.5, g: 0.6 };
  else if (t === "mie") props.doc.scattering = { type: "mie", tio2_wt_pct: 1, diameter_um: 0.3, sigma_g: 1.3, host: "pc", efficiency: 1 };
  else if (t === "table") props.doc.scattering = { type: "table", lambda: [380, 780], mus: [0.5, 0.5], g: [0.6, 0.6] };
}

const thickness = ref(3);
const busy = ref(false);
const error = ref("");
const mie = ref<MieResult | null>(null);
const fit = ref<FitResult | null>(null);
const samples = reactive<{ thickness_mm: number; total: number; haze: number | null }[]>([
  { thickness_mm: 1, total: 70, haze: null },
  { thickness_mm: 3, total: 55, haze: null },
]);

function at550(a: number[]): number {
  const i = mie.value ? mie.value.lambda.findIndex((l) => l >= 550) : 0;
  return a[Math.max(i, 0)];
}
const mieSeries = computed(() => {
  const m = mie.value;
  if (!m) return [];
  return [{ label: "μs", color: "#2563eb", x: m.lambda, y: m.mus }];
});

async function runMie() {
  busy.value = true;
  error.value = "";
  try {
    const s = sc.value;
    mie.value = await api.mie({ tio2_wt_pct: s.tio2_wt_pct, diameter_um: s.diameter_um, host: s.host ?? "pc", sigma_g: s.sigma_g ?? 1.3, efficiency: s.efficiency === "" || s.efficiency == null ? 1 : s.efficiency, thickness_mm: thickness.value });
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
async function runFit() {
  busy.value = true;
  error.value = "";
  fit.value = null;
  try {
    const host = scType.value === "mie" ? String(sc.value.host ?? "pc") : "pc";
    fit.value = await api.fitDiffuser({
      host,
      samples: samples.map((s) => ({
        thickness_mm: s.thickness_mm,
        total_transmission: s.total / 100,
        haze: s.haze == null || (s.haze as unknown) === "" ? null : s.haze / 100,
      })),
    });
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
function applyFit() {
  if (fit.value) props.doc.scattering = { ...(fit.value.material as object), type: "constant" } as MaterialDoc["scattering"];
}
</script>
