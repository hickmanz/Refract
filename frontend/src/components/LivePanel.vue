<template>
  <section class="live-panel" @click.stop @pointerdown.stop @wheel.stop>
    <header class="live-head">
      <Zap :size="15" />
      <strong>Live</strong>
      <span class="spacer" />
      <label class="switch" :title="live.enabled.value ? 'Stop the live simulation' : 'Simulate the setup as you edit it'">
        <input type="checkbox" :checked="live.enabled.value" @change="live.toggle()" />
        <span>Physics</span>
      </label>
      <button class="icon-btn" title="Close this panel (the simulation keeps running)" @click="$emit('close')"><X :size="14" /></button>
    </header>

    <p v-if="!live.enabled.value" class="muted small">
      Switch on to trace the setup as you edit it: the exit-surface map appears on the model within seconds and sharpens while more rays are traced. Change a part, an LED or the diffuser and it starts again.
    </p>

    <template v-else>
      <div v-if="live.phase.value === 'error'" class="notice warn small"><AlertTriangle :size="14" /><span>{{ live.error.value }}</span></div>
      <div v-else class="live-status small">
        <div v-if="live.phase.value !== 'done'" class="spinner small" />
        <Check v-else :size="14" class="ok-icon" />
        <span v-if="live.phase.value === 'starting'">Starting…</span>
        <span v-else-if="live.phase.value === 'running'">Tracing {{ (live.run.value?.live?.rays ?? 0).toLocaleString() }} / {{ (live.run.value?.live?.target ?? live.rays.value).toLocaleString() }} rays per channel</span>
        <span v-else>Done, {{ (live.run.value?.live?.rays ?? live.rays.value).toLocaleString() }} rays per channel</span>
      </div>
      <div v-if="live.phase.value === 'running'" class="progress thin"><div class="progress-bar" :style="{ width: live.progress.value * 100 + '%' }" /></div>

      <label v-if="live.states.value.length > 1" class="mini" title="Which LEDs are on. The simulation covers every LED, so switching state needs no new run.">
        <span>State</span>
        <select :value="live.shownState.value" @change="live.stateId.value = ($event.target as HTMLSelectElement).value">
          <option v-for="s in live.states.value" :key="s.id" :value="s.id">{{ s.name }}</option>
        </select>
      </label>

      <div v-if="det" class="live-metrics">
        <div><span class="muted">Efficiency</span><strong>{{ fmtPct(det.efficiency) }}</strong></div>
        <div><span class="muted">Min / max</span><strong>{{ det.stats.minOverMax == null ? "–" : fmtPct(det.stats.minOverMax, 0) }}</strong></div>
        <div><span class="muted">Hotspots</span><strong>{{ det.hotspots.length }}</strong></div>
        <div><span class="muted">Δu′v′</span><strong>{{ det.color?.maxDuv == null ? "–" : det.color.maxDuv.toFixed(3) }}</strong></div>
        <div v-if="det.color"><span class="muted">CCT</span><strong>{{ Math.round(det.color.cct) }} K</strong></div>
        <div><span class="muted">Exit</span><strong>{{ det.exitLm.toFixed(2) }} lm</strong></div>
      </div>
      <p v-else-if="live.phase.value !== 'error'" class="muted small">The first picture appears after a couple of seconds.</p>
      <NoiseBadge v-if="det" :noise="det.noise" />

      <div v-if="live.diffusers.value.length" class="live-slider">
        <label class="mini">
          <span>Diffuser</span>
          <select :value="live.partId.value" @change="live.selectPart(($event.target as HTMLSelectElement).value)">
            <option v-for="d in live.diffusers.value" :key="d.inst.id" :value="d.inst.id">{{ d.inst.name }} ({{ d.material }})</option>
          </select>
        </label>
        <div class="slider-row">
          <span class="small">TiO₂</span>
          <input type="range" min="0" max="1" step="0.002" :value="sliderPos" @input="onSlide(($event.target as HTMLInputElement).valueAsNumber)" />
          <strong class="small">{{ fmtNum(live.shownTio2.value) }} %</strong>
        </div>
        <p v-if="live.tio2.value !== null" class="muted small">
          Trying {{ fmtNum(live.tio2.value) }} % instead of {{ fmtNum(live.baseTio2.value) }} % in the material.
          <a href="#" @click.prevent="live.setTio2(null)">Back to the material's value</a>
        </p>
      </div>
      <p v-else class="muted small">Give a part a diffusing material (with TiO₂) to try its loading here.</p>

      <div class="live-row">
        <label class="mini">
          <span>Rays per channel</span>
          <select v-model.number="live.rays.value">
            <option v-for="r in LIVE_RAY_CHOICES" :key="r" :value="r">{{ r.toLocaleString() }}</option>
          </select>
        </label>
        <button class="btn small" :disabled="live.phase.value !== 'done'" title="Keep this result as a run in the Results tab" @click="live.keep()"><Save :size="14" /> Keep</button>
      </div>
      <label v-if="live.gpu.value?.available" class="mini" title="Trace on the GPU: the same physics, many times faster, so a preview sharpens in moments.">
        <span>Solver</span>
        <select v-model="live.chosen.value">
          <option value="cpu">CPU</option>
          <option value="gpu">GPU ({{ live.gpu.value.name }})</option>
        </select>
      </label>
      <label class="mini" title="Stop tracing before the ray count above once the map is this quiet (relative noise per pixel)">
        <span>Stop when map noise is below</span>
        <select v-model.number="live.targetNoise.value">
          <option :value="0">Off (trace all rays)</option>
          <option :value="0.1">10 %</option>
          <option :value="0.05">5 % (converged)</option>
          <option :value="0.03">3 %</option>
        </select>
      </label>
      <p v-if="live.notice.value" class="muted small">{{ live.notice.value }}</p>

      <hr />
      <label class="switch" title="A picture of the model lit by the simulated light, rendered live on the GPU with the 3D view's camera">
        <input type="checkbox" :checked="view.on" @change="view.on = ($event.target as HTMLInputElement).checked" />
        <span>Photoreal view</span>
      </label>
      <template v-if="view.on">
        <label class="mini">
          <span>Light</span>
          <select v-model="view.mode">
            <option value="map">Exit surface only (smooth)</option>
            <option value="transport">Full light transport (noisy)</option>
          </select>
        </label>
        <label class="mini">
          <span title="Pixels along the long side of the picture. Higher is sharper and smoother at the edges, but every pass takes longer, so it needs more time to refine.">Resolution</span>
          <select v-model.number="view.resolution">
            <option :value="0">Fit the view</option>
            <option :value="1600">Fine (1600 px)</option>
            <option :value="2400">High (2400 px)</option>
            <option :value="3200">Very high (3200 px)</option>
            <option :value="4096">Maximum (4096 px)</option>
          </select>
        </label>
        <p v-if="view.mode === 'transport'" class="muted small">
          The LEDs light the whole model, so light leaking through the sides and glowing inside the pipe shows up. Rays find the LEDs only by chance, so the picture is grainy and cleans up over many seconds while the camera is still.
        </p>
        <div class="slider-row">
          <span class="small">Exposure</span>
          <input v-model.number="view.exposure" type="range" min="-3" max="3" step="0.1" />
          <strong class="small">{{ view.exposure > 0 ? "+" : "" }}{{ view.exposure.toFixed(1) }}</strong>
        </div>
        <div class="slider-row">
          <span class="small" title="A soft studio light around the model, relative to the brightness of the exit surface. It shows the housing and the reflections in the parts.">Studio light</span>
          <input v-model.number="view.ambient" type="range" min="0" max="1" step="0.01" />
          <strong class="small">{{ view.ambient.toFixed(2) }}</strong>
        </div>
        <label class="check small" title="Show a coloured light as neutral white, like the eye does. Off: the colour of the light as simulated."><input v-model="view.whiteBalance" type="checkbox" /> Neutralise the colour of the light</label>
        <p v-if="view.status?.state === 'live'" class="muted small">{{ view.status.variant.includes("cuda") ? "GPU" : "CPU" }} · {{ view.status.spp }} samples · {{ view.status.fps.toFixed(0) }} fps</p>
        <p v-else-if="view.status?.state === 'error'" class="error small">{{ view.status.message }}</p>
        <p v-else-if="view.status" class="muted small">{{ view.status.message }}</p>
        <p class="muted small">Cuts and hidden parts are not shown in this picture. Orbit as usual.</p>
      </template>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { AlertTriangle, Check, Save, X, Zap } from "lucide-vue-next";
