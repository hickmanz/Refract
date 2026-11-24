<template>
  <section class="live-panel" :class="{ collapsed }" @click.stop @pointerdown.stop @wheel.stop>
    <header class="live-head">
      <Zap :size="15" />
      <strong>Live</strong>
      <span class="spacer" />
      <label class="tog" :title="live.enabled.value ? 'Stop the live simulation' : 'Start simulating your setup as you edit it'">
        <input type="checkbox" :checked="live.enabled.value" @change="live.toggle()" />
        <span class="tog-track" />
        <span class="tog-text">{{ live.enabled.value ? "On" : "Off" }}</span>
      </label>
      <button class="icon-btn" :title="collapsed ? 'Expand' : 'Collapse'" :aria-expanded="!collapsed" @click="collapsed = !collapsed"><component :is="collapsed ? ChevronDown : ChevronUp" :size="14" /></button>
      <button class="icon-btn" title="Close this panel (the simulation keeps running)" @click="$emit('close')"><X :size="14" /></button>
    </header>

    <div v-show="!collapsed" class="dock-body">
      <p v-if="!live.enabled.value" class="muted small">Turn this on to see your LEDs' light on the model as you edit. Any change to a part, LED or diffuser starts it again.</p>

      <template v-else>
        <!-- one status line -->
        <div v-if="live.phase.value === 'error'" class="notice warn small"><AlertTriangle :size="14" /><span>{{ live.error.value }}</span></div>
        <div v-else class="live-status small">
          <div v-if="live.phase.value !== 'done'" class="spinner small" />
          <Check v-else :size="14" class="ok-icon" />
          <span class="live-status-text">{{ statusText }}</span>
          <button class="btn small" :disabled="live.phase.value !== 'done'" title="Save this result as a run in the Results tab" @click="live.keep()"><Save :size="14" /> Keep</button>
        </div>
        <div v-if="live.phase.value === 'running'" class="progress thin"><div class="progress-bar" :style="{ width: live.progress.value * 100 + '%' }" /></div>
        <p v-if="live.notice.value" class="muted small">{{ live.notice.value }}</p>

        <label v-if="live.states.value.length > 1" class="mini" title="Which LEDs are on. Switching state needs no new run.">
          <span>State</span>
          <select :value="live.shownState.value" @change="live.stateId.value = ($event.target as HTMLSelectElement).value">
            <option v-for="s in live.states.value" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
        </label>

        <div v-if="det" class="live-metrics">
          <div><span class="muted">{{ det.kind === "receiver" ? "Arriving" : "Efficiency" }}</span><strong>{{ fmtPct(det.efficiency) }}</strong></div>
          <div><span class="muted">Min / max</span><strong>{{ det.stats.minOverMax == null ? "–" : fmtPct(det.stats.minOverMax, 0) }}</strong></div>
          <div><span class="muted">{{ det.kind === "receiver" ? "On receiver" : "Exit" }}</span><strong>{{ det.exitLm.toFixed(2) }} lm</strong></div>
          <div v-if="det.color"><span class="muted">CCT</span><strong>{{ Math.round(det.color.cct) }} K</strong></div>
        </div>
        <p v-else-if="live.phase.value !== 'error'" class="muted small">Waiting for the first picture…</p>
        <NoiseBadge v-if="det" :noise="det.noise" />

        <!-- try the diffuser loading -->
        <div v-if="live.diffusers.value.length" class="live-slider">
          <label v-if="live.diffusers.value.length > 1" class="mini">
            <span>Diffuser</span>
            <select :value="live.partId.value" @change="live.selectPart(($event.target as HTMLSelectElement).value)">
              <option v-for="d in live.diffusers.value" :key="d.inst.id" :value="d.inst.id">{{ d.inst.name }} ({{ d.material }})</option>
            </select>
          </label>
          <div class="slider-row">
            <span class="small" :title="live.diffusers.value.length === 1 ? `${live.diffusers.value[0].inst.name} (${live.diffusers.value[0].material})` : ''">TiO₂</span>
            <input type="range" min="0" max="1" step="0.002" :value="sliderPos" @input="onSlide(($event.target as HTMLInputElement).valueAsNumber)" />
            <strong class="small">{{ fmtNum(live.shownTio2.value) }} %</strong>
          </div>
          <p v-if="live.tio2.value !== null" class="muted small">
            Trying {{ fmtNum(live.tio2.value) }} % (the material has {{ fmtNum(live.baseTio2.value) }} %).
            <a href="#" @click.prevent="live.setTio2(null)">Reset</a>
          </p>
        </div>
        <p v-else class="muted small">Give a part a diffusing material to try different TiO₂ amounts here.</p>

        <!-- quality -->
        <div class="live-quality">
          <label class="mini">
            <span>Rays</span>
            <select v-model.number="live.rays.value" title="How many rays to trace for each LED colour channel">
              <option v-for="r in LIVE_RAY_CHOICES" :key="r" :value="r">{{ r.toLocaleString() }}</option>
            </select>
          </label>
          <label class="mini" title="Stop early once the picture is this smooth">
            <span>Stop at noise</span>
            <select v-model.number="live.targetNoise.value">
              <option :value="0">Never</option>
              <option :value="0.1">10 %</option>
              <option :value="0.05">5 % (converged)</option>
              <option :value="0.03">3 %</option>
            </select>
          </label>
          <label v-if="live.gpu.value?.available" class="mini" title="The GPU traces the same physics much faster">
            <span>Solver</span>
            <select v-model="live.chosen.value">
              <option value="cpu">CPU</option>
              <option value="gpu">GPU ({{ live.gpu.value.name }})</option>
            </select>
          </label>
        </div>

        <hr />

        <!-- the photoreal picture -->
        <label class="tog" title="Show the model lit by the simulated light, rendered live on the GPU from the 3D view's camera">
          <input type="checkbox" :checked="view.on" @change="view.on = ($event.target as HTMLInputElement).checked" />
          <span class="tog-track" />
          <span class="tog-text">Photoreal view</span>
        </label>
        <template v-if="view.on">
          <label class="mini">
            <span>Light</span>
            <select v-model="view.mode">
              <option value="map">Exit surface only (smooth)</option>
              <option value="transport">Full light transport (grainy)</option>
            </select>
          </label>
          <div v-if="exitSurfaces.length > 1 && view.mode === 'map'" class="exit-pick" title="Which exit surfaces light the picture. The others stay dark.">
            <span class="small">Lit exit surfaces</span>
            <label v-for="s in exitSurfaces" :key="s.index" class="check small">
              <input type="checkbox" :checked="!view.hiddenExits.includes(s.index)" @change="setLit(s.index, ($event.target as HTMLInputElement).checked)" /> {{ s.name }}
            </label>
            <p v-if="litCount > 1" class="muted small">
              With more than one lit, light can be counted twice where one surface glows onto another. A receiver under a pass-through exit is handled, but other glowing surfaces may still light each other.
            </p>
          </div>
          <label class="mini" title="How the picture is exposed">
            <span>Exposure mode</span>
            <select v-model="view.exposureMode" :title="EXPOSURE_MODES.find((m) => m.id === view.exposureMode)?.hint">
              <option v-for="m in EXPOSURE_MODES" :key="m.id" :value="m.id">{{ m.label }}</option>
            </select>
          </label>
          <p class="muted small">{{ EXPOSURE_MODES.find((m) => m.id === view.exposureMode)?.hint }}</p>
          <div class="slider-row">
            <span class="small">Exposure</span>
            <input v-model.number="view.exposure" type="range" min="-6" max="6" step="0.1" />
            <strong class="small">{{ view.exposure > 0 ? "+" : "" }}{{ view.exposure.toFixed(1) }}</strong>
          </div>
          <div class="slider-row" title="The light of the room, in lux (illuminance on a surface facing the camera). A well-lit room is 300 to 500 lux, a bright desk 1,000 or more, 0 is a dark room.">
            <span class="small">Room light</span>
            <input :value="posFromLux(view.roomLux)" type="range" :min="0" :max="ROOM_LUX_STEPS" step="1" @input="view.roomLux = luxFromPos(Number(($event.target as HTMLInputElement).value))" />
            <strong class="small">{{ fmtLux(view.roomLux) }}</strong>
          </div>
          <div class="chip-row one-line" :style="{ '--chips': ROOM_LUX_PRESETS.length }" role="group" aria-label="Room light presets">
            <button v-for="p in ROOM_LUX_PRESETS" :key="p.label" type="button" class="chip-btn" :class="{ on: Math.abs(view.roomLux - p.lux) < Math.max(1, p.lux * 0.02) }" :title="p.hint" @click="view.roomLux = p.lux">{{ p.label }}</button>
          </div>
          <div v-if="view.lum" class="lum-readout small">
            <div v-if="view.lum.card > 0" title="Luminance of a white card facing the camera in the room light"><span>White card in the room</span> <strong>{{ fmtLum(view.lum.card) }} cd/m²</strong></div>
            <div v-for="s in view.lum.surfaces" :key="s.index" title="Mean and peak luminance of this surface in the picture">
              <span>{{ surfaceName(s.index) }}</span> <strong>{{ fmtLum(s.mean) }} cd/m²</strong> <span class="muted">peak {{ fmtLum(s.peak) }}</span>
            </div>
          </div>
          <p v-if="view.status?.state === 'live'" class="muted small">{{ view.status.variant.includes("cuda") ? "GPU" : "CPU" }} · {{ view.status.spp }} samples · {{ view.status.fps.toFixed(0) }} fps</p>
          <p v-else-if="view.status?.state === 'error'" class="error small">{{ view.status.message }}</p>
          <p v-else-if="view.status" class="muted small">{{ view.status.message }}</p>

          <a href="#" class="small" :aria-expanded="morePicture" @click.prevent="morePicture = !morePicture">{{ morePicture ? "▾" : "▸" }} More picture settings</a>
          <template v-if="morePicture">
            <label class="mini" title="Size of the picture in pixels (long side). A smaller picture refines faster.">
              <span>Resolution</span>
              <select v-model.number="view.resolution">
                <option v-for="r in RESOLUTIONS" :key="r.px" :value="r.px">{{ r.label }}</option>
              </select>
            </label>
            <label class="check small" title="Divide out the colour of the light so a warm or cool white looks neutral"><input v-model="view.whiteBalance" type="checkbox" /> Neutralise the colour of the light</label>
            <p class="muted small">Cuts and hidden parts are not shown in this picture. Orbit as usual.</p>
          </template>
        </template>
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { AlertTriangle, Check, ChevronDown, ChevronUp, Save, X, Zap } from "lucide-vue-next";
import { LIVE_RAY_CHOICES, TIO2_RANGE, type LivePreview } from "../composables/useLivePreview";
import { useWorkspace } from "../composables/useWorkspace";
import { fmtPct } from "../utils/colormap";
import { EXPOSURE_MODES, ROOM_LUX_PRESETS, ROOM_LUX_STEPS, fmtLum, fmtLux, luxFromPos, posFromLux, type ExposureMode } from "../utils/roomLux";
import type { LiveLuminance } from "../api";
import NoiseBadge from "./NoiseBadge.vue";

