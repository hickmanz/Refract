<template>
  <section class="res-card parts-card">
    <div class="hm-title"><span>Parts in this simulation</span></div>
    <div v-if="error" class="notice warn"><AlertTriangle :size="15" /><span>{{ error }}</span></div>
    <div v-else-if="loading && !info" class="muted small"><div class="spinner small" /> Loading parts…</div>
    <template v-else-if="info">
      <table class="data-table parts-table">
        <thead>
          <tr><th>Part</th><th>Material</th><th>Finish</th><th>Absorbs</th></tr>
        </thead>
        <tbody>
          <template v-for="p in info.parts" :key="p.index">
            <tr class="part-row" :class="{ open: openIdx === p.index }" @click="toggle(p.index)">
              <td class="part-name">
                <ChevronRight :size="13" class="chev" />
                <span :title="p.path">{{ p.name }}</span>
                <span v-if="p.faceOverrides.length" class="chip" :title="`${p.faceOverrides.length} face(s) with a finish of their own`">{{ p.faceOverrides.length }} face override{{ p.faceOverrides.length === 1 ? "" : "s" }}</span>
              </td>
              <td>
                <span v-if="p.material">{{ p.material.name }}</span>
                <span v-else class="muted">none (opaque)</span>
              </td>
              <td>{{ p.finish.name }}</td>
              <td>{{ absorbedText(p) }}</td>
            </tr>
            <tr v-if="openIdx === p.index" class="detail-row">
              <td colspan="4">
                <div class="detail-grid">
                  <div class="detail-block">
                    <h5>Material<span v-if="p.material" class="muted"> · {{ kindText(p) }}</span></h5>
                    <template v-if="p.material?.properties">
                      <dl v-if="p.material.properties.kind === 'volume'" class="props">
                        <dt>Refractive index (550 nm)</dt><dd>{{ fmtNum(p.material.properties.n550, 4) }} <span class="muted">({{ p.material.properties.nCurve }} curve)</span></dd>
                        <dt>Absorption (550 nm)</dt><dd>{{ fmtNum(p.material.properties.absorption550, 4) }} <span class="muted">1/mm</span></dd>
                        <dt>Scattering</dt><dd>{{ scatterText(p.material.properties) }}</dd>
                        <template v-if="p.material.properties.scattering?.type === 'mie'">
                          <dt>TiO₂ filler</dt><dd>{{ p.material.properties.scattering?.tio2_wt_pct }} wt %, {{ p.material.properties.scattering?.diameter_um }} µm particles</dd>
                          <dt v-if="p.material.properties.scattering?.efficiency !== undefined">Dispersion efficiency</dt><dd v-if="p.material.properties.scattering?.efficiency !== undefined">{{ fmtNum(p.material.properties.scattering?.efficiency, 3) }}</dd>
                        </template>
                      </dl>
                      <dl v-else class="props">
                        <dt>Diffuse reflectance</dt><dd>{{ fmtNum(p.material.properties.rdiff, 3) }}</dd>
                        <dt>Gloss (specular)</dt><dd>{{ fmtNum(p.material.properties.rspec, 3) }}</dd>
                        <dt>Roughness σ</dt><dd>{{ fmtNum(p.material.properties.sigma, 3) }}</dd>
                        <dt v-if="p.material.properties.color">Colour</dt>
                        <dd v-if="p.material.properties.color"><i class="swatch" :style="{ background: p.material.properties.color }" /> {{ p.material.properties.color }}</dd>
                      </dl>
                      <p v-if="p.material.properties.notes" class="muted small notes">{{ p.material.properties.notes }}</p>
                    </template>
                    <p v-else-if="p.material" class="muted small">The run's library no longer describes this material.</p>
                    <p v-else class="muted small">No material: the part is an opaque body with only the finish below.</p>
                  </div>

                  <div class="detail-block">
                    <h5>Finish</h5>
                    <dl v-if="p.finish.properties" class="props">
                      <dt>Type</dt><dd>{{ finishType(p.finish.properties) }}</dd>
                      <template v-if="p.finish.properties.type === 'opaque'">
                        <dt>Diffuse reflectance</dt><dd>{{ fmtNum(p.finish.properties.rdiff, 3) }}</dd>
                        <dt>Gloss (specular)</dt><dd>{{ fmtNum(p.finish.properties.rspec, 3) }}</dd>
                      </template>
                      <dt>Roughness σ</dt><dd>{{ p.finish.properties.inherit ? "from the material" : fmtNum(p.finish.properties.sigma, 3) }}</dd>
                      <dt v-if="p.finish.properties.color">Colour</dt>
                      <dd v-if="p.finish.properties.color"><i class="swatch" :style="{ background: p.finish.properties.color }" /> {{ p.finish.properties.color }}</dd>
                    </dl>
                    <p v-if="p.kind === 'surface'" class="muted small">The surface material sets the reflectances; a texture finish only changes how sharp its gloss is.</p>

                    <template v-if="p.faceOverrides.length">
                      <h5 class="sub">Faces with their own finish</h5>
                      <ul class="face-list">
                        <li v-for="f in p.faceOverrides" :key="String(f.face)" :class="{ gone: !f.applied }">
                          <strong>Face {{ f.face }}</strong>: {{ f.name }}
                          <span v-if="f.properties" class="muted"> · {{ overrideText(f.properties) }}</span>
                          <span v-if="!f.applied" class="chip failed" title="This face no longer exists on the model; the override was ignored">ignored</span>
                        </li>
                      </ul>
                    </template>
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <p v-if="info.excluded.length" class="muted small excluded">
        Left out of the run (turned off in the setup): {{ info.excluded.map((e) => e.name).join(", ") }}.
      </p>
      <p class="muted small">Properties are those the run used, kept with the run: editing the library afterwards does not change them. Click a part for details.</p>
    </template>
  </section>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import { AlertTriangle, ChevronRight } from "lucide-vue-next";