import { LIVE_RAY_CHOICES, TIO2_RANGE, type LivePreview } from "../composables/useLivePreview";
import { fmtPct } from "../utils/colormap";
import NoiseBadge from "./NoiseBadge.vue";

export interface PhotorealView {
  on: boolean;
  exposure: number;
  ambient: number;
  whiteBalance: boolean;
  mode: "map" | "transport";
  /** Long side of the picture in pixels; 0 follows the size of the 3D view. */
  resolution: number;
  status: { state: "starting" | "waiting" | "live" | "error"; message: string; spp: number; ms: number; variant: string; fps: number } | null;
}

const props = defineProps<{ live: LivePreview; view: PhotorealView }>();
defineEmits<{ close: [] }>();

const det = computed(() => props.live.detector.value);
const [LO, HI] = TIO2_RANGE;
const sliderPos = computed(() => {
  const v = props.live.shownTio2.value ?? LO;
  return Math.min(1, Math.max(0, Math.log(Math.max(v, LO) / LO) / Math.log(HI / LO)));
});
function onSlide(t: number) {
  const v = LO * Math.pow(HI / LO, t);
  props.live.setTio2(Number(v.toPrecision(3)));
}
const fmtNum = (v: number | null) => (v == null ? "–" : Number(v.toPrecision(3)).toString());
</script>
