<template>
  <section class="res-card">
    <div class="hm-title"><span>Picture of the {{ detectors?.[form.detector]?.name ?? "exit surface" }} (Mitsuba 3)</span></div>
    <p v-if="st && !st.available" class="muted small">{{ st.reason }}</p>
    <template v-else>
      <div class="render-form">
        <label class="mini">
          <span>Method</span>
          <select v-model="form.mode">
            <option value="map">From this simulation (fast, smooth)</option>
            <option value="transport">Full light transport in Mitsuba (slow, noisy)</option>
          </select>
        </label>
        <label class="mini narrow"><span>Width</span>
          <select v-model.number="form.width"><option v-for="w in [320, 640, 960, 1280, 1920, 2560, 3840]" :key="w" :value="w">{{ w }} px</option></select>
        </label>
        <label class="mini narrow"><span>Samples</span>
          <select v-model.number="form.spp"><option v-for="s in [8, 32, 128, 512, 2048, 8192, 16384, 32768]" :key="s" :value="s">{{ s }}</option></select>
        </label>
        <label v-if="(detectors?.length ?? 0) > 1" class="mini" title="The surface the camera looks at">
          <span>Look at</span>
          <select v-model.number="form.detector"><option v-for="(d, i) in detectors" :key="i" :value="i">{{ d.name }}</option></select>
        </label>
        <div v-if="form.mode === 'map' && (detectors?.length ?? 0) > 1" class="exit-pick" title="Which exit surfaces glow in the picture. The others stay dark, and the brightness is set by the ones shown.">
          <span class="small">Lit exit surfaces</span>
          <label v-for="(d, i) in detectors" :key="i" class="check small">
            <input type="checkbox" :checked="!form.hidden_exits.includes(i)" :disabled="!form.hidden_exits.includes(i) && (detectors?.length ?? 0) - form.hidden_exits.length <= 1" @change="toggleExit(i, ($event.target as HTMLInputElement).checked)" />
            {{ d.name }}
          </label>
        </div>
        <label class="mini narrow"><span>Turn {{ form.azimuth }}°</span><input v-model.number="form.azimuth" type="range" min="-70" max="70" step="5" /></label>
        <label class="mini narrow"><span>Tilt {{ form.elevation }}°</span><input v-model.number="form.elevation" type="range" min="-70" max="70" step="5" /></label>
        <label class="mini narrow"><span>Zoom {{ form.zoom.toFixed(1) }}×</span><input v-model.number="form.zoom" type="range" min="0.5" max="4" step="0.1" /></label>
        <label class="mini narrow"><span>Exposure {{ form.exposure }} EV</span><input v-model.number="form.exposure" type="range" min="-6" max="6" step="0.5" /></label>
        <label class="mini narrow" title="The light of the room on the housing, in lux (illuminance on a surface facing the camera). The exposure is fixed like a camera that is not changed between shots, so a dim room looks dim and a bright one burns out."><span>Room light {{ fmtLux(form.room_lux) }}</span><input :value="posFromLux(form.room_lux)" type="range" :min="0" :max="ROOM_LUX_STEPS" step="1" @input="form.room_lux = luxFromPos(Number(($event.target as HTMLInputElement).value))" /></label>
        <div class="chip-row one-line" :style="{ '--chips': ROOM_LUX_PRESETS.length }" role="group" aria-label="Room light presets">
          <button v-for="p in ROOM_LUX_PRESETS" :key="p.label" type="button" class="chip-btn" :class="{ on: Math.abs(form.room_lux - p.lux) < Math.max(1, p.lux * 0.02) }" :title="p.hint" @click="form.room_lux = p.lux">{{ p.label }}</button>
        </div>
        <label class="mini narrow" title="How the picture is exposed"><span>Exposure</span>
          <select v-model="form.exposure_mode" :title="EXPOSURE_MODES.find((m) => m.id === form.exposure_mode)?.hint">
            <option v-for="m in EXPOSURE_MODES" :key="m.id" :value="m.id">{{ m.label }}</option>
          </select>
        </label>
        <label class="check small"><input v-model="form.white_balance" type="checkbox" /> neutral white balance</label>
        <button v-if="!busy" class="btn primary small" @click="start"><Camera :size="14" /> Render picture</button>
        <button v-else class="btn small" @click="cancel"><X :size="14" /> Cancel</button>
      </div>
      <div v-if="busy" class="progress thin"><div class="progress-bar" :style="{ width: Math.round((st?.render?.progress.fraction ?? 0) * 100) + '%' }" /></div>
      <p v-if="busy" class="muted small">{{ st?.render?.progress.phase || "Queued" }}</p>
      <p v-if="error || st?.render?.status === 'failed'" class="error small">{{ error || st?.render?.error }}</p>
      <img v-if="st?.render?.hasImage && !busy" :src="api.renderUrl(runId, st.render.finished ?? 0)" class="render-img" alt="Rendered picture of the exit surface" />
      <div v-for="w in st?.render?.info?.warnings ?? []" :key="w" class="notice info"><Info :size="15" /><span>{{ w }}</span></div>
      <p class="muted small">
        The picture follows the camera projection chosen in the toolbar ({{ projection === "orthographic" ? "orthographic" : "perspective" }}).
        The picture shows how the lit surfaces look to a camera in the room light set above (0 lux is a dark room), exposed as chosen under Exposure (a fixed camera, for the room light, or from the lit surfaces), for the state chosen at the top. The numbers on this page always come from the simulation.
        {{ form.mode === "transport" ? "Full transport finds the small LEDs only by chance through the glass, so it needs thousands of samples to look clean." : "" }}
      </p>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { Camera, Info, X } from "lucide-vue-next";