import { api, type FinishInfo, type MaterialInfo, type PartInfo, type RunParts } from "../api";
import { fmtNum, fmtPct } from "../utils/colormap";

const props = defineProps<{ runId: string; state?: string }>();

const info = ref<RunParts | null>(null);
const error = ref("");
const loading = ref(false);
const openIdx = ref<number | null>(null);
let seq = 0;

async function load() {
  const mine = ++seq;
  loading.value = true;
  try {
    const r = await api.runParts(props.runId, props.state);
    if (mine !== seq) return;
    info.value = r;
    error.value = "";
  } catch (e) {
    if (mine === seq) error.value = (e as Error).message;
  } finally {
    if (mine === seq) loading.value = false;
  }
}
// the run decides the parts; the state only changes how much each one absorbed
watch(() => props.runId, () => {
  info.value = null;
  openIdx.value = null;
  void load();
}, { immediate: true });
watch(() => props.state, () => {
  if (info.value) void load();
});

function toggle(i: number) {
  openIdx.value = openIdx.value === i ? null : i;
}
function absorbedText(p: PartInfo): string {
  const total = p.absorbed.volume + p.absorbed.surface;
  if (total < 1e-5) return "-";
  const split = p.absorbed.volume > 1e-5 && p.absorbed.surface > 1e-5 ? ` (${fmtPct(p.absorbed.volume, 1)} in it, ${fmtPct(p.absorbed.surface, 1)} on its surface)` : "";
  return fmtPct(total, 1) + split;
}
function kindText(p: PartInfo): string {
  return p.kind === "volume" ? "a transparent or translucent volume" : p.kind === "surface" ? "an opaque surface material" : "";
}
function scatterText(m: MaterialInfo): string {
  const s = m.scattering;
  if (!s || s.type === "none") return "none (clear)";
  const parts: string[] = [];
  if (s.mus550 !== undefined) parts.push(`μs ${fmtNum(s.mus550, 3)} /mm`);
  if (s.g550 !== undefined) parts.push(`g ${fmtNum(s.g550, 2)}`);
  return `${s.type === "mie" ? "Mie (filler)" : s.type}${parts.length ? ": " + parts.join(", ") : ""}`;
}
function finishType(f: FinishInfo): string {
  if (f.type === "opaque") return "opaque (reflects, nothing passes)";
  if (f.type === "texture") return "texture (roughness only)";
  return "dielectric surface";
}
function overrideText(f: FinishInfo): string {
  if (f.type === "opaque") return `reflectance diffuse ${fmtNum(f.rdiff, 2)}, gloss ${fmtNum(f.rspec, 2)}, σ ${fmtNum(f.sigma, 2)}`;
  return `σ ${f.inherit ? "from the material" : fmtNum(f.sigma, 3)}`;
}
</script>

<style scoped>
.parts-table .part-row { cursor: pointer; }
.parts-table .part-row:hover { background: var(--neutral-2); }
.parts-table th, .parts-table td { text-align: left !important; }
.part-name { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.chev { flex: none; transition: transform 0.12s; color: var(--neutral-10); }
.part-row.open .chev { transform: rotate(90deg); }
.detail-row td { background: var(--neutral-2); padding: 8px 12px; }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px 24px; }
.detail-block h5 { margin: 0 0 4px; font-size: 0.8rem; color: var(--neutral-11); }
.detail-block h5.sub { margin-top: 10px; }
.props { display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; margin: 0; font-size: 0.8rem; }
.props dt { color: var(--neutral-10); }
.props dd { margin: 0; }
.notes { margin: 6px 0 0; max-width: 60ch; }
.swatch { display: inline-block; width: 11px; height: 11px; border-radius: 3px; vertical-align: -1px; border: 1px solid var(--neutral-6); }
.face-list { margin: 0; padding-left: 16px; font-size: 0.8rem; display: flex; flex-direction: column; gap: 2px; }
.face-list .gone { opacity: 0.6; }
.excluded { margin: 6px 0 0; }
</style>