export interface PhotorealView {
  on: boolean;
  exposure: number;
  /** Illuminance of the room light on a surface facing the camera, in lux (0: a dark room). */
  roomLux: number;
  /** How the picture is exposed: a fixed camera, for the room light or from the lit surfaces. */
  exposureMode: ExposureMode;
  whiteBalance: boolean;
  mode: "map" | "transport";
  /** Long side of the picture in pixels; 0 follows the size of the 3D view. */
  resolution: number;
  /** Exit surfaces (indices in the setup) that stay dark in the picture; empty lights all of them. */
  hiddenExits: number[];
  status: { state: "starting" | "waiting" | "live" | "error"; message: string; spp: number; ms: number; variant: string; fps: number } | null;
  /** The luminances (cd/m2) behind the last picture. */
  lum: LiveLuminance | null;
}

const props = defineProps<{ live: LivePreview; view: PhotorealView }>();
defineEmits<{ close: [] }>();

const collapsed = ref(false);
const ws = useWorkspace();
/** The exit surfaces (and receivers) of the setup, by their index: that is how the picture refers to them. */
const exitSurfaces = computed(() => (ws.setup.value?.detectors ?? []).map((d, index) => ({ index, name: d.name || `Exit surface ${index + 1}` })));
const surfaceName = (index: number) => exitSurfaces.value.find((s) => s.index === index)?.name ?? `Surface ${index + 1}`;
const litCount = computed(() => exitSurfaces.value.filter((s) => !props.view.hiddenExits.includes(s.index)).length);
function setLit(index: number, lit: boolean) {
  const rest = props.view.hiddenExits.filter((i) => i !== index);
  props.view.hiddenExits = lit ? rest : [...rest, index].sort((a, b) => a - b);
}
const morePicture = ref(false);
const RESOLUTIONS = [
  { px: 0, label: "Fit the view" },
  { px: 640, label: "640 px" },
  { px: 960, label: "960 px" },
  { px: 1280, label: "1280 px" },
  { px: 1920, label: "1920 px" },
  { px: 2560, label: "2560 px" },
  { px: 3200, label: "3200 px" },
  { px: 4096, label: "4096 px (the most the server draws)" },
];

const det = computed(() => props.live.detector.value);
/** The one line that says where the simulation is. */
const statusText = computed(() => {
  const live = props.live;
  if (live.phase.value === "starting") return "Starting…";
  if (live.phase.value === "running") return `Tracing ${(live.run.value?.live?.rays ?? 0).toLocaleString()} of ${(live.run.value?.live?.target ?? live.rays.value).toLocaleString()} rays`;
  return `Done · ${(live.run.value?.live?.rays ?? live.rays.value).toLocaleString()} rays`;
});
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