import { api, type RenderState } from "../api";
import { DEFAULT_ROOM_LUX, EXPOSURE_MODES, ROOM_LUX_PRESETS, ROOM_LUX_STEPS, fmtLux, luxFromPos, posFromLux, type ExposureMode } from "../utils/roomLux";

const props = defineProps<{ runId: string; state?: string; detectors?: { name: string }[]; detector?: number; projection?: "perspective" | "orthographic" }>();
const st = ref<RenderState | null>(null);
const error = ref("");
const form = reactive({ mode: "map" as "map" | "transport", width: 640, spp: 32, azimuth: 0, elevation: 0, detector: props.detector ?? 0, zoom: 1, exposure: 0, room_lux: DEFAULT_ROOM_LUX, exposure_mode: "fixed" as ExposureMode, white_balance: true, hidden_exits: [] as number[] });
function toggleExit(index: number, lit: boolean) {
  const rest = form.hidden_exits.filter((i) => i !== index);
  form.hidden_exits = lit ? rest : [...rest, index].sort((a, b) => a - b);
}
let timer: number | undefined;

const busy = computed(() => st.value?.render?.status === "queued" || st.value?.render?.status === "running");

async function refresh() {
  try {
    st.value = await api.getRender(props.runId);
    error.value = "";
  } catch (e) {
    error.value = (e as Error).message;
  }
  window.clearTimeout(timer);
  if (busy.value) timer = window.setTimeout(refresh, 800);
}
async function start() {
  error.value = "";
  try {
    st.value = await api.createRender(props.runId, { ...form, state: props.state, projection: props.projection ?? "perspective" });
    window.clearTimeout(timer);
    timer = window.setTimeout(refresh, 600);
  } catch (e) {
    error.value = (e as Error).message;
  }
}
async function cancel() {
  await api.cancelRender(props.runId);
  await refresh();
}
watch(() => props.runId, refresh, { immediate: true });
watch(() => props.detector, (d) => { if (d !== undefined) form.detector = d; }); // follows the surface chosen in the results
onBeforeUnmount(() => window.clearTimeout(timer));
</script>
